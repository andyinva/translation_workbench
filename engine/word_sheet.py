#!/usr/bin/env python3
"""
word_sheet.py  (place this file in translation_workbench/engine/)

Builds a "word sheet" for one original-language word (a lemma): every place the
word is used in the Bible, with the verse, the form used, how the KJV renders it
there, and how other English translations render the whole verse. A translation
session reads the sheet before deciding how to render the word, so the word is
handled the same way everywhere.

A sheet contains:
    1. The lemma rows found (Hebrew or Greek), with headword, Strong's number,
       and the lexicon entry (Strong's text and the start of the BDB entry).
    2. A tally of how the KJV renders the word, counted over EVERY use.
    3. The approved glossary entry from the project database, if there is one.
    4. A list of uses: reference, source verse with the word marked, the KJV
       word(s) for it, and the verse text from the chosen translations.

Databases used (all opened read-only, nothing is ever changed):
    source db    sources/source_texts.db      (required)
    bibles db    Bible Search Lite bibles.db  (required for KJV and translations)
    project db   projects/<name>/project.db   (optional, for glossary entries)

The other translations stay in bibles.db. A sheet that includes a translation
still under copyright (see COPYRIGHTED) is for local use only. Do not commit it.

Usage examples:
    python3 word_sheet.py --source ../sources/source_texts.db --bibles BIBLES.db --strongs G26
    python3 word_sheet.py --source ... --bibles ... --strongs H1254 --translations YLT ASV
    python3 word_sheet.py --source ... --bibles ... --headword ἀγάπη --limit 50 --output sheet.md
    python3 word_sheet.py --source ... --bibles ... --lemma-id 1234 --project ../projects/english_literal/project.db
"""

import argparse
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# books.py sits next to this file, whether run as a script or as part of a package.
try:
    from books import book_name
except ImportError:
    from engine.books import book_name

# Translations that are still under copyright. Output that includes them is local only.
COPYRIGHTED = {"JUB", "LEB", "NET", "LIT", "MKJ", "BBE"}

# Translations shown when none are asked for: all in the public domain, and literal in style.
DEFAULT_TRANSLATIONS = ["KJV", "ASV", "YLT"]

BDB_PREVIEW_CHARS = 700     # how much of the BDB entry to show in the sheet


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def normalize_strongs(code):
    """Return a Strong's number as a letter plus a plain number, or None.

    TAGNT writes G0976 or G2424G, OSHB-derived rows write H1254, and bibles.db
    writes G976 or H1254. This makes them comparable: "G0976" and "G976" both
    become "G976", and a trailing disambiguation letter is dropped.
    """
    match = re.match(r"^([HGhg])0*(\d+)", (code or "").strip())
    if not match:
        return None
    return match.group(1).upper() + str(int(match.group(2)))


