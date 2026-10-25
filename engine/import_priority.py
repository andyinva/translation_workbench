#!/usr/bin/env python3
"""
import_priority.py  (place this file in translation_workbench/engine/)

Builds the word priority list: every Hebrew and Aramaic lemma of the Old
Testament, with several separate measures of how influential it is, and two
orderings (most influential first, least influential first).

The measures (each is stored on its own, never only as one blended number):

    uses        how many times the lemma occurs in the Old Testament
    verses      in how many different verses it occurs
    crossrefs   cross references (OpenBible) that touch the verses holding it
    shadow      Word Atlas: how much influence the word casts over nearby words
    depth       Word Atlas: how strongly the word is concentrated in one place
    pull        Word Atlas: the average pull of the word's five strongest companions
    motifs      how many Scripture Motifs files name the word
    difficulty  a first flag for rare words (hapax = once, rare = two or three uses)

The combined score is only a starting point. It is the weighted average of the
percentile ranks of uses, crossrefs, shadow and pull, plus a small share for
motifs. The weights are printed and stored, and can be changed with --weights.

The Word Atlas and Scripture Motifs numbers are joined to our lemmas through the
Strong's number. The join is approximate: Word Atlas counts the Hebrew words
behind the King James text, so where the King James merges or splits a Hebrew
word the numbers are rough. Lemmas with no Strong's number (the prefixes "the",
"and", "in" and so on) get no score. They need rules in style_rules.md, not a rank.

This script only READS the source, Word Atlas, cross reference and motif files.
It writes two tables in the project database (word_priority and
word_priority_info). Running it again replaces both tables.

Usage (from the engine folder):
    python3 import_priority.py build
    python3 import_priority.py show --top 40
    python3 import_priority.py show --bottom 40
    python3 import_priority.py show --top 40 --todo        (only words not yet in the glossary)
    python3 import_priority.py show --strongs H4375
    python3 import_priority.py show --lemma-id 4505
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

from books import book_number

# Where things normally live. Every path can be changed on the command line.
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROJECT = os.path.join(HERE, "..", "projects", "english_literal", "project.db")
DEFAULT_SOURCE = os.path.join(HERE, "..", "sources", "source_texts.db")
DEFAULT_ATLAS = os.path.expanduser("~/projects/word_atlas/atlas.db")
DEFAULT_XREFS = os.path.expanduser("~/projects/word_atlas/cross_references.db")
DEFAULT_MOTIFS = os.path.expanduser("~/projects/scripture-motifs/motifs")

# Starting weights for the combined score.
DEFAULT_WEIGHTS = {"uses": 1.0, "crossrefs": 1.0, "shadow": 1.0, "pull": 1.0, "motifs": 0.25}

# Old Testament books are 1 to 39 in the project's numbering.
LAST_OT_BOOK = 39

# A cross reference to a very long range is not a reference to each verse.
MAX_RANGE = 30


def strongs_key(text):
    """Turn 'H0430', 'H430' or 'H430a' into 'H430'. Return None when there is no number."""
    match = re.match(r"\s*H0*(\d+)", text or "")
    return "H" + match.group(1) if match else None


def read_only(path):
    """Open a SQLite file so that nothing can be changed in it."""
    if not os.path.exists(path):
        sys.exit(f"File not found: {path}")
    return sqlite3.connect("file:" + os.path.abspath(path) + "?mode=ro", uri=True)


def percentile_ranks(values):
    """Map each value to its percentile (0 to 1), ties sharing the average rank."""
    ordered = sorted(values)
    count = len(ordered)
    first, last = {}, {}
    for index, value in enumerate(ordered):
        first.setdefault(value, index)
        last[value] = index
    return {v: ((first[v] + last[v]) / 2) / max(count - 1, 1) for v in first}


class PriorityBuilder:
    """Reads the five sources and writes the word_priority tables."""

    def __init__(self, project, source, atlas, xrefs, motifs, weights):
        self.project_path = project
        self.source_path = source
        self.atlas_path = atlas
        self.xrefs_path = xrefs
        self.motifs_dir = motifs
        self.weights = weights

    # ---- step 1: the Old Testament words, from the source database ----

    def load_uses(self):
        """Return {lemma_id: (uses, set of verses)} and {lemma_id: strongs key}."""
        source = read_only(self.source_path)
        strongs = {}
        for lemma_id, number in source.execute("SELECT lemma_id, strongs FROM lemmas"):
            strongs[lemma_id] = strongs_key(number)
        uses = defaultdict(int)
        verses = defaultdict(set)
        rows = source.execute(
            "SELECT lemma_id, book, chapter, verse FROM words "
            "WHERE lemma_id IS NOT NULL AND book <= ?", (LAST_OT_BOOK,))
        for lemma_id, book, chapter, verse in rows:
            uses[lemma_id] += 1
            verses[lemma_id].add((book, chapter, verse))
        source.close()
        return uses, verses, strongs

    # ---- step 2: cross references per verse ----

    def load_crossrefs(self):
        """Return {(book, chapter, verse): number of cross references touching that verse}.

        Only references with a positive relevance score are counted (a negative
        score means readers voted the link down). Both ends of a reference count.
        """
        xrefs = read_only(self.xrefs_path)
        per_verse = defaultdict(int)
        skipped = 0
        rows = xrefs.execute(
            "SELECT from_book, from_chapter, from_verse_start, from_verse_end, "
            "to_book, to_chapter, to_verse_start, to_verse_end "
            "FROM cross_references WHERE relevance_score > 0")
        for fb, fc, fs, fe, tb, tc, ts, te in rows:
            for book_name, chapter, start, end in ((fb, fc, fs, fe), (tb, tc, ts, te)):
                try:
                    book = book_number(book_name)
                except Exception:
                    skipped += 1
                    continue
                if book is None or book > LAST_OT_BOOK:
                    continue
                end = end if end and end >= start else start
                if end - start >= MAX_RANGE:
                    end = start + MAX_RANGE - 1
                for verse in range(start, end + 1):
                    per_verse[(book, chapter, verse)] += 1
        xrefs.close()
        self.skipped_xref_ends = skipped
        return per_verse

    # ---- step 3: Word Atlas numbers ----

    def load_atlas(self, wanted):
        """Return {strongs key: (weight, shadow, depth, pull)} for the Strong's numbers in `wanted`."""
        atlas = read_only(self.atlas_path)
        found = {}
        for root, weight, shadow, depth in atlas.execute(
                "SELECT root, weight, shadow, depth FROM words WHERE root LIKE 'H%'"):
            if root in wanted:
                found[root] = [weight, shadow, depth, None]
        # Pull: the average of the five strongest companions across the whole Bible.
        for root in found:
            pulls = [row[0] for row in atlas.execute(
                "SELECT pull FROM pairs WHERE scope = 'Bible' AND focus = ? "
                "ORDER BY pull DESC LIMIT 5", (root,)) if row[0] is not None]
            if pulls:
                found[root][3] = sum(pulls) / len(pulls)
        atlas.close()
        return {k: tuple(v) for k, v in found.items()}

    # ---- step 4: motifs ----

    def load_motifs(self):
        """Return {strongs key: sorted list of motif keys that name it}."""
        named = defaultdict(set)
        for path in sorted(glob.glob(os.path.join(self.motifs_dir, "*.json"))):
            if os.path.basename(path) == "groups.json":
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    motif = json.load(handle)
            except (OSError, ValueError):
                continue
            key = motif.get("key", os.path.basename(path)[:-5])
            for term in motif.get("hebrew", []):
                # A term is a single lemma or a short sequence of lemmas.
                for field in (term.get("lemma", ""), term.get("sequence", "")):
                    for token in field.split():
                        number = strongs_key(token)
                        if number:
                            named[number].add(key)
        return {k: sorted(v) for k, v in named.items()}

    # ---- step 5: put it together ----

    @staticmethod
    def difficulty_flag(uses):
        """A first, simple flag. A lexical session can refine it later."""
        if uses == 1:
            return "hapax"
        if uses <= 3:
            return "rare"
        return ""

    def combine(self, rows):
        """Add the combined score and the two rank columns to the rows (in place)."""
        scored = [r for r in rows if r["strongs"] and r["in_atlas"]]
        ranks = {}
        for measure in ("uses", "crossrefs", "shadow", "pull"):
            values = [r[measure] if r[measure] is not None else 0 for r in scored]
            ranks[measure] = percentile_ranks(values) if values else {}
        total_weight = sum(self.weights.values()) or 1.0
        for r in scored:
            score = 0.0
            for measure in ("uses", "crossrefs", "shadow", "pull"):
                value = r[measure] if r[measure] is not None else 0
                score += self.weights.get(measure, 0) * ranks[measure][value]
            # Motifs count from 0 (none) to 1 (three or more).
            score += self.weights.get("motifs", 0) * min(r["motif_count"], 3) / 3.0
            r["score"] = score / total_weight
        order = sorted(scored, key=lambda r: (-r["score"], -r["uses"], r["lemma_id"]))
        for index, r in enumerate(order, start=1):
            r["rank_most"] = index
            r["rank_least"] = len(order) - index + 1
        return len(order)

    def build(self):
        """Run all steps and write the tables."""
        print("Reading the Old Testament words ...")
        uses, verses, strongs = self.load_uses()
        print(f"  {len(uses)} lemmas used in the Old Testament.")
        print("Reading the cross references ...")
        per_verse = self.load_crossrefs()
        print(f"  {len(per_verse)} Old Testament verses have cross references.")
        wanted = {strongs[lid] for lid in uses if strongs.get(lid)}
        print("Reading Word Atlas ...")
        atlas = self.load_atlas(wanted)
        print(f"  {len(atlas)} of {len(wanted)} Strong's numbers found in Word Atlas.")
        print("Reading the motif files ...")
        motifs = self.load_motifs()
        print(f"  {len(motifs)} Strong's numbers are named in motifs.")

        rows = []
        for lemma_id in sorted(uses):
            number = strongs.get(lemma_id)
            weight, shadow, depth, pull = atlas.get(number, (None, None, None, None))
            refs = sum(per_verse.get(v, 0) for v in verses[lemma_id])
            keys = motifs.get(number, []) if number else []
            rows.append({
                "lemma_id": lemma_id, "strongs": number,
                "uses": uses[lemma_id], "verses": len(verses[lemma_id]),
                "crossrefs": refs, "atlas_weight": weight, "shadow": shadow,
                "depth": depth, "pull": pull,
                "motif_count": len(keys), "motif_keys": "; ".join(keys),
                "difficulty": self.difficulty_flag(uses[lemma_id]),
                "in_atlas": 1 if number in atlas else 0,
                "score": None, "rank_most": None, "rank_least": None,
            })
        ranked = self.combine(rows)
        self.write(rows, ranked)
        return rows, ranked

    def write(self, rows, ranked):
        """Replace the word_priority tables in the project database."""
        project = sqlite3.connect(self.project_path)
        with project:
            project.execute("DROP TABLE IF EXISTS word_priority")
            project.execute("DROP TABLE IF EXISTS word_priority_info")
            project.execute("""
                CREATE TABLE word_priority (
                    lemma_id     INTEGER PRIMARY KEY,   -- lemma_id in the source database
                    strongs      TEXT,                  -- Strong's key such as H430 (empty for prefixes)
                    uses         INTEGER NOT NULL,      -- occurrences in the Old Testament
                    verses       INTEGER NOT NULL,      -- distinct verses holding it
                    crossrefs    INTEGER NOT NULL,      -- cross references touching those verses
                    atlas_weight INTEGER,               -- Word Atlas: count behind the KJV
                    shadow       REAL,                  -- Word Atlas: shadow
                    depth        REAL,                  -- Word Atlas: depth
                    pull         REAL,                  -- Word Atlas: mean of the top five pulls
                    motif_count  INTEGER NOT NULL,      -- motif files naming the word
                    motif_keys   TEXT,                  -- their keys, separated by semicolons
                    difficulty   TEXT,                  -- hapax, rare or empty
                    in_atlas     INTEGER NOT NULL,      -- 1 if Word Atlas had the Strong's number
                    score        REAL,                  -- combined score, 0 to 1 (empty when unranked)
                    rank_most    INTEGER,               -- 1 = most influential
                    rank_least   INTEGER                -- 1 = least influential
                )""")
            project.execute("CREATE TABLE word_priority_info (key TEXT PRIMARY KEY, value TEXT)")
            project.executemany(
                "INSERT INTO word_priority VALUES (:lemma_id, :strongs, :uses, :verses, :crossrefs, "
                ":atlas_weight, :shadow, :depth, :pull, :motif_count, :motif_keys, :difficulty, "
                ":in_atlas, :score, :rank_most, :rank_least)", rows)
            info = {
                "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "weights": json.dumps(self.weights),
                "ranked_lemmas": str(ranked),
                "lemmas_total": str(len(rows)),
                "word_atlas_file": os.path.abspath(self.atlas_path),
                "word_atlas_stamp": self._file_stamp(self.atlas_path),
                "cross_references": "OpenBible.info, CC BY, only references with a positive score",
                "join_note": ("Word Atlas and the motifs are joined by Strong's number. Atlas counts "
                              "come from the Hebrew words behind the King James text, so the join is approximate. "
                              "Verse numbers of cross references are King James numbers."),
                "unranked_note": "Lemmas without a Strong's number (prefixes) or not in Word Atlas have no score.",
            }
            project.executemany("INSERT INTO word_priority_info VALUES (?, ?)", list(info.items()))
        project.close()

    @staticmethod
    def _file_stamp(path):
        try:
            return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return ""


