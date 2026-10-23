#!/usr/bin/env python3
"""
import_greek_nt.py  (place this file in translation_workbench/engine/)

Imports the Greek New Testament from STEPBible's TAGNT ("Translators Amalgamated
Greek New Testament") into the source database: every word with its dictionary
form, Strong's number, morphology and the editions that contain it.

Source: https://github.com/STEPBible/STEPBible-Data
        folder "Translators Amalgamated OT+NT", two files named "TAGNT ..."
License: CC BY 4.0. Credit "STEP Bible" (www.STEPBible.org), data created by
         STEPBible based on work at Tyndale House Cambridge.

The file lists the words of ALL the major Greek editions together, so a word
that only the traditional text has (the text behind the KJV) is included too.
Each word row therefore carries:
    word_type   NKO = in all editions; N... = in the Nestle-Aland text;
                K or k = traditional text only; O or o = other editions only
    editions    for example NA28+NA27+Tyn+SBL+WH+Treg+TR+Byz
A translation session chooses the text it follows by filtering on word_type
(for example, words whose type starts with N for the Nestle-Aland text).

How the data is stored:
    texts         one row named TAGNT
    lemmas        one row per dictionary form plus disambiguated Strong's number
    words         one row per word; position = word_number = the word's number in the verse
    source_notes  variant readings, spelling variants and variant notes (kinds below)
    verse_map     verses the KJV numbers differently (from the [x.y] marks in the file)

Not imported: the English wording (it is Berean Study Bible based) and the
Spanish and context glosses. Only the dictionary-form gloss from the Tyndale
Brief Lexicon (TBESG) is kept, as lemmas.gloss_hint.

Usage:
    python3 import_greek_nt.py STEP_DIR SOURCE_DB --dry-run
    python3 import_greek_nt.py STEP_DIR SOURCE_DB [--books Matthew John] [--replace] [--log FILE]

STEP_DIR is the folder holding the two TAGNT files. The import is all or nothing.
"""

import argparse
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from pathlib import Path

# books.py sits next to this file, whether run as a script or as part of a package.
try:
    from books import book_name, book_number
except ImportError:
    from engine.books import book_name, book_number

TEXT_NAME = "TAGNT"
TEXT_ORIGIN = "https://github.com/STEPBible/STEPBible-Data (Translators Amalgamated Greek NT)"
TEXT_LICENSE = ("CC BY 4.0. Data created by www.STEPBible.org based on work at Tyndale House "
                "Cambridge. Credit STEP Bible and refer others to github.com/STEPBible.")

# The three-letter book codes used in the TAGNT files, with the standard book numbers.
TAGNT_BOOKS = {
    "Mat": 40, "Mrk": 41, "Luk": 42, "Jhn": 43, "Act": 44, "Rom": 45, "1Co": 46, "2Co": 47,
    "Gal": 48, "Eph": 49, "Php": 50, "Col": 51, "1Th": 52, "2Th": 53, "1Ti": 54, "2Ti": 55,
    "Tit": 56, "Phm": 57, "Heb": 58, "Jas": 59, "1Pe": 60, "2Pe": 61, "1Jn": 62, "2Jn": 63,
    "3Jn": 64, "Jud": 65, "Rev": 66,
}

# A word row starts with "Mat.1.1#01=NKO" and may carry other verse numbers in
# brackets: [KJV numbering], (Nestle-Aland numbering) or {other editions}.
ROW_PATTERN = re.compile(
    r"^(?P<book>[1-3]?[A-Za-z]{2,3})\.(?P<chapter>\d+)\.(?P<verse>\d+)"
    r"(?:(?P<open>[\[\(\{])(?P<ach>\d+)\.(?P<av>\d+)[\]\)\}])?"
    r"#(?P<num>\d+)=(?P<type>\S*)$")

# A disambiguated Strong's number such as G0976 or G2424G.
STRONG_PATTERN = re.compile(r"^G\d{4}[A-Z]?$")

