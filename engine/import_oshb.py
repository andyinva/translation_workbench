#!/usr/bin/env python3
"""
import_oshb.py  (place this file in translation_workbench/engine/)

Imports the Open Scriptures Hebrew Bible (OSHB, also called morphhb) into the
source database: every word of the Hebrew Bible with its lemma and morphology,
plus the map of verses that are numbered differently in English Bibles.

How the data is stored:
    texts       one row for the OSHB text, with its origin and license
    lemmas      one row per distinct lemma code, for example "1254 a"
                (the Hebrew headword and gloss hints are added later from the lexicon)
    words       one row per word PART
    verse_map   verses whose numbering differs in English Bibles (from VerseMap.xml)
    source_notes  alternative readings (ketiv and qere) and editorial notes, each
                  pointing to the word it follows

OSHB writes a word that has attached prefixes as one tag, with the parts
separated by "/":
    <w lemma="b/7225" morph="HR/Ncfsa" id="01xeN">בְּ/רֵאשִׁית</w>
This importer splits such a word into one row per part ("in" and "beginning").
All parts of a word share one word_number and one source_id.

Usage:
    python3 import_oshb.py WLC_DIR SOURCE_DB --dry-run     (read and report only)
    python3 import_oshb.py WLC_DIR SOURCE_DB               (read and import)
    python3 import_oshb.py WLC_DIR SOURCE_DB --books Ruth Jonah
    python3 import_oshb.py WLC_DIR SOURCE_DB --replace

WLC_DIR is the "wlc" folder of morphhb. Only the 39 book files (Gen.xml and so
on) and VerseMap.xml are read, so other files kept in that folder are ignored.
The import is all or nothing: if anything goes wrong, the database is unchanged.

Credit required when publishing anything derived from this data: the Open
Scriptures Hebrew Bible Project (lemma and morphology data are CC BY 4.0, the
Westminster Leningrad Codex text is public domain).
"""

import argparse
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# books.py sits next to this file, whether run as a script or as part of a package.
try:
    from books import book_name, book_number, osis_id
except ImportError:
    from engine.books import book_name, book_number, osis_id

TEXT_NAME = "OSHB"
TEXT_ORIGIN = "https://github.com/openscriptures/morphhb"
TEXT_LICENSE = ("Lemma and morphology data: CC BY 4.0 (credit the Open Scriptures Hebrew "
                "Bible Project). Westminster Leningrad Codex text: public domain.")

OT_BOOK_COUNT = 39      # books 1 to 39 are the Hebrew Bible in Protestant order

# Short hints for the one-letter prefix codes that OSHB puts before a lemma number.
PREFIX_HINTS = {
    "b": "in, with", "c": "and", "d": "the", "k": "like, as",
    "l": "to, for", "m": "from", "s": "that, which (relative)",
}

# A reference such as "Gen.32.1" or "1Sam.3.4". VerseMap.xml also uses a part
# letter for split verses, such as "Ps.13.6!a" (the first part of verse 6).
REF_PATTERN = re.compile(r"^([1-3]?[A-Za-z]+)\.(\d+)\.(\d+)(?:!([a-z]))?$")


def local(tag):
    """Return an XML tag name without its namespace."""
    return tag.rsplit("}", 1)[-1]