class PriorityViewer:
    """Prints parts of the word_priority table."""

    def __init__(self, project, source):
        self.project = sqlite3.connect(project)
        self.project.row_factory = sqlite3.Row
        # The source database is attached read only to show the Hebrew and the transliteration.
        self.project.execute("ATTACH DATABASE ? AS src", ("file:" + os.path.abspath(source) + "?mode=ro",))

    def _rows(self, where="", params=(), order="p.rank_most", limit=40):
        sql = f"""
            SELECT p.*, l.headword, l.transliteration, l.gloss_hint,
                   (SELECT COUNT(*) FROM glossary g
                     WHERE g.lemma_id = p.lemma_id AND g.status IN ('approved', 'revised')) AS in_glossary
            FROM word_priority p
            JOIN src.lemmas l ON l.lemma_id = p.lemma_id
            {where}
            ORDER BY {order}
            LIMIT {int(limit)}"""
        return self.project.execute(sql, params).fetchall()

    @staticmethod
    def _line(r):
        rank = r["rank_most"] if r["rank_most"] else "-"
        shadow = f"{r['shadow']:.0f}" if r["shadow"] is not None else "-"
        pull = f"{r['pull']:.1f}" if r["pull"] is not None else "-"
        return (f"{rank:>5}  {r['lemma_id']:>5}  {(r['strongs'] or '-'):<7} {r['headword'] or '':<12} "
                f"{(r['transliteration'] or ''):<14} {r['uses']:>6} {r['crossrefs']:>7} {shadow:>7} {pull:>6} "
                f"{r['motif_count']:>3}  {r['difficulty'] or '':<5} {'yes' if r['in_glossary'] else 'no':<3} "
                f"{(r['gloss_hint'] or '')[:24]}")

    HEADER = ("rank  lemma  strongs headword     translit       uses  crossrefs  shadow   pull  mot  diff  gl  gloss\n"
              + "-" * 112)

    def show(self, top=None, bottom=None, todo=False, strongs=None, lemma_id=None):
        """Print a list of rows."""
        where, params, order, limit = "WHERE p.rank_most IS NOT NULL", (), "p.rank_most", 40
        if todo:
            where += (" AND NOT EXISTS (SELECT 1 FROM glossary g WHERE g.lemma_id = p.lemma_id "
                      "AND g.status IN ('approved', 'revised'))")
        if bottom:
            order, limit = "p.rank_least", bottom
        elif top:
            limit = top
        if strongs:
            where, params, limit = "WHERE p.strongs = ?", (strongs_key(strongs),), 20
        if lemma_id:
            where, params, limit = "WHERE p.lemma_id = ?", (lemma_id,), 1
        rows = self._rows(where, params, order, limit)
        print(self.HEADER)
        for r in rows:
            print(self._line(r))
        if not rows:
            print("(no rows)")
        info = dict(self.project.execute("SELECT key, value FROM word_priority_info").fetchall())
        print(f"\nBuilt {info.get('built_at', '?')} from Word Atlas of {info.get('word_atlas_stamp', '?')}; "
              f"{info.get('ranked_lemmas', '?')} of {info.get('lemmas_total', '?')} lemmas ranked. "
              f"Weights: {info.get('weights', '?')}")


