#!/usr/bin/env python3
"""
import_lexicon.py  (place this file in translation_workbench/engine/)

Imports the Open Scriptures Hebrew Lexicon into the source database and links
it to the OSHB lemmas that import_oshb.py already stored.

Three files are read from the HebrewLexicon folder:
    LexicalIndex.xml      -> lexicon_entries  (headword, transliteration, short definition)
    HebrewStrong.xml      -> strongs_entries  (Strong's Hebrew Dictionary)
    BrownDriverBriggs.xml -> bdb_entries      (Brown-Driver-Briggs, as plain text)
and AugIndex.xml, which maps the augmented Strong numbers used by OSHB
(for example "1254 a") to lexical index ids.

After the entries are stored, each lemma gets:
    lex_key       the lexical index entry it belongs to
    headword      the Hebrew or Aramaic dictionary form
    transliteration and gloss_hint, only if those were still empty

Usage:
    python3 import_lexicon.py LEXICON_DIR SOURCE_DB --dry-run
    python3 import_lexicon.py LEXICON_DIR SOURCE_DB [--log FILE]

It can be run again at any time: the lexicon tables are emptied and refilled.
The import is all or nothing.

Credit required when publishing anything derived from this data: the Open
Scriptures Hebrew Bible Project (CC BY 4.0; the texts of Brown, Driver, Briggs
and Strong's Hebrew Dictionary are in the public domain).
"""

import argparse
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
FILES = ["LexicalIndex.xml", "HebrewStrong.xml", "BrownDriverBriggs.xml", "AugIndex.xml"]


def local(tag):
    """Return an XML tag name without its namespace."""
    return tag.rsplit("}", 1)[-1]


def text_of(el, skip=("status",)):
    """All the text inside an element as one plain line, leaving out skipped tags."""
    if el is None:
        return None
    parts = [el.text or ""]
    for child in el:
        if local(child.tag) not in skip:
            parts.append(text_of(child, skip) or "")
        parts.append(child.tail or "")
    return re.sub(r"\s+", " ", "".join(parts)).strip() or None


def first_child(el, name):
    """The first direct child with this tag name, or None."""
    for child in el:
        if local(child.tag) == name:
            return child
    return None


def aug_key(code):
    """Normalise a lemma code so OSHB and AugIndex spellings can be compared."""
    return re.sub(r"\s+", "", str(code)).lower()


