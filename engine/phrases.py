#!/usr/bin/env python3
"""
phrases.py  (place this file in translation_workbench/engine/)

Phrase entries for the glossary. A phrase is a short run of source words that
must be written together in the translation, for example:

    Yom Kippur       yom (sense "in festival names") + kippurim (sense "yom kippur")
    Yom Teruah       yom (sense "in festival names") + teruah
    tohu va vohu     tohu + vohu (the conjunction between them is part of the phrase)

Why it exists: the glossary holds one entry per word. A phrase rule says that when
these words stand next to each other in the source, the draft must contain the
phrase text as one unit. draft.py uses this file in two places:
    * the verse sheet lists the phrases found in the verse
    * `draft.py add` refuses a draft in which a found phrase is missing or split,
      unless every member word is marked as a departure with a note

Matching rule: the member lemmas must follow each other in the verse. Only parts
without a Strong's number (prefixes such as the article, conjunctions and
suffixes) may stand between them.

Usage:
    python3 phrases.py add     --project P.db --session 10 --name "Yom Kippur" \\
        --rendering "Yom Kippur" --members "3111:in festival names,3790:yom kippur" \\
        --reason "Phrases stay together"
    python3 phrases.py approve --project P.db --session 10 --name "Yom Kippur" --reason "..."
    python3 phrases.py list    --project P.db
    python3 phrases.py verse   --project P.db --source S.db --book Leviticus --chapter 23 --verse 27
"""

import argparse
import sqlite3
import sys
from pathlib import Path

try:
    from books import book_name, book_number
except ImportError:
    from engine.books import book_name, book_number

# Phrase statuses that are enforced by the draft tool.
ESTABLISHED = ("approved", "revised")