def parse_weights(text):
    """Turn 'uses=2,pull=0' into a dictionary that starts from the default weights."""
    weights = dict(DEFAULT_WEIGHTS)
    if text:
        for part in text.split(","):
            name, _, value = part.partition("=")
            if name.strip() not in weights:
                sys.exit(f"Unknown weight '{name}'. Use: {', '.join(weights)}")
            weights[name.strip()] = float(value)
    return weights


def main():
    parser = argparse.ArgumentParser(description="Build and show the word priority list")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="read the sources and write the word_priority tables")
    build.add_argument("--project", default=DEFAULT_PROJECT)
    build.add_argument("--source", default=DEFAULT_SOURCE)
    build.add_argument("--atlas", default=DEFAULT_ATLAS, help="path to Word Atlas atlas.db")
    build.add_argument("--xrefs", default=DEFAULT_XREFS, help="path to cross_references.db")
    build.add_argument("--motifs", default=DEFAULT_MOTIFS, help="the Scripture Motifs motifs folder")
    build.add_argument("--weights", help="for example uses=2,pull=0.5")

    show = sub.add_parser("show", help="print the list")
    show.add_argument("--project", default=DEFAULT_PROJECT)
    show.add_argument("--source", default=DEFAULT_SOURCE)
    show.add_argument("--top", type=int, help="most influential first")
    show.add_argument("--bottom", type=int, help="least influential first")
    show.add_argument("--todo", action="store_true", help="only words with no approved glossary entry")
    show.add_argument("--strongs", help="one Strong's number such as H4375")
    show.add_argument("--lemma-id", type=int, help="one lemma_id")

    args = parser.parse_args()
    if args.command == "build":
        builder = PriorityBuilder(args.project, args.source, args.atlas, args.xrefs, args.motifs,
                                  parse_weights(args.weights))
        rows, ranked = builder.build()
        print(f"\nDone. {ranked} lemmas ranked, {len(rows) - ranked} without a rank "
              f"(prefixes and words Word Atlas does not have).")
        print("Next: python3 import_priority.py show --top 40")
    else:
        viewer = PriorityViewer(args.project, args.source)
        viewer.show(args.top, args.bottom, args.todo, args.strongs, args.lemma_id)


if __name__ == "__main__":
    main()