class LexiconImporter:
    """Reads the lexicon files and fills the lexicon tables of the source database."""

    def __init__(self, lex_dir, db_path, dry_run=False, log_path=None):
        self.lex_dir = Path(lex_dir).expanduser().resolve()
        self.db_path = Path(db_path).expanduser().resolve()
        self.dry_run = dry_run
        self.log_path = Path(log_path).expanduser().resolve() if log_path else None

        self.index_rows = []        # lexicon_entries
        self.strong_rows = []       # strongs_entries
        self.bdb_rows = []          # bdb_entries
        self.aug = {}               # normalised augmented number -> lexical index id
        self.links = []             # (lemma_id, lex_key) decided for each lemma
        self.unmatched = []         # lemma codes that could not be linked
        self.stats = Counter()
        self.log_lines = []

    # ------------------------------------------------------------------
    def run(self):
        self._check_files()
        self._parse_index()
        self._parse_strongs()
        self._parse_bdb()
        self._parse_aug()
        self._plan_links()
        self._print_report()
        self._write_log()
        if self.dry_run:
            print("Dry run only: nothing was written.")
            return
        self._write()
        print("Import finished.")

    def _check_files(self):
        if not self.lex_dir.is_dir():
            sys.exit(f"Folder not found: {self.lex_dir}")
        missing = [f for f in FILES if not (self.lex_dir / f).is_file()]
        if missing:
            sys.exit("Missing files in " + str(self.lex_dir) + ": " + ", ".join(missing))
        if not self.db_path.is_file():
            sys.exit(f"Source database not found: {self.db_path}")

    def _load(self, name):
        try:
            return ET.parse(self.lex_dir / name).getroot()
        except ET.ParseError as err:
            sys.exit(f"Cannot read {name}: {err}")

    # ------------------------------------------------------------------
    # Reading the files
    # ------------------------------------------------------------------
    def _parse_index(self):
        """LexicalIndex.xml: one entry per Hebrew or Aramaic dictionary word."""
        root = self._load("LexicalIndex.xml")
        for part in root:
            language = part.get(XML_LANG)
            for entry in part.iter():
                if local(entry.tag) != "entry" or not entry.get("id"):
                    continue
                w = first_child(entry, "w")
                xref = first_child(entry, "xref")
                xref = xref if xref is not None else ET.Element("xref")
                self.index_rows.append((
                    entry.get("id"), language, text_of(w),
                    w.get("xlit") if w is not None else None,
                    text_of(first_child(entry, "pos")), text_of(first_child(entry, "def")),
                    xref.get("bdb"), xref.get("strong"), xref.get("twot")))

    def _parse_strongs(self):
        """HebrewStrong.xml: Strong's Hebrew Dictionary."""
        root = self._load("HebrewStrong.xml")
        for entry in root:
            if local(entry.tag) != "entry" or not entry.get("id"):
                continue
            w = first_child(entry, "w")
            attrs = w.attrib if w is not None else {}
            self.strong_rows.append((
                entry.get("id"), attrs.get(XML_LANG), text_of(w), attrs.get("xlit"),
                attrs.get("pron"), attrs.get("pos"),
                text_of(first_child(entry, "source")),
                text_of(first_child(entry, "meaning")),
                text_of(first_child(entry, "usage"))))

    def _parse_bdb(self):
        """BrownDriverBriggs.xml: each entry kept as plain text."""
        root = self._load("BrownDriverBriggs.xml")
        for entry in root.iter():
            if local(entry.tag) != "entry" or not entry.get("id"):
                continue
            w = next((e for e in entry.iter() if local(e.tag) == "w"), None)
            pos = next((e for e in entry.iter() if local(e.tag) == "pos"), None)
            self.bdb_rows.append((entry.get("id"), entry.get("type"), text_of(w),
                                  text_of(pos), text_of(entry)))

    def _parse_aug(self):
        """AugIndex.xml: augmented Strong number -> lexical index id."""
        root = self._load("AugIndex.xml")
        for w in root:
            if local(w.tag) != "w" or not w.get("aug") or not (w.text or "").strip():
                continue
            key = aug_key(w.get("aug"))
            if key in self.aug and self.aug[key] != w.text.strip():
                self._log("duplicate", f"AugIndex lists {key!r} twice with different ids")
                continue
            self.aug[key] = w.text.strip()

    # ------------------------------------------------------------------
    # Linking the lemmas
    # ------------------------------------------------------------------
    def _plan_links(self):
        """Decide the lexical index entry for every Hebrew or Aramaic lemma."""
        index_ids = {row[0] for row in self.index_rows}
        # Strong number -> first lexical index entry that cites it.
        # Several entries can cite one number; a single word beats a phrase, so
        # an entry whose headword has a space is used only if nothing better exists.
        by_strong = {}
        for row in self.index_rows:
            if not row[7]:
                continue
            number = row[7].strip()
            is_phrase = " " in (row[2] or "").strip()
            if number not in by_strong or (by_strong[number][1] and not is_phrase):
                by_strong[number] = (row[0], is_phrase)
        by_strong = {number: entry[0] for number, entry in by_strong.items()}
        conn = sqlite3.connect(self.db_path)
        try:
            lemmas = conn.execute(
                "SELECT lemma_id, lemma FROM lemmas WHERE strongs LIKE 'H%'").fetchall()
        finally:
            conn.close()
        self.stats["lemmas"] = len(lemmas)

        for lemma_id, code in lemmas:
            key = aug_key(code)
            lex_key = self.aug.get(key)
            how = "exact"
            if lex_key is None:
                # Fallback 1: drop a trailing "+" (first part of a compound name) or letter.
                base = re.sub(r"[+a-z]+$", "", key)
                lex_key = self.aug.get(base) if base else None
                how = "compound" if key.endswith("+") else "fallback"
            if lex_key is None:
                # Fallback 2: the Strong's number written in the lexical index itself.
                lex_key = by_strong.get(re.sub(r"[+a-z]+$", "", key))
                how = "strong"
            if lex_key is None or lex_key not in index_ids:
                self.unmatched.append(code)
                self._log("unmatched", f"lemma {code!r} has no lexical index entry")
                continue
            self.stats["linked_" + how] += 1
            if how == "compound":
                self._log("compound", f"lemma {code!r} (first part of a compound name) "
                          f"linked to {lex_key!r}; its gloss is the whole name")
            elif how != "exact":
                self._log("fallback", f"lemma {code!r} linked to {lex_key!r} "
                          f"by {'its Strong number in the index' if how == 'strong' else 'its base number'}")
            self.links.append((lemma_id, lex_key))

    def _log(self, kind, message):
        self.log_lines.append(f"{kind}: {message}")

    # ------------------------------------------------------------------
    # Report and log
    # ------------------------------------------------------------------
    def _print_report(self):
        print(f"Lexicon read from {self.lex_dir}")
        print(f"  lexical index entries:   {len(self.index_rows)}")
        print(f"  Strong's entries:        {len(self.strong_rows)}")
        print(f"  BDB entries:             {len(self.bdb_rows)}")
        print(f"  AugIndex numbers:        {len(self.aug)}")
        print(f"  lemmas in the database:  {self.stats['lemmas']}")
        print(f"  linked exactly:          {self.stats['linked_exact']}")
        print(f"  compound name parts:     {self.stats['linked_compound']}")
        print(f"  linked by base number:   {self.stats['linked_fallback']}")
        print(f"  linked by Strong number: {self.stats['linked_strong']}")
        print(f"  not linked:              {len(self.unmatched)}")
        if self.unmatched:
            print("  first lemmas not linked: " + ", ".join(self.unmatched[:15]))

    def _write_log(self):
        if self.log_path is None:
            return
        self.log_path.write_text("\n".join(self.log_lines) + "\n", encoding="utf-8")
        print(f"  full log written to {self.log_path} ({len(self.log_lines)} lines)")

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def _write(self):
        conn = sqlite3.connect(self.db_path)
        try:
            self._check_schema(conn)
            for table in ("lexicon_entries", "strongs_entries", "bdb_entries"):
                conn.execute(f"DELETE FROM {table}")
            conn.executemany("INSERT OR REPLACE INTO lexicon_entries VALUES (?,?,?,?,?,?,?,?,?)",
                             self.index_rows)
            conn.executemany("INSERT OR REPLACE INTO strongs_entries VALUES (?,?,?,?,?,?,?,?,?)",
                             self.strong_rows)
            conn.executemany("INSERT OR REPLACE INTO bdb_entries VALUES (?,?,?,?,?)",
                             self.bdb_rows)
            # Link the lemmas. Transliteration and gloss are filled only where empty.
            # Clear what an earlier run filled in, so a rerun never keeps stale values.
            # Only Strong's-numbered lemmas are touched; the prefix hints ("in", "and") stay.
            conn.execute("UPDATE lemmas SET lex_key = NULL, headword = NULL, "
                         "transliteration = NULL, gloss_hint = NULL WHERE strongs LIKE 'H%'")
            conn.executemany("UPDATE lemmas SET lex_key = ? WHERE lemma_id = ?",
                             [(k, i) for i, k in self.links])
            conn.execute("""
                UPDATE lemmas SET
                    headword        = (SELECT e.headword FROM lexicon_entries e WHERE e.lex_key = lemmas.lex_key),
                    transliteration = COALESCE(transliteration,
                                      (SELECT e.transliteration FROM lexicon_entries e WHERE e.lex_key = lemmas.lex_key)),
                    gloss_hint      = COALESCE(gloss_hint,
                                      (SELECT e.short_def FROM lexicon_entries e WHERE e.lex_key = lemmas.lex_key))
                WHERE lex_key IS NOT NULL""")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _check_schema(self, conn):
        cols = [r[1] for r in conn.execute("PRAGMA table_info(lemmas)")]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "lex_key" not in cols or "lexicon_entries" not in tables:
            sys.exit("This source database was made with an older schema.py. "
                     "Delete it, recreate it with the current schema.py, import OSHB "
                     "again, and then run this importer.")


def main():
    parser = argparse.ArgumentParser(description="Import the Open Scriptures Hebrew Lexicon")
    parser.add_argument("lexicon_dir", help="the HebrewLexicon folder (holds LexicalIndex.xml)")
    parser.add_argument("source_db", help="path to source_texts.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="read the files and print a report without writing anything")
    parser.add_argument("--log", metavar="FILE", help="write unmatched lemmas and fallbacks here")
    args = parser.parse_args()
    LexiconImporter(args.lexicon_dir, args.source_db, args.dry_run, args.log).run()


if __name__ == "__main__":
    main()