def open_read_only(path):
    """Open a SQLite file so it cannot be changed. Works on Linux and Windows."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        sys.exit(f"Database not found: {path}")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def clean_word(text):
    """Lower-case a KJV word and strip punctuation, so counting is not fooled by commas."""
    return re.sub(r"[^\w' -]", "", text or "").strip().lower()


# ----------------------------------------------------------------------
# The sheet itself: plain data, easy to show in text now and in a window later
# ----------------------------------------------------------------------
@dataclass
class Occurrence:
    """One use of the word."""
    word_id: int
    book: int
    chapter: int
    verse: int
    surface: str                # the form used here
    morph: str
    word_type: str              # Greek editions code, empty for Hebrew
    context: str = ""           # the source verse with this word marked
    english_refs: list = field(default_factory=list)    # [(chapter, verse)] in English numbering
    kjv_words: list = field(default_factory=list)       # KJV words tagged with this Strong's number
    translations: dict = field(default_factory=dict)    # abbreviation -> verse text
    uses_in_verse: int = 1      # how many uses of the word this verse holds

    @property
    def reference(self):
        return f"{book_name(self.book)} {self.chapter}:{self.verse}"


@dataclass
class WordSheet:
    """Everything known about one word, ready to be shown."""
    lemmas: list                                    # dict rows from the lemmas table
    strongs_text: str = ""                          # Strong's dictionary entry, if any
    bdb_text: str = ""                              # start of the BDB entry, if any
    total_uses: int = 0
    kjv_tally: Counter = field(default_factory=Counter)
    kjv_covered: int = 0                            # uses whose verse was found in bibles.db
    title_uses: int = 0                             # uses inside a Psalm title (no English verse exists)
    glossary: list = field(default_factory=list)    # dict rows from the project glossary
    occurrences: list = field(default_factory=list)  # the uses that are listed (may be limited)
    translations: list = field(default_factory=list)
    greek_text: str = "nestle"


# ----------------------------------------------------------------------
# Readers, one per database
# ----------------------------------------------------------------------
class SourceReader:
    """Reads lemmas, words, lexicon entries and the verse map from the source database."""

    def __init__(self, path):
        self.conn = open_read_only(path)
        self.conn.row_factory = sqlite3.Row

    def find_lemmas(self, strongs=None, lemma_id=None, headword=None):
        """Find the lemma rows asked for. A Strong's number can match several rows."""
        if lemma_id is not None:
            rows = self.conn.execute("SELECT * FROM lemmas WHERE lemma_id = ?", (lemma_id,))
            return [dict(r) for r in rows]
        if headword:
            rows = self.conn.execute("SELECT * FROM lemmas WHERE headword = ?", (headword,))
            return [dict(r) for r in rows]
        wanted = normalize_strongs(strongs)
        if wanted is None:
            sys.exit(f"Cannot read the Strong's number {strongs!r}. Use a form like G26 or H1254.")
        # Compare in Python, because the stored numbers are padded in different ways.
        rows = self.conn.execute("SELECT * FROM lemmas WHERE strongs LIKE ?", (wanted[0] + "%",))
        return [dict(r) for r in rows if normalize_strongs(r["strongs"]) == wanted]

    def lexicon(self, lemmas):
        """Return (Strong's text, BDB text) for the first lemma that has them."""
        strongs_text = bdb_text = ""
        for lemma in lemmas:
            wanted = normalize_strongs(lemma["strongs"])
            if wanted and not strongs_text:
                row = self.conn.execute(
                    "SELECT meaning, usage, source_text FROM strongs_entries WHERE strongs = ?",
                    (wanted,)).fetchone()
                if row:
                    parts = [row["source_text"], row["meaning"],
                             ("KJV usage: " + row["usage"]) if row["usage"] else None]
                    strongs_text = " ".join(p for p in parts if p)
            if lemma.get("lex_key") and not bdb_text:
                row = self.conn.execute(
                    "SELECT b.content FROM lexicon_entries e JOIN bdb_entries b ON b.bdb_id = e.bdb_id "
                    "WHERE e.lex_key = ?", (lemma["lex_key"],)).fetchone()
                if row and row["content"]:
                    text = row["content"]
                    bdb_text = text[:BDB_PREVIEW_CHARS] + ("..." if len(text) > BDB_PREVIEW_CHARS else "")
        return strongs_text, bdb_text

    def occurrences(self, lemma_ids, greek_text):
        """Every use of these lemmas, in Bible order."""
        marks = ",".join("?" * len(lemma_ids))
        sql = (f"SELECT w.word_id, w.text_id, w.book, w.chapter, w.verse, w.position, w.word_number, "
               f"w.surface, w.morph, w.word_type, t.language AS text_language "
               f"FROM words w JOIN texts t ON t.text_id = w.text_id "
               f"WHERE w.lemma_id IN ({marks}) ")
        if greek_text == "nestle":      # words of the Nestle-Aland text only
            sql += "AND (t.language <> 'greek' OR w.word_type LIKE 'N%') "
        sql += "ORDER BY w.book, w.chapter, w.verse, w.position"
        return [dict(r) for r in self.conn.execute(sql, lemma_ids)]

    def verse_context(self, text_id, book, chapter, verse, mark_number, greek_text):
        """The source verse as one line, with the wanted word between double angle marks."""
        sql = ("SELECT word_number, surface, word_type FROM words "
               "WHERE text_id = ? AND book = ? AND chapter = ? AND verse = ? ORDER BY position")
        words = {}      # word_number -> joined parts, in order
        for row in self.conn.execute(sql, (text_id, book, chapter, verse)):
            if greek_text == "nestle" and row["word_type"] and not row["word_type"].startswith("N"):
                continue
            words[row["word_number"]] = words.get(row["word_number"], "") + row["surface"]
        return " ".join(("<<" + w + ">>") if n == mark_number else w for n, w in words.items())

    def english_numbering(self, text_id, book, chapter, verse):
        """Where English Bibles number this verse, using the verse map (same number if unlisted)."""
        rows = self.conn.execute(
            "SELECT DISTINCT eng_chapter, eng_verse FROM verse_map "
            "WHERE text_id = ? AND book = ? AND chapter = ? AND verse = ?",
            (text_id, book, chapter, verse)).fetchall()
        if rows:
            found = [(r[0], r[1]) for r in rows]
            # Psalms with a two-verse Hebrew title: the map sends both title verses and the
            # first real verse to English verse 1. Only the last Hebrew verse is English verse 1;
            # the earlier ones are the title.
            if book == 19:
                later = self.conn.execute(
                    "SELECT 1 FROM verse_map WHERE text_id = ? AND book = ? AND chapter = ? AND verse > ? "
                    "AND eng_chapter = ? AND eng_verse = ? LIMIT 1",
                    (text_id, book, chapter, verse, found[0][0], found[0][1])).fetchone()
                if later:
                    return [(found[0][0], 0)]
            return found
        # No entry means "same number", with one exception. In the Psalms the Hebrew text
        # counts the title as verse 1 and the map starts at verse 2, so the title has no entry.
        # If another verse of the chapter is mapped TO this number, this verse cannot also
        # be that English verse. It is the unnumbered title, which some Bible databases
        # (including bibles.db, if it has them) store as verse 0.
        taken = self.conn.execute(
            "SELECT 1 FROM verse_map WHERE text_id = ? AND book = ? AND eng_chapter = ? "
            "AND eng_verse = ? LIMIT 1", (text_id, book, chapter, verse)).fetchone()
        if taken:
            return [(chapter, 0)]
        return [(chapter, verse)]