# The Greek column looks like "Βίβλος (Biblos)": the word, then its transliteration.
GREEK_PATTERN = re.compile(r"^(.*?)\s+\(([^()]*)\)\s*$")

# Note kinds written to source_notes, and the column each comes from.
NOTE_COLUMNS = [(6, "variant_reading"), (7, "spelling_variants"), (13, "variant_note")]
MIN_COLUMNS = 14


def nfc(text):
    """Normalise Greek text to Unicode NFC.

    The source mixes two encodings of the same accented letters (the old "oxia"
    code points and the usual "tonos" ones), so a typed search would find only
    some of the words. NFC makes every word use the usual encoding.
    """
    return unicodedata.normalize("NFC", text)


class GreekNtImporter:
    """Reads the TAGNT files and writes them into the source database."""

    def __init__(self, step_dir, db_path, books=None, replace=False, dry_run=False,
                 log_path=None):
        self.step_dir = Path(step_dir).expanduser().resolve()
        self.db_path = Path(db_path).expanduser().resolve()
        self.replace = replace
        self.dry_run = dry_run
        self.log_path = Path(log_path).expanduser().resolve() if log_path else None

        if books:
            try:
                self.book_numbers = sorted({book_number(b) for b in books})
            except KeyError as err:
                sys.exit(str(err.args[0]))
            if any(n < 40 or n > 66 for n in self.book_numbers):
                sys.exit("TAGNT contains only the 27 books of the New Testament (40 to 66).")
        else:
            self.book_numbers = list(range(40, 67))

        self.word_rows = []         # (book, chapter, verse, number, surface, key, morph, source_id, type, editions)
        self.note_rows = []         # rows for source_notes
        self.lemma_info = {}        # key -> (headword, gloss) taken from the first word seen
        self.map_rows = {}          # (book, chapter, verse) -> (eng_chapter, eng_verse)
        self.stats = Counter()
        self.types = Counter()
        self.note_kinds = Counter()
        self.books_seen = Counter()
        self.other_numbering = Counter()    # counts of (NA) and {other} verse numbers
        self.anomalies = []
        self.log_lines = []

    # ------------------------------------------------------------------
    def run(self):
        for path in self._find_files():
            self._parse_file(path)
        self._print_report()
        self._write_log()
        if self.dry_run:
            print("Dry run only: nothing was written.")
            return
        self._write()
        print("Import finished.")

    def _find_files(self):
        if not self.step_dir.is_dir():
            sys.exit(f"Folder not found: {self.step_dir}")
        files = sorted(self.step_dir.glob("TAGNT*.txt"))
        if not files:
            sys.exit(f"No TAGNT files found in {self.step_dir}")
        return files

    def _anomaly(self, message):
        self.stats["anomalies"] += 1
        if len(self.anomalies) < 25:
            self.anomalies.append(message)
        self.log_lines.append("anomaly: " + message)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def _parse_file(self, path):
        """Read one TAGNT file. Only lines that start like a word row are used."""
        with open(path, encoding="utf-8-sig") as f:
            for line_no, line in enumerate(f, 1):
                cols = line.rstrip("\n").split("\t")
                match = ROW_PATTERN.match(cols[0])
                if not match:
                    continue                         # headings, comments, blank lines
                if len(cols) < MIN_COLUMNS:
                    self._anomaly(f"{path.name} line {line_no}: only {len(cols)} columns")
                    continue
                code = match.group("book")
                book = TAGNT_BOOKS.get(code)
                if book is None:
                    self._anomaly(f"{path.name} line {line_no}: unknown book code {code!r}")
                    continue
                if book not in self.book_numbers:
                    continue
                self._add_word(match, book, cols, f"{path.name} line {line_no}")

    def _add_word(self, match, book, cols, where):
        chapter, verse = int(match.group("chapter")), int(match.group("verse"))
        number = int(match.group("num"))
        ref = f"{book_name(book)} {chapter}:{verse}"
        source_id = f"{match.group('book')}.{chapter}.{verse}#{number:02d}"

        # Greek surface form (the transliteration in brackets is dropped).
        greek = GREEK_PATTERN.match(cols[1].strip())
        surface = nfc((greek.group(1) if greek else cols[1]).strip())

        # Strong's number and morphology: "G0976=N-NSF".
        strong, _, morph = cols[3].strip().partition("=")
        if not STRONG_PATTERN.match(strong):
            self._anomaly(f"unusual Strong's number {strong!r} at {ref} ({where})")

        # Dictionary form and short gloss: "Δαυείδ, Δαυίδ, Δαβίδ=David".
        forms, _, gloss = cols[4].strip().partition("=")
        headword = nfc(forms.split(",")[0].strip())
        if not surface or not headword:
            self._anomaly(f"missing Greek word or dictionary form at {ref} ({where})")
            return
        key = ("greek", headword, strong)
        self.lemma_info.setdefault(key, (headword, gloss.strip() or None))

        word_type = match.group("type")
        self.types[word_type] += 1
        self.stats["words"] += 1
        self.books_seen[book] += 1
        self.word_rows.append((book, chapter, verse, number, surface, key, morph or None,
                               source_id, word_type, cols[5].strip() or None))

        # Notes: variant readings, spelling variants and variant notes.
        for index, kind in NOTE_COLUMNS:
            text = nfc(cols[index].strip()) if index < len(cols) else ""
            if text:
                self.note_rows.append((book, chapter, verse, number, source_id, kind, text))
                self.note_kinds[kind] += 1

        # Verse numbers that differ in other systems.
        if match.group("open"):
            alt = (int(match.group("ach")), int(match.group("av")))
            if match.group("open") == "[":
                previous = self.map_rows.setdefault((book, chapter, verse), alt)
                if previous != alt:
                    self.log_lines.append(f"note: {ref} has more than one KJV numbering; kept {previous}")
            else:
                self.other_numbering[match.group("open")] += 1

    # ------------------------------------------------------------------
    # Report and log
    # ------------------------------------------------------------------
    def _print_report(self):
        verses = len({(b, c, v) for (b, c, v, *_rest) in self.word_rows})
        print(f"TAGNT read from {self.step_dir}")
        print(f"  books:                {len(self.books_seen)}")
        print(f"  verses:               {verses}")
        print(f"  words (all editions): {self.stats['words']}")
        print(f"  distinct lemma rows:  {len(self.lemma_info)}")
        print(f"  KJV verse number differences: {len(self.map_rows)}")
        print(f"  other numberings noted only in the log: {sum(self.other_numbering.values())}")
        print("  word types (most common first):")
        for kind, n in self.types.most_common(8):
            print(f"    {kind:10} {n}")
        nt = sum(n for kind, n in self.types.items() if kind.startswith("N"))
        print(f"  words in the Nestle-Aland text (type starts with N): {nt}")
        print(f"  notes stored: {len(self.note_rows)}")
        for kind, n in sorted(self.note_kinds.items()):
            print(f"    {kind:18} {n}")
        print(f"  anomalies: {self.stats['anomalies']}")
        for message in self.anomalies[:10]:
            print("   - " + message)

    def _write_log(self):
        if self.log_path is None:
            return
        self.log_path.write_text("\n".join(self.log_lines) + "\n", encoding="utf-8")
        print(f"  full log written to {self.log_path} ({len(self.log_lines)} lines)")

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def _write(self):
        if not self.db_path.is_file():
            sys.exit(f"Source database not found: {self.db_path}\n"
                     "Create it first with schema.py.")
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._check_schema(conn)
            text_id = self._prepare_text(conn)
            lemma_ids = self._store_lemmas(conn)
            conn.executemany(
                "INSERT INTO words (text_id, book, chapter, verse, position, word_number, "
                "surface, lemma_id, morph, source_id, word_type, editions) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(text_id, b, c, v, n, n, surf, lemma_ids[key], morph, sid, wtype, eds)
                 for (b, c, v, n, surf, key, morph, sid, wtype, eds) in self.word_rows])
            conn.executemany(
                "INSERT INTO source_notes (text_id, book, chapter, verse, word_number, "
                "base_source_id, note_type, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(text_id,) + row for row in self.note_rows])
            conn.executemany(
                "INSERT OR REPLACE INTO verse_map (text_id, book, chapter, verse, part, "
                "eng_chapter, eng_verse, eng_part, note) VALUES (?, ?, ?, ?, '', ?, ?, '', NULL)",
                [(text_id, b, c, v, ec, ev) for (b, c, v), (ec, ev) in self.map_rows.items()])
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _check_schema(self, conn):
        cols = [r[1] for r in conn.execute("PRAGMA table_info(words)")]
        if "word_type" not in cols:
            sys.exit("The words table has no word_type column. Run: python3 schema.py source "
                     "<source_texts.db>  (it adds missing columns), then run this again.")

    def _prepare_text(self, conn):
        """Find or create the texts row; clear old rows when --replace is used."""
        marks = ",".join("?" * len(self.book_numbers))
        row = conn.execute("SELECT text_id FROM texts WHERE name = ?", (TEXT_NAME,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO texts (name, language, origin, license, notes) VALUES (?, 'greek', ?, ?, ?)",
                (TEXT_NAME, TEXT_ORIGIN, TEXT_LICENSE,
                 "All major Greek editions in one file; filter on words.word_type. "
                 "Imported by import_greek_nt.py"))
            return cur.lastrowid
        text_id = row[0]
        existing = conn.execute(
            f"SELECT COUNT(*) FROM words WHERE text_id = ? AND book IN ({marks})",
            [text_id] + self.book_numbers).fetchone()[0]
        if existing:
            if not self.replace:
                sys.exit(f"{existing} word rows for these books are already in the database. "
                         "Run again with --replace to replace them.")
            for table in ("words", "source_notes", "verse_map"):
                conn.execute(f"DELETE FROM {table} WHERE text_id = ? AND book IN ({marks})",
                             [text_id] + self.book_numbers)
            print(f"Replacing {existing} earlier word rows.")
        conn.execute("UPDATE texts SET origin = ?, license = ? WHERE text_id = ?",
                     (TEXT_ORIGIN, TEXT_LICENSE, text_id))
        return text_id

    def _store_lemmas(self, conn):
        """Make sure every lemma exists and return {key: lemma_id}."""
        ids = {(lang, lemma, strongs): lemma_id for lemma_id, lang, lemma, strongs
               in conn.execute("SELECT lemma_id, language, lemma, strongs FROM lemmas "
                               "WHERE language = 'greek'")}
        for key in sorted(self.lemma_info):
            if key in ids:
                continue
            language, lemma, strongs = key
            headword, gloss = self.lemma_info[key]
            cur = conn.execute(
                "INSERT INTO lemmas (language, lemma, strongs, gloss_hint, headword) "
                "VALUES (?, ?, ?, ?, ?)", (language, lemma, strongs, gloss, headword))
            ids[key] = cur.lastrowid
        return ids


def main():
    parser = argparse.ArgumentParser(description="Import the STEPBible Greek New Testament (TAGNT)")
    parser.add_argument("step_dir", help='the "Translators Amalgamated OT+NT" folder')
    parser.add_argument("source_db", help="path to source_texts.db")
    parser.add_argument("--books", nargs="+", metavar="BOOK",
                        help="import only these books, for example Matthew John")
    parser.add_argument("--replace", action="store_true",
                        help="replace word rows imported earlier for these books")
    parser.add_argument("--dry-run", action="store_true",
                        help="read the files and print a report without writing anything")
    parser.add_argument("--log", metavar="FILE", help="write anomalies and notes to this file")
    args = parser.parse_args()
    GreekNtImporter(args.step_dir, args.source_db, args.books, args.replace, args.dry_run,
                    args.log).run()


if __name__ == "__main__":
    main()
