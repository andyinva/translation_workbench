#!/usr/bin/env python3
"""
import_legacy_gateway.py  (place this file in translation_workbench/engine/)

Imports the notes from the earlier Gateway Translation master JSON file into a
project database, as clearly labelled reference material.

What is imported (every note kind starts with "legacy_", so it can never be
mistaken for new work):
    lexicon_notes            -> verse_notes,   kind "legacy_lexical"
    fresh_translation        -> verse_notes,   kind "legacy_gateway"
    translation_differences  -> chapter_notes, kind "legacy_differences"
    chapter glossary         -> chapter_notes, kind "legacy_glossary"

What is deliberately NOT imported:
    fresh_translation is not stored as a draft, because it is a transliteration
    style text and the new project is plain literal English.
    word_table is not imported, because the tagged source text replaces it.

Usage:
    python3 import_legacy_gateway.py MASTER.json PROJECT.db
    python3 import_legacy_gateway.py MASTER.json PROJECT.db --replace

The importer refuses to run a second time unless --replace is given. With
--replace it removes only the earlier legacy_ notes, then imports again.
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

# books.py sits next to this file, whether run as a script or as part of a package.
try:
    from books import book_number
except ImportError:
    from engine.books import book_number

# The note kinds written by this importer.
KIND_LEXICAL = "legacy_lexical"
KIND_GATEWAY = "legacy_gateway"
KIND_DIFFERENCES = "legacy_differences"
KIND_GLOSSARY = "legacy_glossary"

# Needed to find legacy notes with SQL LIKE ("_" is a wildcard unless escaped).
LEGACY_PATTERN = "legacy\\_%"


class LegacyGatewayImporter:
    """Loads one master JSON file into one project database."""

    REQUIRED_SCHEMA_VERSION = "2"

    def __init__(self, json_path, db_path, replace=False):
        self.json_path = Path(json_path).expanduser().resolve()
        self.db_path = Path(db_path).expanduser().resolve()
        self.replace = replace
        self.warnings = []          # problems found while importing (shown at the end)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def run(self):
        """Do the whole import inside one transaction (all or nothing)."""
        if not self.json_path.is_file():
            sys.exit(f"JSON file not found: {self.json_path}")
        if not self.db_path.is_file():
            sys.exit(f"Project database not found: {self.db_path}\n"
                     "Create it first with schema.py.")

        print(f"Reading {self.json_path.name} ...")
        with open(self.json_path, encoding="utf-8") as f:
            data = json.load(f)

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._check_schema(conn)
            self._handle_existing(conn)
            session_id = self._start_session(conn)
            counts = self._import_chapters(conn, data, session_id)
            self._finish_session(conn, session_id, data, counts)
            conn.commit()                  # nothing is saved until everything worked
        except BaseException:
            conn.rollback()                # any error leaves the database untouched
            raise
        finally:
            conn.close()

        self._report(counts)

    # ------------------------------------------------------------------
    # Checks before importing
    # ------------------------------------------------------------------
    def _check_schema(self, conn):
        """Make sure this is a version 2 project database."""
        try:
            row = conn.execute(
                "SELECT value FROM project_info WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            sys.exit("This does not look like a project database (no project_info table).")
        version = row[0] if row else None
        if version != self.REQUIRED_SCHEMA_VERSION:
            sys.exit(f"Project database is schema version {version}; "
                     f"version {self.REQUIRED_SCHEMA_VERSION} is required. "
                     "Recreate it with the current schema.py.")

    def _handle_existing(self, conn):
        """Refuse to import twice, or remove the old legacy notes with --replace."""
        sql_v = "FROM verse_notes WHERE kind LIKE ? ESCAPE '\\'"
        sql_c = "FROM chapter_notes WHERE kind LIKE ? ESCAPE '\\'"
        existing = (conn.execute("SELECT COUNT(*) " + sql_v, (LEGACY_PATTERN,)).fetchone()[0]
                    + conn.execute("SELECT COUNT(*) " + sql_c, (LEGACY_PATTERN,)).fetchone()[0])
        if existing == 0:
            return
        if not self.replace:
            sys.exit(f"{existing} legacy notes are already in this database. "
                     "Run again with --replace to replace them.")
        conn.execute("DELETE " + sql_v, (LEGACY_PATTERN,))
        conn.execute("DELETE " + sql_c, (LEGACY_PATTERN,))
        print(f"Removed {existing} earlier legacy notes.")

    # ------------------------------------------------------------------
    # Session record
    # ------------------------------------------------------------------
    def _start_session(self, conn):
        """Record this import as a preparation session."""
        cur = conn.execute(
            "INSERT INTO sessions (role, scope) VALUES ('preparation', ?)",
            ("Import legacy Gateway notes from " + self.json_path.name,),
        )
        return cur.lastrowid

    def _finish_session(self, conn, session_id, data, counts):
        """Close the session with a summary, and remember where the data came from."""
        source = (f"{self.json_path.name} (version {data.get('version')}, "
                  f"last modified {data.get('last_modified')})")
        summary = (f"Imported {counts['verse_notes']} verse notes and "
                   f"{counts['chapter_notes']} chapter notes from {source}. "
                   f"Chapters: {counts['chapters']}. Warnings: {len(self.warnings)}.")
        conn.execute(
            "UPDATE sessions SET ended_at = datetime('now'), summary = ? WHERE session_id = ?",
            (summary, session_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO project_info (key, value) VALUES ('legacy_gateway_source', ?)",
            (source,),
        )

    # ------------------------------------------------------------------
    # The import itself
    # ------------------------------------------------------------------
    def _import_chapters(self, conn, data, session_id):
        counts = {"chapters": 0, "verse_notes": 0, "chapter_notes": 0}

        for ch in data.get("chapters", []):
            # An unknown book name stops the import, and everything is rolled back.
            try:
                book = book_number(ch["book"])
            except KeyError:
                sys.exit(f"Unknown book name {ch.get('book')!r} (chapter {ch.get('chapter')}). "
                         "Add that spelling to ALIASES in books.py and run again. "
                         "Nothing was imported.")
            chapter = int(ch["chapter"])
            counts["chapters"] += 1

            # Chapter-level notes: the glossary and the translation differences.
            glossary_text = self._format_glossary(ch.get("glossary", []))
            self._add_chapter_note(conn, counts, book, chapter, KIND_GLOSSARY,
                                   glossary_text, session_id)
            self._add_chapter_note(conn, counts, book, chapter, KIND_DIFFERENCES,
                                   self._to_text(ch.get("translation_differences")),
                                   session_id)

            # Verse-level notes: the lexicon notes and the Gateway text.
            for verse in ch.get("verses", []):
                verse_no = self._parse_verse(verse.get("ref", ""), book, chapter)
                if verse_no is None:
                    continue
                self._add_verse_note(conn, counts, book, chapter, verse_no, KIND_LEXICAL,
                                     verse.get("lexicon_notes"), session_id)
                self._add_verse_note(conn, counts, book, chapter, verse_no, KIND_GATEWAY,
                                     verse.get("fresh_translation"), session_id)
        return counts

    def _add_verse_note(self, conn, counts, book, chapter, verse, kind, text, session_id):
        """Insert one verse note unless the text is empty."""
        text = self._to_text(text)
        if not text:
            return
        conn.execute(
            "INSERT INTO verse_notes (book, chapter, verse, kind, note, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (book, chapter, verse, kind, text, session_id),
        )
        counts["verse_notes"] += 1

    def _add_chapter_note(self, conn, counts, book, chapter, kind, text, session_id):
        """Insert one chapter note unless the text is empty."""
        if not text:
            return
        conn.execute(
            "INSERT INTO chapter_notes (book, chapter, kind, note, session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (book, chapter, kind, text, session_id),
        )
        counts["chapter_notes"] += 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _parse_verse(self, ref, book, chapter):
        """Turn a reference such as "22:2" into the verse number 2."""
        match = re.search(r":\s*(\d+)", str(ref))
        if match is None and str(ref).strip().isdigit():
            match = re.match(r"\s*(\d+)", str(ref))
        if match is None:
            if len(self.warnings) < 20:    # keep the list short
                self.warnings.append(f"Book {book} chapter {chapter}: cannot read verse ref {ref!r}")
            return None
        return int(match.group(1))

    def _format_glossary(self, entries):
        """Turn the chapter glossary list into readable lines."""
        lines = []
        for e in entries or []:
            lines.append("{} ({}, {}): {}".format(
                e.get("transliteration", ""), e.get("hebrew", ""),
                e.get("pronunciation", ""), e.get("meaning", "")))
        return "\n".join(lines)

    def _to_text(self, value):
        """Turn a string, list or dictionary from the JSON into plain text."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            parts = [self._to_text(v) for v in value]
            return "\n".join(p for p in parts if p)
        if isinstance(value, dict):
            return "\n".join(f"{k}: {self._to_text(v)}" for k, v in value.items())
        return str(value)

    def _report(self, counts):
        print("Import finished.")
        print(f"  chapters read:  {counts['chapters']}")
        print(f"  verse notes:    {counts['verse_notes']}")
        print(f"  chapter notes:  {counts['chapter_notes']}")
        print(f"  warnings:       {len(self.warnings)}")
        for w in self.warnings:
            print("   - " + w)


def main():
    parser = argparse.ArgumentParser(description="Import legacy Gateway notes into a project database")
    parser.add_argument("json_file", help="gateway_translation_master_N.json")
    parser.add_argument("project_db", help="path to the project.db file")
    parser.add_argument("--replace", action="store_true",
                        help="replace legacy notes imported earlier")
    args = parser.parse_args()
    LegacyGatewayImporter(args.json_file, args.project_db, args.replace).run()


if __name__ == "__main__":
    main()