CREATE_STATEMENTS = [
    # One row per phrase.
    """
    CREATE TABLE IF NOT EXISTS glossary_phrases (
        phrase_id   INTEGER PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,      -- label shown to people, for example Yom Kippur
        rendering   TEXT NOT NULL,             -- the text that must appear in a draft as one unit
        rule        TEXT,
        reason      TEXT,
        status      TEXT NOT NULL DEFAULT 'proposed',   -- proposed, approved
        session_id  INTEGER REFERENCES sessions(session_id),
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # The words of a phrase, in source order, each tied to a glossary sense.
    """
    CREATE TABLE IF NOT EXISTS phrase_members (
        phrase_id   INTEGER NOT NULL REFERENCES glossary_phrases(phrase_id),
        seq         INTEGER NOT NULL,          -- 1, 2, 3 in source order
        lemma_id    INTEGER NOT NULL,
        sense_label TEXT NOT NULL DEFAULT 'main',
        PRIMARY KEY (phrase_id, seq)
    )
    """,
]


def norm(text):
    """Compare text without caring about case or extra spaces."""
    return " ".join((text or "").lower().split())


def tables_exist(conn):
    """True if the phrase tables are in the project database."""
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'glossary_phrases'").fetchone()
    return row is not None


def load_phrases(conn, only_established=False):
    """Return a list of phrases, each a dict with its members: [{'phrase_id', 'name', ..., 'members': [(lemma, sense)]}]."""
    if not tables_exist(conn):
        return []
    sql = "SELECT * FROM glossary_phrases"
    if only_established:
        sql += " WHERE status IN ('approved', 'revised')"
    found = []
    for row in conn.execute(sql + " ORDER BY name").fetchall():
        members = conn.execute(
            "SELECT lemma_id, sense_label FROM phrase_members WHERE phrase_id = ? ORDER BY seq",
            (row["phrase_id"],)).fetchall()
        found.append({"phrase_id": row["phrase_id"], "name": row["name"], "rendering": row["rendering"],
                      "status": row["status"], "rule": row["rule"],
                      "members": [(m["lemma_id"], m["sense_label"]) for m in members]})
    return found


def find_matches(conn, words, only_established=False):
    """Find the phrases that occur in a verse.

    `words` are the verse parts in reading order (rows with position, lemma_id, strongs).
    Returns a list of dicts: the phrase plus `positions`, the source position of each member.
    """
    matches = []
    count = len(words)
    for phrase in load_phrases(conn, only_established):
        first_lemma = phrase["members"][0][0]
        for start in range(count):
            if words[start]["lemma_id"] != first_lemma:
                continue
            positions = [words[start]["position"]]
            index = start
            for lemma_id, _sense in phrase["members"][1:]:
                index += 1
                # Skip prefixes, conjunctions and suffixes: parts without a Strong's number.
                while index < count and not words[index]["strongs"]:
                    index += 1
                if index >= count or words[index]["lemma_id"] != lemma_id:
                    positions = None
                    break
                positions.append(words[index]["position"])
            if positions:
                matches.append(dict(phrase, positions=positions))
    return matches


class PhraseTool:
    """Adds, approves and lists phrases."""

    def __init__(self, project_path, source_path=None):
        self.project = self._open(project_path, read_only=False)
        self.source = self._open(source_path, read_only=True) if source_path else None
        for statement in CREATE_STATEMENTS:
            self.project.execute(statement)
        self.project.commit()

    @staticmethod
    def _open(path, read_only):
        """Open a database; the source is read only."""
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            sys.exit(f"Database not found: {path}")
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) if read_only else sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    def _lexical_session(self, session_id):
        """Only an open lexical session may change phrases."""
        session = self.project.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if session is None:
            sys.exit(f"No session {session_id}.")
        if session["role"] != "lexical":
            sys.exit(f"Session {session_id} is a {session['role']} session. Only a lexical session may change phrases.")
        if session["ended_at"]:
            sys.exit(f"Session {session_id} is closed.")

    def add(self, session_id, name, rendering, members_text, rule=None, reason=None):
        """Add or update a phrase. Members look like '3111:in festival names,3790:yom kippur'."""
        self._lexical_session(session_id)
        if not name.strip() or not rendering.strip():
            sys.exit("A phrase needs a name and a rendering.")
        members = []
        for part in members_text.split(","):
            lemma_text, _, sense = part.strip().partition(":")
            try:
                lemma_id = int(lemma_text)
            except ValueError:
                sys.exit(f"Member '{part}' must look like lemma_id:sense")
            sense = sense.strip() or "main"
            if self.project.execute("SELECT 1 FROM glossary WHERE lemma_id = ? AND sense_label = ?",
                                    (lemma_id, sense)).fetchone() is None:
                sys.exit(f"Lemma {lemma_id} has no glossary sense '{sense}'. Add that entry first.")
            members.append((lemma_id, sense))
        if len(members) < 2:
            sys.exit("A phrase needs at least two member words.")
        existing = self.project.execute("SELECT * FROM glossary_phrases WHERE name = ?", (name,)).fetchone()
        with self.project:
            if existing is None:
                cur = self.project.execute(
                    "INSERT INTO glossary_phrases (name, rendering, rule, reason, session_id) VALUES (?,?,?,?,?)",
                    (name, rendering.strip(), rule, reason, session_id))
                phrase_id = cur.lastrowid
                verb = "Added"
            else:
                phrase_id = existing["phrase_id"]
                self.project.execute(
                    "UPDATE glossary_phrases SET rendering = ?, rule = ?, reason = ?, status = 'proposed', "
                    "session_id = ?, updated_at = datetime('now') WHERE phrase_id = ?",
                    (rendering.strip(), rule, reason, session_id, phrase_id))
                self.project.execute("DELETE FROM phrase_members WHERE phrase_id = ?", (phrase_id,))
                verb = "Updated"
            for seq, (lemma_id, sense) in enumerate(members, start=1):
                self.project.execute(
                    "INSERT INTO phrase_members (phrase_id, seq, lemma_id, sense_label) VALUES (?,?,?,?)",
                    (phrase_id, seq, lemma_id, sense))
            self.project.execute(
                "INSERT INTO decisions (topic, summary, reason, session_id) VALUES (?,?,?,?)",
                (f"Phrase: {name}", f"{verb} phrase '{name}' = '{rendering.strip()}' [proposed]", reason, session_id))
        return f"{verb} phrase {name}: {rendering.strip()} [proposed]\n"

    def approve(self, session_id, name, reason):
        """Approve a phrase. A reason is required, as for glossary entries."""
        self._lexical_session(session_id)
        if not (reason or "").strip():
            sys.exit("Approving a phrase needs a reason (--reason).")
        row = self.project.execute("SELECT * FROM glossary_phrases WHERE name = ?", (name,)).fetchone()
        if row is None:
            sys.exit(f"No phrase named '{name}'.")
        with self.project:
            self.project.execute("UPDATE glossary_phrases SET status = 'approved', reason = ?, "
                                 "updated_at = datetime('now') WHERE phrase_id = ?", (reason.strip(), row["phrase_id"]))
            self.project.execute("INSERT INTO decisions (topic, summary, reason, session_id) VALUES (?,?,?,?)",
                                 (f"Phrase: {name}", f"Approved phrase '{name}' = '{row['rendering']}'",
                                  reason.strip(), session_id))
        return f"Approved phrase {name}.\n"

    def list_all(self):
        """All phrases as text."""
        out = []
        for p in load_phrases(self.project):
            members = " + ".join(f"{lemma} [{sense}]" for lemma, sense in p["members"])
            out.append(f"{p['name']:<16} = {p['rendering']:<16} [{p['status']}]  {members}")
            if p["rule"]:
                out.append(f"    Rule: {p['rule']}")
        return "\n".join(out) + "\n" if out else "No phrases yet.\n"

    def verse(self, book, chapter, verse):
        """The phrases found in one verse (needs the source database)."""
        if self.source is None:
            sys.exit("The verse command needs --source.")
        text_id = self.source.execute("SELECT text_id FROM texts WHERE name = ?",
                                      ("OSHB" if book <= 39 else "TAGNT",)).fetchone()["text_id"]
        words = self.source.execute(
            "SELECT w.position, w.lemma_id, l.strongs FROM words w LEFT JOIN lemmas l ON l.lemma_id = w.lemma_id "
            "WHERE w.text_id = ? AND w.book = ? AND w.chapter = ? AND w.verse = ? ORDER BY w.position",
            (text_id, book, chapter, verse)).fetchall()
        found = find_matches(self.project, words)
        if not found:
            return f"No phrase in {book_name(book)} {chapter}:{verse}.\n"
        return "".join(f"{book_name(book)} {chapter}:{verse}: {m['name']} -> {m['rendering']} "
                       f"(positions {m['positions']}) [{m['status']}]\n" for m in found)


def main():
    parser = argparse.ArgumentParser(description="Phrase entries for the glossary")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, need_session=False):
        p.add_argument("--project", required=True)
        if need_session:
            p.add_argument("--session", required=True, type=int, help="an open lexical session")

    add = sub.add_parser("add", help="add or update a phrase")
    common(add, True)
    add.add_argument("--name", required=True)
    add.add_argument("--rendering", required=True)
    add.add_argument("--members", required=True, help="lemma_id:sense,lemma_id:sense")
    add.add_argument("--rule")
    add.add_argument("--reason")

    approve = sub.add_parser("approve", help="approve a phrase")
    common(approve, True)
    approve.add_argument("--name", required=True)
    approve.add_argument("--reason", required=True)

    lst = sub.add_parser("list", help="list all phrases")
    common(lst)

    verse = sub.add_parser("verse", help="show the phrases found in a verse")
    common(verse)
    verse.add_argument("--source", required=True)
    verse.add_argument("--book", required=True)
    verse.add_argument("--chapter", required=True, type=int)
    verse.add_argument("--verse", required=True, type=int)

    args = parser.parse_args()
    tool = PhraseTool(args.project, getattr(args, "source", None))
    if args.command == "add":
        print(tool.add(args.session, args.name, args.rendering, args.members, args.rule, args.reason), end="")
    elif args.command == "approve":
        print(tool.approve(args.session, args.name, args.reason), end="")
    elif args.command == "list":
        print(tool.list_all(), end="")
    else:
        print(tool.verse(book_number(args.book), args.chapter, args.verse), end="")


if __name__ == "__main__":
    main()