class BiblesReader:
    """Reads KJV Strong's tagging and verse texts from Bible Search Lite's bibles.db."""

    def __init__(self, path):
        self.conn = open_read_only(path)

    def verse_ids(self, refs):
        """Map (book, chapter, verse) to bibles.db verse ids. books.id equals our book number."""
        found = {}
        for book, chapter, verse in set(refs):
            row = self.conn.execute(
                "SELECT id FROM verses WHERE book_id = ? AND chapter = ? AND verse_number = ?",
                (book, chapter, verse)).fetchone()
            if row:
                found[(book, chapter, verse)] = row[0]
        return found

    def _wanted_table(self, verse_ids):
        """Put the wanted verse ids in a temporary table, so one query can fetch them all."""
        self.conn.execute("DROP TABLE IF EXISTS temp.wanted")
        self.conn.execute("CREATE TEMP TABLE wanted (id INTEGER PRIMARY KEY)")
        self.conn.executemany("INSERT OR IGNORE INTO temp.wanted VALUES (?)", [(i,) for i in verse_ids])

    def kjv_words(self, verse_ids, wanted_strongs):
        """For each verse id, the KJV words tagged with this Strong's number."""
        self._wanted_table(verse_ids)
        sql = ("SELECT vs.verse_id, vs.word_position, vs.strongs_number, vs.word_text "
               "FROM verse_strongs vs JOIN temp.wanted w ON w.id = vs.verse_id "
               "ORDER BY vs.verse_id, vs.word_position")
        result = {}
        for verse_id, _pos, strongs, text in self.conn.execute(sql):
            if normalize_strongs(strongs) == wanted_strongs and text:
                result.setdefault(verse_id, []).append(text)
        return result

    def verse_texts(self, verse_ids, abbreviations):
        """For each verse id, {abbreviation: text} for the chosen translations."""
        if not abbreviations:
            return {}
        self._wanted_table(verse_ids)
        marks = ",".join("?" * len(abbreviations))
        sql = (f"SELECT vt.verse_id, t.abbreviation, vt.text FROM verse_texts vt "
               f"JOIN temp.wanted w ON w.id = vt.verse_id "
               f"JOIN translations t ON t.id = vt.translation_id "
               f"WHERE t.abbreviation IN ({marks})")
        result = {}
        for verse_id, abbreviation, text in self.conn.execute(sql, abbreviations):
            result.setdefault(verse_id, {})[abbreviation] = text
        return result

    def available_translations(self):
        return [r[0] for r in self.conn.execute("SELECT abbreviation FROM translations")]


