#!/usr/bin/env python3
"""
panel_data.py  (place this file in translation_workbench/engine/)

The data side of the control panel. It has no windows in it, so it can be
tested without a screen. Two classes:

    ReviewStore    reads the glossary entries that wait for a decision and
                   carries out the decisions (approve, revise, reject) through
                   the same tools the command line uses (glossary.py and
                   handoff.py), so every rule and every log entry still applies.

    NotesChannel   two small JSON files in the repo folder "control":
                       user_notes.json    written by the panel (your decisions and comments)
                       claude_notes.json  written by Claude (comments for you)
                   This is how Claude and the panel talk to each other: Claude
                   reads the first file and writes the second. Neither side
                   touches the databases through these files.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime

from books import book_name
from glossary import GlossaryTool
from handoff import SessionManager


class PanelError(Exception):
    """A problem to show to the user in a message box (not a crash)."""


def _now():
    """Current time as text, to the minute."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class NotesChannel:
    """Reads and writes the two notes files in the control folder."""

    def __init__(self, control_dir):
        self.control_dir = control_dir
        os.makedirs(control_dir, exist_ok=True)
        self.user_file = os.path.join(control_dir, "user_notes.json")
        self.claude_file = os.path.join(control_dir, "claude_notes.json")

    @staticmethod
    def _read(path):
        """Return the list stored in a notes file (an empty list if the file is missing or unreadable)."""
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    @staticmethod
    def _write(path, notes):
        """Write the list safely: to a temporary file first, then swap it in."""
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(notes, handle, ensure_ascii=False, indent=1)
        os.replace(temp, path)

    def add_user_note(self, lemma_id, sense, action, text, before="", after=""):
        """Record one decision or comment from the panel."""
        notes = self._read(self.user_file)
        notes.append({
            "id": (max([n.get("id", 0) for n in notes]) + 1) if notes else 1,
            "time": _now(), "lemma_id": lemma_id, "sense": sense, "action": action,
            "text": text.strip(), "before": before, "after": after,
        })
        self._write(self.user_file, notes)

    def claude_notes_for(self, lemma_id, sense=None):
        """Claude's notes about one lemma (and one sense, or all senses when sense is None)."""
        found = []
        for note in self._read(self.claude_file):
            if note.get("lemma_id") != lemma_id:
                continue
            if sense is not None and note.get("sense") not in (None, "", sense):
                continue
            found.append(note)
        return found

    def claude_lemma_ids(self):
        """The lemma ids that have at least one note from Claude."""
        return {n.get("lemma_id") for n in self._read(self.claude_file)}


