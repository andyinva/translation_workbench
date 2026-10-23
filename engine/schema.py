#!/usr/bin/env python3
"""
schema.py  (place this file in translation_workbench/engine/)

Creates the SQLite databases for translation_workbench.

There are two databases, because they have different lifetimes:

  1. SOURCE database  (sources/source_texts.db)
     Original-language texts, tagged words and lemmas. It is shared by every
     translation project, so the lemma work is done only once.

  2. PROJECT database (projects/<name>/project.db)
     Everything created while translating into one target language:
     glossary decisions, drafts, flags, notes and session logs.

The project database never copies the other English translations. Those stay
in Bible Search Lite's bibles.db and are attached at run time, which keeps
copyrighted text out of this repository.

Usage:
    python schema.py source  ../sources/source_texts.db
    python schema.py project ../projects/english_literal/project.db \
        --language English --style literal
"""

import argparse
import sqlite3
from pathlib import Path

# Bump this number whenever a table changes, so old databases can be detected.
SCHEMA_VERSION = 2   # v2: word parts, verse_map, name/term fields, notes tables


class Schema:
    """Base class: holds a list of SQL statements and applies them to a file."""

    # Subclasses fill this with CREATE TABLE / INDEX / VIEW statements.
    STATEMENTS = []

    def __init__(self, db_path):
        self.db_path = Path(db_path).expanduser().resolve()

    def create(self):
        """Create the database file and every table. Safe to run twice."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            # Make SQLite enforce FOREIGN KEY rules (it ignores them by default).
            conn.execute("PRAGMA foreign_keys = ON")
            for sql in self.STATEMENTS:
                conn.execute(sql)
            self.after_create(conn)
            conn.commit()
        finally:
            conn.close()
        print(f"Database ready: {self.db_path}")

    def after_create(self, conn):
        """Hook for subclasses that need to insert starter rows."""


class SourceSchema(Schema):
    """Tables for the shared original-language data."""

    STATEMENTS = [
        # One row per source text, for example the Hebrew OT or the Greek NT.
        # The license column records where the text came from and its terms.
        """
        CREATE TABLE IF NOT EXISTS texts (
            text_id     INTEGER PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            language    TEXT NOT NULL,          -- 'hebrew', 'greek', 'aramaic'
            origin      TEXT,                   -- where the data was obtained
            license     TEXT,                   -- license or terms of use
            notes       TEXT
        )
        """,
        # One row per distinct lemma (dictionary form of a word).
        """
        CREATE TABLE IF NOT EXISTS lemmas (
            lemma_id        INTEGER PRIMARY KEY,
            language        TEXT NOT NULL,
            lemma           TEXT NOT NULL,
            strongs         TEXT,               -- Strong's number if available
            transliteration TEXT,
            gloss_hint      TEXT,               -- short lexicon gloss, a starting point only
            UNIQUE (language, lemma, strongs)
        )
        """,
        # One row per word in the source text, in reading order.
        """
        CREATE TABLE IF NOT EXISTS words (
            word_id   INTEGER PRIMARY KEY,
            text_id   INTEGER NOT NULL REFERENCES texts(text_id),
            book      INTEGER NOT NULL,         -- 1 to 66, standard Protestant order
            chapter   INTEGER NOT NULL,
            verse     INTEGER NOT NULL,
            position  INTEGER NOT NULL,         -- part number within the verse (every part has its own row)
            word_number INTEGER NOT NULL DEFAULT 0,  -- whole-word number within the verse
                                                -- (Hebrew prefixes such as "and" or "in" are
                                                -- separate parts that share one word_number)
            surface   TEXT NOT NULL,            -- the part as written in the text
            lemma_id  INTEGER REFERENCES lemmas(lemma_id),
            morph     TEXT,                     -- morphology code
            UNIQUE (text_id, book, chapter, verse, position)
        )
        """,
        # Versification map. Hebrew and English Bibles number some verses
        # differently (Psalm titles, a few verses in Genesis, Joel, Malachi and
        # others). This lets verse-level comparison with the English translations
        # in bibles.db line up the right verses.
        """
        CREATE TABLE IF NOT EXISTS verse_map (
            text_id      INTEGER NOT NULL REFERENCES texts(text_id),
            book         INTEGER NOT NULL,
            chapter      INTEGER NOT NULL,      -- numbering used by this source text
            verse        INTEGER NOT NULL,
            eng_chapter  INTEGER NOT NULL,      -- numbering used by English Bibles
            eng_verse    INTEGER NOT NULL,
            note         TEXT,                  -- for split or merged verses
            PRIMARY KEY (text_id, book, chapter, verse)
        )
        """,
        # Fast lookups by verse and by lemma (the "all 12 uses" query).
        "CREATE INDEX IF NOT EXISTS idx_words_ref   ON words(book, chapter, verse)",
        "CREATE INDEX IF NOT EXISTS idx_words_lemma ON words(lemma_id)",
        # A view that lists every occurrence of every lemma with its location.
        # There is no separate occurrences table, so the data cannot drift out of sync.
        """
        CREATE VIEW IF NOT EXISTS lemma_occurrences AS
        SELECT l.lemma_id, l.lemma, l.strongs,
               w.word_id, w.book, w.chapter, w.verse, w.position, w.surface
        FROM lemmas l
        JOIN words  w ON w.lemma_id = l.lemma_id
        """,
        # A view with the occurrence count for each lemma.
        """
        CREATE VIEW IF NOT EXISTS lemma_counts AS
        SELECT lemma_id, COUNT(*) AS occurrences
        FROM words
        WHERE lemma_id IS NOT NULL
        GROUP BY lemma_id
        """,
    ]


class ProjectSchema(Schema):
    """Tables for one translation project (one target language)."""

    STATEMENTS = [
        # Simple key and value settings: language, style profile, paths, version.
        """
        CREATE TABLE IF NOT EXISTS project_info (
            key    TEXT PRIMARY KEY,
            value  TEXT
        )
        """,
        # One row each time a working session starts. The role is what the
        # session was for: translation, quality, lexical, preparation, programming.
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id  INTEGER PRIMARY KEY,
            role        TEXT NOT NULL,
            started_at  TEXT NOT NULL DEFAULT (datetime('now')),
            ended_at    TEXT,                   -- set when the session closes properly
            scope       TEXT,                   -- what section or task it covered
            summary     TEXT                    -- handoff note for the next session
        )
        """,
        # Glossary: one row per lemma and sense, with the approved rendering.
        # lemma_id points into the source database (a different file), so it
        # cannot be a FOREIGN KEY here.
        """
        CREATE TABLE IF NOT EXISTS glossary (
            glossary_id       INTEGER PRIMARY KEY,
            lemma_id          INTEGER NOT NULL,
            sense_label       TEXT NOT NULL DEFAULT 'main',
            category          TEXT NOT NULL DEFAULT 'word',  -- word, name, divine_name, term
            tier              INTEGER,          -- for names: 1 always transliterate, 2 when the
                                                -- text explains the name, 3 use the common form
            rendering         TEXT NOT NULL,    -- the approved target-language rendering
            pronunciation     TEXT,             -- optional, for transliterated terms and names
            allowed_variants  TEXT,             -- other acceptable renderings, if any
            rule              TEXT,             -- when this sense applies
            reason            TEXT,             -- why this decision was made
            status            TEXT NOT NULL DEFAULT 'proposed',  -- proposed, approved, revised
            session_id        INTEGER REFERENCES sessions(session_id),
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (lemma_id, sense_label)
        )
        """,
        # Drafts: one row per verse per version. Old versions are never overwritten.
        """
        CREATE TABLE IF NOT EXISTS drafts (
            draft_id    INTEGER PRIMARY KEY,
            book        INTEGER NOT NULL,
            chapter     INTEGER NOT NULL,
            verse       INTEGER NOT NULL,
            version     INTEGER NOT NULL DEFAULT 1,
            text        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'draft',   -- draft, checked, frozen, legacy
            session_id  INTEGER REFERENCES sessions(session_id),
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (book, chapter, verse, version)
        )
        """,
        # Notes tied to one verse: lexical notes, textual variants, difficulties.
        # Earlier Gateway "lexicon_notes" are imported here.
        """
        CREATE TABLE IF NOT EXISTS verse_notes (
            note_id     INTEGER PRIMARY KEY,
            book        INTEGER NOT NULL,
            chapter     INTEGER NOT NULL,
            verse       INTEGER NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'lexical',   -- lexical, textual, difficulty, other
            note        TEXT NOT NULL,
            draft_id    INTEGER REFERENCES drafts(draft_id),
            session_id  INTEGER REFERENCES sessions(session_id),
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # Notes tied to a whole chapter, such as the earlier "translation differences".
        """
        CREATE TABLE IF NOT EXISTS chapter_notes (
            note_id     INTEGER PRIMARY KEY,
            book        INTEGER NOT NULL,
            chapter     INTEGER NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'differences',
            note        TEXT NOT NULL,
            session_id  INTEGER REFERENCES sessions(session_id),
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        # Alignment: which source word is rendered by which words in a draft.
        # This is what lets the checker compare each word to its glossary entry.
        """
        CREATE TABLE IF NOT EXISTS draft_alignment (
            alignment_id  INTEGER PRIMARY KEY,
            draft_id      INTEGER NOT NULL REFERENCES drafts(draft_id),
            word_id       INTEGER NOT NULL,     -- points into the source database
            glossary_id   INTEGER REFERENCES glossary(glossary_id),
            rendered_as   TEXT NOT NULL,        -- the words used in this draft
            departs       INTEGER NOT NULL DEFAULT 0,  -- 1 if it departs from the glossary
            departure_note TEXT                 -- required when departs = 1
        )
        """,
        # Flags raised by the quality session or the automated checkers.
        """
        CREATE TABLE IF NOT EXISTS flags (
            flag_id      INTEGER PRIMARY KEY,
            draft_id     INTEGER REFERENCES drafts(draft_id),
            glossary_id  INTEGER REFERENCES glossary(glossary_id),
            kind         TEXT NOT NULL,         -- inconsistent, divergent, unapproved, other
            description  TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'open',   -- open, resolved, accepted
            resolution   TEXT,
            session_id   INTEGER REFERENCES sessions(session_id),
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            resolved_at  TEXT
        )
        """,
        # Decision log: the reasoning behind choices, so it can be revisited later.
        """
        CREATE TABLE IF NOT EXISTS decisions (
            decision_id  INTEGER PRIMARY KEY,
            topic        TEXT NOT NULL,
            summary      TEXT NOT NULL,
            reason       TEXT,
            glossary_id  INTEGER REFERENCES glossary(glossary_id),
            session_id   INTEGER REFERENCES sessions(session_id),
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_drafts_ref  ON drafts(book, chapter, verse)",
        "CREATE INDEX IF NOT EXISTS idx_vnotes_ref  ON verse_notes(book, chapter, verse)",
        "CREATE INDEX IF NOT EXISTS idx_align_draft ON draft_alignment(draft_id)",
        "CREATE INDEX IF NOT EXISTS idx_align_word  ON draft_alignment(word_id)",
        "CREATE INDEX IF NOT EXISTS idx_flags_open  ON flags(status)",
    ]

    def __init__(self, db_path, language="English", style="literal"):
        super().__init__(db_path)
        self.language = language
        self.style = style

    def after_create(self, conn):
        """Store the starting settings. INSERT OR IGNORE keeps existing values."""
        starter = {
            "schema_version": str(SCHEMA_VERSION),
            "target_language": self.language,
            "style_profile": self.style,        # 'literal' or 'readable'
            "bibles_db_path": "",               # path to Bible Search Lite bibles.db
        }
        for key, value in starter.items():
            conn.execute(
                "INSERT OR IGNORE INTO project_info (key, value) VALUES (?, ?)",
                (key, value),
            )


def main():
    parser = argparse.ArgumentParser(description="Create translation_workbench databases")
    parser.add_argument("kind", choices=["source", "project"])
    parser.add_argument("db_path")
    parser.add_argument("--language", default="English")
    parser.add_argument("--style", default="literal", choices=["literal", "readable"])
    args = parser.parse_args()

    if args.kind == "source":
        SourceSchema(args.db_path).create()
    else:
        ProjectSchema(args.db_path, args.language, args.style).create()


if __name__ == "__main__":
    main()