class ProjectReader:
    """Reads approved renderings from a project database."""

    def __init__(self, path):
        self.conn = open_read_only(path)
        self.conn.row_factory = sqlite3.Row

    def glossary(self, lemma_ids):
        marks = ",".join("?" * len(lemma_ids))
        rows = self.conn.execute(
            f"SELECT lemma_id, sense_label, category, rendering, allowed_variants, rule, reason, status "
            f"FROM glossary WHERE lemma_id IN ({marks}) ORDER BY lemma_id, sense_label", lemma_ids)
        return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# Building the sheet
# ----------------------------------------------------------------------
class WordSheetBuilder:
    """Puts the readers together and produces a WordSheet."""

    def __init__(self, source_path, bibles_path, project_path=None):
        self.source = SourceReader(source_path)
        self.bibles = BiblesReader(bibles_path)
        self.project = ProjectReader(project_path) if project_path else None

    def build(self, strongs=None, lemma_id=None, headword=None, translations=None,
              limit=200, greek_text="nestle"):
        lemmas = self.source.find_lemmas(strongs, lemma_id, headword)
        if not lemmas:
            sys.exit("No lemma found for that request.")
        translations = translations or DEFAULT_TRANSLATIONS
        unknown = set(translations) - set(self.bibles.available_translations())
        if unknown:
            sys.exit("Not in bibles.db: " + ", ".join(sorted(unknown)))

        ids = [row["lemma_id"] for row in lemmas]
        sheet = WordSheet(lemmas=lemmas, translations=translations, greek_text=greek_text)
        sheet.strongs_text, sheet.bdb_text = self.source.lexicon(lemmas)
        if self.project:
            sheet.glossary = self.project.glossary(ids)

        uses = self.source.occurrences(ids, greek_text)
        sheet.total_uses = len(uses)

        # The Strong's number used to find the word in the KJV (the first lemma that has one).
        wanted = next((normalize_strongs(r["strongs"]) for r in lemmas if normalize_strongs(r["strongs"])), None)

        # Work out the English numbering of every use once, then fetch everything in bulk.
        english = {}
        for use in uses:
            key = (use["text_id"], use["book"], use["chapter"], use["verse"])
            if key not in english:
                english[key] = self.source.english_numbering(*key)
        refs = {(u["book"],) + ref for u in uses
                for ref in english[(u["text_id"], u["book"], u["chapter"], u["verse"])]}
        verse_ids = self.bibles.verse_ids(refs)

        # Tally over ALL uses, but list only the first `limit`.
        kjv = self.bibles.kjv_words(set(verse_ids.values()), wanted) if wanted else {}
        listed_ids = set()
        tallied = set()         # bibles.db verses already counted in the tally
        uses_per_verse = Counter((u["text_id"], u["book"], u["chapter"], u["verse"]) for u in uses)
        for index, use in enumerate(uses):
            eng_refs = english[(use["text_id"], use["book"], use["chapter"], use["verse"])]
            ids_here = [verse_ids[(use["book"],) + r] for r in eng_refs if (use["book"],) + r in verse_ids]
            words_here = [w for vid in ids_here for w in kjv.get(vid, [])]
            if ids_here:
                sheet.kjv_covered += 1
            elif any(v == 0 for _c, v in eng_refs):
                sheet.title_uses += 1       # English Bibles hold no numbered verse for a title
            # The KJV tagging belongs to the verse, not to one source word. When a verse
            # holds several uses, its KJV words are counted once, not once per use.
            for vid in ids_here:
                if vid not in tallied:
                    tallied.add(vid)
                    for word in kjv.get(vid, []):
                        sheet.kjv_tally[clean_word(word)] += 1
            if index < limit:
                listed_ids.update(ids_here)
                occ = Occurrence(use["word_id"], use["book"], use["chapter"], use["verse"],
                                 use["surface"], use["morph"] or "", use["word_type"] or "",
                                 english_refs=eng_refs, kjv_words=words_here)
                occ.uses_in_verse = uses_per_verse[(use["text_id"], use["book"], use["chapter"], use["verse"])]
                occ.context = self.source.verse_context(use["text_id"], use["book"], use["chapter"],
                                                        use["verse"], use["word_number"], greek_text)
                occ._verse_ids = ids_here
                sheet.occurrences.append(occ)

        texts = self.bibles.verse_texts(listed_ids, translations)
        for occ in sheet.occurrences:
            for vid in occ._verse_ids:
                occ.translations.update(texts.get(vid, {}))
        return sheet