class ReviewStore:
    """Entries waiting for review, and the actions that settle them."""

    def __init__(self, project_path, source_path):
        self.project_path = project_path
        self.source_path = source_path
        for path in (project_path, source_path):
            if not os.path.isfile(path):
                raise PanelError(f"File not found: {path}")
        self.conn = sqlite3.connect(project_path)
        self.conn.row_factory = sqlite3.Row
        # The source database is attached read only: the panel can look but never change it.
        self.conn.execute("ATTACH DATABASE ? AS src",
                          ("file:" + os.path.abspath(source_path) + "?mode=ro",))
        # Only create the tool objects when a decision is made (they open their own connections).
        self._glossary = None

    # ---- sessions ----

    def lexical_sessions(self):
        """Open lexical sessions, newest first."""
        return self.conn.execute(
            "SELECT session_id, scope, started_at FROM sessions "
            "WHERE role = 'lexical' AND ended_at IS NULL ORDER BY session_id DESC").fetchall()

    def start_lexical_session(self, scope):
        """Open a new lexical session and return its number."""
        if not scope.strip():
            raise PanelError("A session needs a scope, for example: Batch 8 review.")
        manager = SessionManager(self.project_path, self.source_path)
        session_id, _briefing = manager.start("lexical", scope.strip())
        manager.conn.close()
        return session_id

    # ---- reading entries ----

    def _has_priority(self):
        """True if the word_priority table has been built."""
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'word_priority'").fetchone()
        return row is not None

    def proposed(self):
        """All glossary entries with status 'proposed', most influential words first."""
        rank_sql = "p.rank_most" if self._has_priority() else "NULL"
        join_sql = "LEFT JOIN word_priority p ON p.lemma_id = g.lemma_id" if self._has_priority() else ""
        sql = f"""
            SELECT g.*, l.headword, l.transliteration, l.gloss_hint, l.strongs,
                   {rank_sql} AS rank_most,
                   (SELECT occurrences FROM src.lemma_counts c WHERE c.lemma_id = g.lemma_id) AS uses
            FROM glossary g
            JOIN src.lemmas l ON l.lemma_id = g.lemma_id
            {join_sql}
            WHERE g.status = 'proposed'
            ORDER BY (rank_most IS NULL), rank_most, g.lemma_id, g.sense_label"""
        return self.conn.execute(sql).fetchall()

    def entry(self, lemma_id, sense):
        """One glossary row, or None."""
        return self.conn.execute(
            "SELECT * FROM glossary WHERE lemma_id = ? AND sense_label = ?", (lemma_id, sense)).fetchone()

    def other_senses(self, lemma_id, sense):
        """The other glossary entries of the same lemma."""
        return self.conn.execute(
            "SELECT sense_label, rendering, status FROM glossary "
            "WHERE lemma_id = ? AND sense_label <> ? ORDER BY sense_label", (lemma_id, sense)).fetchall()

    def examples(self, lemma_id, limit=10):
        """Some places where the lemma occurs: (reference, Hebrew form). One per verse."""
        rows = self.conn.execute(
            "SELECT book, chapter, verse, surface FROM src.words WHERE lemma_id = ? "
            "GROUP BY book, chapter, verse ORDER BY book, chapter, verse LIMIT ?", (lemma_id, limit))
        return [(f"{book_name(r['book'])} {r['chapter']}:{r['verse']}", r["surface"]) for r in rows]

    # ---- deciding ----

    def _tool(self):
        """The glossary tool, created on first use."""
        if self._glossary is None:
            self._glossary = GlossaryTool(self.project_path, self.source_path)
        return self._glossary

    def _call(self, function, *args, **kwargs):
        """Run a glossary call. The tools stop with sys.exit(message) on a problem; show that message instead."""
        try:
            return function(*args, **kwargs)
        except SystemExit as stop:
            raise PanelError(str(stop.code or "The action was refused."))

    def approve(self, session_id, lemma_id, sense, fields):
        """Approve one entry. `fields` holds the (possibly edited) values from the panel.

        Returns "approved" if nothing was changed, or "revised" if the wording, the allowed
        variants, the rule, the category or the tier were edited.
        """
        current = self.entry(lemma_id, sense)
        if current is None:
            raise PanelError("That entry no longer exists. Reload the list.")
        if not (fields["reason"] or "").strip():
            raise PanelError("Write a reason before approving. It is kept so the choice can be revisited.")
        if not fields["rendering"].strip():
            raise PanelError("The rendering cannot be empty. Use (none) for a word with no English.")
        changed = (
            fields["rendering"].strip() != current["rendering"]
            or (fields["allowed"] or "") != (current["allowed_variants"] or "")
            or (fields["rule"] or "") != (current["rule"] or "")
            or fields["category"] != current["category"]
            or fields["tier"] != current["tier"])
        status = "revised" if changed else "approved"
        self._call(self._tool().set_entry, session_id, lemma_id, fields["rendering"], sense,
                   fields["category"], fields["tier"], current["pronunciation"],
                   fields["allowed"] or None, fields["rule"] or None, fields["reason"].strip(), status)
        return status

    def reject(self, session_id, lemma_id, sense, reason):
        """Reject an entry: it is removed from the glossary and the removal is logged with the reason."""
        self._call(self._tool().remove_entry, session_id, lemma_id, sense, reason)