class OshbImporter:
    """Reads the OSHB XML files and writes them into the source database."""

    def __init__(self, wlc_dir, db_path, books=None, replace=False, dry_run=False,
                 log_path=None):
        self.wlc_dir = Path(wlc_dir).expanduser().resolve()
        self.db_path = Path(db_path).expanduser().resolve()
        self.replace = replace
        self.dry_run = dry_run
        self.log_path = Path(log_path).expanduser().resolve() if log_path else None

        # Which books to import, as numbers. The default is all 39.
        if books:
            try:
                self.book_numbers = sorted({book_number(b) for b in books})
            except KeyError as err:
                sys.exit(str(err.args[0]))
            if any(n > OT_BOOK_COUNT for n in self.book_numbers):
                sys.exit("OSHB contains only the 39 books of the Hebrew Bible.")
        else:
            self.book_numbers = list(range(1, OT_BOOK_COUNT + 1))

        self.part_rows = []         # one tuple per word part, collected while reading
        self.map_rows = []          # one tuple per verse_map entry
        self.note_rows = []         # one tuple per reading or note (ketiv/qere and so on)
        self.note_kinds = Counter() # how many note rows of each kind
        self.stats = Counter()      # counts shown in the report
        self.other_tags = Counter() # tags inside a verse that are not words
        self.anomalies = []         # the first few problems found, shown in the report
        self.log_lines = []         # every anomaly and guess, written to the log file
        self.shapes = Counter()     # how often each odd word shape occurs
        self.shape_examples = {}    # one example for each odd word shape

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def run(self):
        if not self.wlc_dir.is_dir():
            sys.exit(f"Folder not found: {self.wlc_dir}")
        self._check_files()

        for number in self.book_numbers:
            self._parse_book(self.wlc_dir / f"{osis_id(number)}.xml")
        self._parse_verse_map()
        self._print_report()
        self._write_log()

        if self.dry_run:
            print("Dry run only: nothing was written.")
            return
        self._write()
        print("Import finished.")

    def _check_files(self):
        """Stop early if any wanted book file is missing."""
        missing = [f"{osis_id(n)}.xml" for n in self.book_numbers
                   if not (self.wlc_dir / f"{osis_id(n)}.xml").is_file()]
        if missing:
            sys.exit("Missing book files in " + str(self.wlc_dir) + ": " + ", ".join(missing))

    def _anomaly(self, message, shape_info=None):
        """Remember a problem. Only the first few are shown in the report."""
        self.stats["anomalies"] += 1
        if len(self.anomalies) < 25:
            self.anomalies.append(message)
        self._log("anomaly", message, shape_info)

    def _log(self, kind, message, shape_info=None):
        """Add a line to the full log, and count the word shape if one is given."""
        self.log_lines.append(f"{kind}: {message}")
        if shape_info:
            self.shapes[shape_info] += 1
            self.shape_examples.setdefault(shape_info, message)

    # ------------------------------------------------------------------
    # Reading the book files
    # ------------------------------------------------------------------
    def _parse_book(self, path):
        """Read one book file and collect a row for every word part."""
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as err:
            sys.exit(f"Cannot read {path.name}: {err}")
        self.stats["books"] += 1

        # Look for verses at any depth, so the exact nesting does not matter.
        for verse_el in root.iter():
            if local(verse_el.tag) != "verse":
                continue
            osis = verse_el.get("osisID")
            if not osis:
                continue
            try:
                code, chapter, verse = osis.split(".")
                book = book_number(code)
                chapter, verse = int(chapter), int(verse)
            except (ValueError, KeyError):
                self._anomaly(f"cannot read verse id {osis!r} in {path.name}")
                continue
            self.stats["verses"] += 1

            position = 0        # counts word parts through the verse
            word_number = 0     # counts whole words through the verse
            last_id = None      # source id of the most recent word (a note belongs to it)
            for child in verse_el:
                if local(child.tag) == "w":
                    word_number += 1
                    last_id = child.get("id")
                    position = self._add_word(child, book, chapter, verse,
                                              position, word_number)
                elif local(child.tag) == "note":
                    # Alternative readings (ketiv/qere) and editorial notes are stored.
                    self._add_note(child, book, chapter, verse, word_number, last_id)
                else:
                    # Punctuation marks, notes and so on. Counted, not imported.
                    self.other_tags[(local(child.tag), child.get("type"))] += 1
                    for nested in child.iter():
                        if nested is not child and local(nested.tag) == "w":
                            self.stats["nested_words_skipped"] += 1

    def _add_word(self, w, book, chapter, verse, position, word_number):
        """Split one <w> tag into parts and collect a row for each part."""
        surface = "".join(w.itertext()).strip()
        lemma = w.get("lemma")
        morph = w.get("morph")
        source_id = w.get("id")
        ref = f"{book_name(book)} {chapter}:{verse}"

        if not surface:
            self._anomaly(f"empty word at {ref} (id {source_id})")
            return position

        surface_parts = surface.split("/")
        lemma_parts = lemma.split("/") if lemma else []
        morph_parts = morph.split("/") if morph else []

        # The first morph part starts with H (Hebrew) or A (Aramaic).
        language = "hebrew"
        if morph_parts and morph_parts[0][:1] in ("H", "A"):
            if morph_parts[0][:1] == "A":
                language = "aramaic"
            morph_parts[0] = morph_parts[0][1:]

        self.stats["words"] += 1
        if language == "aramaic":
            self.stats["aramaic_words"] += 1
        if not lemma_parts:
            self.stats["words_without_lemma"] += 1

        # The text and the morph codes should have the same number of parts.
        # Normally the lemma parts line up with every part EXCEPT suffix parts
        # (morph code starting with "S", such as the pronoun in "his seed"), which
        # have no lemma of their own. A few words in OSHB are tagged irregularly;
        # those are lined up by a best guess and listed in the log for review.
        count = len(surface_parts)
        n_lemma = len(lemma_parts)
        morph_ok = (not morph_parts) or len(morph_parts) == count
        shape = (count, n_lemma, len(morph_parts))   # text parts / lemma parts / morph parts
        detail = f"{surface!r} / {lemma!r} / {morph!r}"

        slots = None            # which parts receive the lemma parts (None: cannot line up)
        irregular = False
        if morph_ok and n_lemma <= count:
            regular = ([i for i, m in enumerate(morph_parts) if not m.startswith("S")]
                       if morph_parts else list(range(count)))
            if n_lemma == 0:
                slots = []
            elif n_lemma == len(regular):
                slots = regular                        # the normal case
            elif n_lemma == count:
                slots = list(range(count))             # a suffix part carries a lemma
                irregular = True
            else:
                slots = list(range(n_lemma))           # fewer lemmas: the first parts get them
                irregular = True

        if slots is None:
            # Cannot line up. Keep the whole word as one row, tagged with its last lemma part.
            self.stats["single_row"] += 1
            self._anomaly(f"parts do not line up at {ref} (id {source_id}): {detail}",
                          ("single row", shape))
            surface_parts = [surface]
            lemma_parts = lemma_parts[-1:]
            morph_parts = ["/".join(morph_parts)] if morph_parts else []
            slots = [0] if lemma_parts else []
        elif irregular:
            self.stats["irregular"] += 1
            self._log("irregular", f"lemma lined up by guess at {ref} (id {source_id}): {detail}",
                      ("guessed", shape))

        # Hand the lemma parts, in order, to the parts that were chosen to have one.
        lemma_for_slot = dict(zip(slots, lemma_parts))
        for i, part_text in enumerate(surface_parts):
            code = lemma_for_slot.get(i)
            key = self._lemma_key(code, language) if code else None
            part_morph = morph_parts[i] if i < len(morph_parts) else None
            if key is None and part_morph and part_morph.startswith("S"):
                self.stats["suffix_parts"] += 1
            position += 1
            self.stats["parts"] += 1
            self.part_rows.append((book, chapter, verse, position, word_number,
                                   part_text, key, part_morph, source_id))
        return position

    def _add_note(self, note, book, chapter, verse, word_number, base_id):
        """Store one <note> from the text.

        A note can hold one or more readings (<rdg>), for example the qere (the
        reading spoken aloud) that goes with a ketiv (the word as written). Each
        reading becomes one row. A note with no reading becomes one row holding
        the note text. Every row points to the word it follows (base_source_id),
        so the words table is never changed by a note.
        """
        note_type = note.get("type") or ""
        catch_word = None
        readings = []
        for el in note:
            if local(el.tag) == "catchWord":
                catch_word = "".join(el.itertext()).strip() or None
            elif local(el.tag) == "rdg":
                readings.append(el)

        if readings:
            for rdg in readings:
                words = [e for e in rdg.iter() if local(e.tag) == "w"]
                self._store_note(book, chapter, verse, word_number, base_id, note_type,
                                 catch_word, rdg.get("type"),
                                 "".join(rdg.itertext()).strip() or None,
                                 words, None)
        else:
            words = [e for e in note.iter() if local(e.tag) == "w"]
            text = "".join(note.itertext()).strip() or None
            self._store_note(book, chapter, verse, word_number, base_id, note_type,
                             catch_word, None, None, words, text)

    def _store_note(self, book, chapter, verse, word_number, base_id, note_type,
                    catch_word, reading_type, surface, words, content):
        """Collect one row for the source_notes table."""
        lemma = " | ".join(w.get("lemma") or "" for w in words) or None
        morph = " | ".join(w.get("morph") or "" for w in words) or None
        reading_id = ",".join(w.get("id") or "" for w in words) or None
        self.note_rows.append((book, chapter, verse, word_number, base_id, note_type,
                               catch_word, reading_type, surface, lemma, morph,
                               reading_id, content))
        self.note_kinds[(note_type or "(none)", reading_type or "")] += 1

    def _lemma_key(self, code, language):
        """Turn an OSHB lemma part into (language, lemma, strongs)."""
        code = code.strip()
        number = re.match(r"^(\d+)", code)
        if number:                                  # "7225" or "1254 a"
            return (language, code, "H" + number.group(1))
        if re.fullmatch(r"[a-z]{1,3}", code):       # prefix code such as "b", "c", "d"
            return (language, code, "")
        self._anomaly(f"unrecognized lemma code {code!r}")
        return (language, code, "")

    # ------------------------------------------------------------------
    # Reading the verse map
    # ------------------------------------------------------------------
    def _parse_verse_map(self):
        """Read VerseMap.xml: where the Hebrew numbering differs from the KJV."""
        path = self.wlc_dir / "VerseMap.xml"
        if not path.is_file():
            self._anomaly("VerseMap.xml not found, so verse_map will be empty")
            return
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as err:
            sys.exit(f"Cannot read VerseMap.xml: {err}")

        for el in root.iter():
            if local(el.tag) != "verse" or el.get("wlc") is None:
                continue
            hebrew = REF_PATTERN.match(el.get("wlc") or "")
            english = REF_PATTERN.match(el.get("kjv") or "")
            if not hebrew or not english:
                self._anomaly(f"cannot read verse map entry {el.get('wlc')!r} -> {el.get('kjv')!r}")
                continue
            try:
                book = book_number(hebrew.group(1))
            except KeyError:
                self._anomaly(f"verse map has unknown book {hebrew.group(1)!r}")
                continue
            if book not in self.book_numbers:
                continue
            kind = el.get("type")
            note = kind if kind and kind != "full" else None   # only mention special cases
            # The part letters ("a", "b") say which part of a split verse is meant.
            # They are part of the key, because "Ps.13.6!a" and "Ps.13.6!b" are
            # two different entries for the same Hebrew verse.
            self.map_rows.append((book, int(hebrew.group(2)), int(hebrew.group(3)),
                                  hebrew.group(4) or "",
                                  int(english.group(2)), int(english.group(3)),
                                  english.group(4) or "", note))

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    def _print_report(self):
        distinct = {row[6] for row in self.part_rows if row[6]}
        print(f"OSHB read from {self.wlc_dir}")
        print(f"  books read:           {self.stats['books']}")
        print(f"  verses:               {self.stats['verses']}")
        print(f"  whole words:          {self.stats['words']}")
        print(f"  word parts (rows):    {self.stats['parts']}")
        print(f"  Aramaic words:        {self.stats['aramaic_words']}")
        print(f"  words without lemma:  {self.stats['words_without_lemma']}")
        print(f"  suffix parts (no lemma by design): {self.stats['suffix_parts']}")
        print(f"  distinct lemma codes: {len(distinct)}")
        print(f"  verse map entries:    {len(self.map_rows)}")
        print(f"  notes and readings stored: {len(self.note_rows)}")
        for (kind, rdg), n in sorted(self.note_kinds.items()):
            print(f"    note type={kind:12} reading={rdg or '-':9} {n}")
        if self.stats["nested_words_skipped"]:
            print(f"  words nested in other tags (not imported): {self.stats['nested_words_skipped']}")
        if self.other_tags:
            print("  other tags inside verses (not imported):")
            for (tag, kind), n in sorted(self.other_tags.items(), key=lambda x: str(x)):
                print(f"    <{tag} type={kind}>: {n}")
        print(f"  lemma lined up by guess:  {self.stats['irregular']}  (irregular tagging in OSHB)")
        print(f"  words kept as one row:    {self.stats['single_row']}")
        print(f"  anomalies (all kinds):    {self.stats['anomalies']}")
        if self.shapes:
            print("  odd word shapes (text parts / lemma parts / morph parts), most common first:")
            for (kind, shape), n in self.shapes.most_common(10):
                example = self.shape_examples[(kind, shape)]
                print(f"    {kind:10} {shape[0]}/{shape[1]}/{shape[2]}  x{n}   e.g. {example}")
        if self.anomalies:
            print("  first anomalies:")
            for message in self.anomalies[:10]:
                print("   - " + message)

    def _write_log(self):
        """Write every anomaly and every guess to the log file, if one was asked for."""
        if self.log_path is None:
            return
        self.log_path.write_text("\n".join(self.log_lines) + "\n", encoding="utf-8")
        print(f"  full log written to {self.log_path} ({len(self.log_lines)} lines)")

    # ------------------------------------------------------------------
    # Writing to the database
    # ------------------------------------------------------------------
    def _write(self):
        """Write everything in one transaction (all or nothing)."""
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
                "surface, lemma_id, morph, source_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(text_id, b, c, v, pos, wn, surf, lemma_ids.get(key), morph, sid)
                 for (b, c, v, pos, wn, surf, key, morph, sid) in self.part_rows],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO verse_map (text_id, book, chapter, verse, part, "
                "eng_chapter, eng_verse, eng_part, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(text_id,) + row for row in self.map_rows],
            )
            conn.executemany(
                "INSERT INTO source_notes (text_id, book, chapter, verse, word_number, "
                "base_source_id, note_type, catch_word, reading_type, surface, lemma, morph, "
                "reading_source_id, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(text_id,) + row for row in self.note_rows],
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _check_schema(self, conn):
        """Make sure the database was made by the current schema.py."""
        words_cols = [row[1] for row in conn.execute("PRAGMA table_info(words)")]
        map_cols = [row[1] for row in conn.execute("PRAGMA table_info(verse_map)")]
        notes_cols = [row[1] for row in conn.execute("PRAGMA table_info(source_notes)")]
        if "source_id" not in words_cols or "part" not in map_cols or not notes_cols:
            sys.exit("This source database was made with an older schema.py. "
                     "Delete it, recreate it with the current schema.py, and run again.")

    def _prepare_text(self, conn):
        """Find or create the texts row, and clear old rows when --replace is used."""
        marks = ",".join("?" * len(self.book_numbers))
        row = conn.execute("SELECT text_id FROM texts WHERE name = ?", (TEXT_NAME,)).fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO texts (name, language, origin, license, notes) VALUES (?, 'hebrew', ?, ?, ?)",
                (TEXT_NAME, TEXT_ORIGIN, TEXT_LICENSE, "Imported by import_oshb.py"))
            return cur.lastrowid

        text_id = row[0]
        existing = conn.execute(
            f"SELECT COUNT(*) FROM words WHERE text_id = ? AND book IN ({marks})",
            [text_id] + self.book_numbers).fetchone()[0]
        if existing:
            if not self.replace:
                sys.exit(f"{existing} word rows for these books are already in the database. "
                         "Run again with --replace to replace them.")
            conn.execute(f"DELETE FROM words WHERE text_id = ? AND book IN ({marks})",
                         [text_id] + self.book_numbers)
            conn.execute(f"DELETE FROM verse_map WHERE text_id = ? AND book IN ({marks})",
                         [text_id] + self.book_numbers)
            conn.execute(f"DELETE FROM source_notes WHERE text_id = ? AND book IN ({marks})",
                         [text_id] + self.book_numbers)
            print(f"Replacing {existing} earlier word rows.")
        conn.execute("UPDATE texts SET origin = ?, license = ? WHERE text_id = ?",
                     (TEXT_ORIGIN, TEXT_LICENSE, text_id))
        return text_id

    def _store_lemmas(self, conn):
        """Make sure every lemma code exists, and return {key: lemma_id}."""
        ids = {(lang, lemma, strongs): lemma_id for lemma_id, lang, lemma, strongs
               in conn.execute("SELECT lemma_id, language, lemma, strongs FROM lemmas")}
        needed = {row[6] for row in self.part_rows if row[6]}
        for key in sorted(needed - set(ids)):
            language, lemma, strongs = key
            hint = PREFIX_HINTS.get(lemma) if strongs == "" else None
            cur = conn.execute(
                "INSERT INTO lemmas (language, lemma, strongs, gloss_hint) VALUES (?, ?, ?, ?)",
                (language, lemma, strongs, hint))
            ids[key] = cur.lastrowid
        return ids


def main():
    parser = argparse.ArgumentParser(description="Import the Open Scriptures Hebrew Bible")
    parser.add_argument("wlc_dir", help="the wlc folder of morphhb")
    parser.add_argument("source_db", help="path to source_texts.db")
    parser.add_argument("--books", nargs="+", metavar="BOOK",
                        help="import only these books, for example Ruth Jonah or Gen")
    parser.add_argument("--replace", action="store_true",
                        help="replace word rows imported earlier for these books")
    parser.add_argument("--dry-run", action="store_true",
                        help="read the files and print a report without writing anything")
    parser.add_argument("--log", metavar="FILE",
                        help="write every anomaly and guessed alignment to this text file")
    args = parser.parse_args()
    OshbImporter(args.wlc_dir, args.source_db, args.books, args.replace, args.dry_run,
                 args.log).run()


if __name__ == "__main__":
    main()