# ----------------------------------------------------------------------
# Showing the sheet
# ----------------------------------------------------------------------
class MarkdownRenderer:
    """Turns a WordSheet into Markdown text."""

    def render(self, sheet):
        lines = []
        first = sheet.lemmas[0]
        title = first.get("headword") or first["lemma"]
        strongs_label = normalize_strongs(first.get("strongs")) or "no Strong number"
        lines.append(f"# Word sheet: {title} ({strongs_label})")
        lines.append("")
        copyrighted = sorted(set(sheet.translations) & COPYRIGHTED)
        if copyrighted:
            lines.append("**Local use only: this sheet includes text under copyright ("
                         + ", ".join(copyrighted) + "). Do not commit or publish it.**")
            lines.append("")

        # 1. The lemma rows
        lines.append("## Lemma rows")
        for row in sheet.lemmas:
            lines.append(f"- id {row['lemma_id']}: {row.get('headword') or row['lemma']}"
                         f" | code {row['lemma']} | {row.get('strongs') or '-'}"
                         f" | {row.get('transliteration') or ''} | hint: {row.get('gloss_hint') or '-'}")
        lines.append("")
        if len(sheet.lemmas) > 1:
            lines.append("Several rows share this Strong's number. Letters such as a and b mark "
                         "senses that the source text distinguishes; check whether they need "
                         "different renderings.")
            lines.append("")

        # 2. The lexicon
        if sheet.strongs_text or sheet.bdb_text:
            lines.append("## Lexicon")
            if sheet.strongs_text:
                lines.append(f"- Strong's: {sheet.strongs_text}")
            if sheet.bdb_text:
                lines.append(f"- BDB: {sheet.bdb_text}")
            lines.append("")

        # 3. Uses and the KJV tally
        text_note = " (Nestle-Aland text)" if sheet.greek_text == "nestle" and \
            any(o.word_type for o in sheet.occurrences) else ""
        lines.append(f"## Uses: {sheet.total_uses}{text_note}")
        if sheet.kjv_tally:
            total = sum(sheet.kjv_tally.values())
            lines.append(f"KJV words tagged with this Strong's number ({total} words, counted once "
                         f"per verse; {sheet.kjv_covered} of {sheet.total_uses} uses were found in bibles.db):")
            lines.append("")
            lines.append(", ".join(f"{word or '(blank)'} x{n}" for word, n in sheet.kjv_tally.most_common()))
        else:
            lines.append("No KJV tagging was found for this word.")
        if sheet.title_uses:
            lines.append("")
            lines.append(f"Note: {sheet.title_uses} of the {sheet.total_uses} uses are in Psalm titles. English Bibles do not "
                         "number the titles and bibles.db does not hold them, so the KJV tally leaves them out "
                         "and the tally above understates how the KJV renders this word.")
        lines.append("")

        # 4. The glossary
        lines.append("## Project glossary")
        if sheet.glossary:
            for g in sheet.glossary:
                lines.append(f"- [{g['status']}] sense '{g['sense_label']}': **{g['rendering']}**"
                             + (f" (also allowed: {g['allowed_variants']})" if g["allowed_variants"] else "")
                             + (f". Rule: {g['rule']}" if g["rule"] else "")
                             + (f". Reason: {g['reason']}" if g["reason"] else ""))
        else:
            lines.append("No decision recorded yet.")
        lines.append("")

        # 5. Each use
        shown = len(sheet.occurrences)
        lines.append(f"## Occurrences (showing {shown} of {sheet.total_uses})")
        for occ in sheet.occurrences:
            lines.append("")
            eng = ""
            if occ.english_refs and occ.english_refs != [(occ.chapter, occ.verse)]:
                names = [f"{c}:{v}" if v else f"{c} title" for c, v in occ.english_refs]
                eng = " (English " + ", ".join(names) + ")"
            lines.append(f"### {occ.reference}{eng}  {occ.surface}  {occ.morph}")
            if occ.context:
                lines.append(f"Source: {occ.context}")
            if any(v == 0 for _c, v in occ.english_refs):
                lines.append("KJV: (a Psalm title, not stored in bibles.db)")
                continue
            kjv_label = "KJV word(s) in this verse" if occ.uses_in_verse > 1 else "KJV word(s)"
            lines.append(f"{kjv_label}: " + (", ".join(occ.kjv_words) if occ.kjv_words else "none tagged")
                         + (f"  [this verse has {occ.uses_in_verse} uses of the word, so these words "
                            f"are not tied to this one]" if occ.uses_in_verse > 1 and occ.kjv_words else ""))
            for abbreviation in sheet.translations:
                if abbreviation in occ.translations:
                    lines.append(f"{abbreviation}: {occ.translations[abbreviation]}")
        if shown < sheet.total_uses:
            lines.append("")
            lines.append(f"({sheet.total_uses - shown} more uses not listed. Raise --limit to see them.)")
        return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Build a word sheet for one Hebrew or Greek lemma")
    parser.add_argument("--source", required=True, help="path to source_texts.db")
    parser.add_argument("--bibles", required=True, help="path to Bible Search Lite bibles.db")
    parser.add_argument("--project", help="path to a project.db, to show its glossary entries")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--strongs", help="a Strong's number such as G26 or H1254")
    group.add_argument("--lemma-id", type=int, help="a lemma_id from the source database")
    group.add_argument("--headword", help="a Hebrew or Greek dictionary form, exactly as stored")
    parser.add_argument("--translations", nargs="+", metavar="ABBR",
                        help="translations to show, default " + " ".join(DEFAULT_TRANSLATIONS))
    parser.add_argument("--limit", type=int, default=200, help="most uses to list (default 200)")
    parser.add_argument("--greek-text", choices=["nestle", "all"], default="nestle",
                        help="which Greek words to count: the Nestle-Aland text or every edition")
    parser.add_argument("--output", help="write the sheet to this file instead of the screen")
    args = parser.parse_args()

    builder = WordSheetBuilder(args.source, args.bibles, args.project)
    sheet = builder.build(args.strongs, args.lemma_id, args.headword, args.translations,
                          args.limit, args.greek_text)
    text = MarkdownRenderer().render(sheet)
    if args.output:
        Path(args.output).expanduser().write_text(text, encoding="utf-8")
        print(f"Word sheet written to {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
