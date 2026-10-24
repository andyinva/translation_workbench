#!/usr/bin/env python3
"""
log_decision.py  (place this file in translation_workbench/engine/)

Records a decision, with its reason, in the project database's `decisions`
table, so the reasoning can be revisited later. A decision belongs to a session
(see handoff.py) and may point to a glossary entry.

Usage:
    python3 log_decision.py add  --project PROJECT.db --session 3 \\
        --topic "Divine name" --summary "Render YHWH as Yahweh" \\
        --reason "Yahweh is the most literal rendering of the name"
    python3 log_decision.py list --project PROJECT.db [--topic divine]

A decision is never edited or deleted here. To change a decision, add a new one
on the same topic that says what changed. The list shows newest first, so the
current position is always at the top.
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def open_project(path):
    """Open the project database for reading and writing."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        sys.exit(f"Project database not found: {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def add_decision(conn, session_id, topic, summary, reason=None, glossary_id=None):
    """Insert one decision. The session must exist and still be open."""
    session = conn.execute("SELECT ended_at FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if session is None:
        sys.exit(f"No session {session_id}. Start one first with handoff.py start.")
    if session["ended_at"]:
        sys.exit(f"Session {session_id} is closed. Decisions are logged in an open session.")
    if glossary_id is not None and not conn.execute(
            "SELECT 1 FROM glossary WHERE glossary_id = ?", (glossary_id,)).fetchone():
        sys.exit(f"No glossary entry {glossary_id}.")
    cur = conn.execute(
        "INSERT INTO decisions (topic, summary, reason, glossary_id, session_id) VALUES (?, ?, ?, ?, ?)",
        (topic.strip(), summary.strip(), (reason or "").strip() or None, glossary_id, session_id))
    conn.commit()
    return cur.lastrowid


def list_decisions(conn, topic=None):
    """Return decisions, newest first, optionally only those whose topic contains the text."""
    sql = "SELECT * FROM decisions"
    params = []
    if topic:
        sql += " WHERE topic LIKE ?"
        params.append(f"%{topic}%")
    return conn.execute(sql + " ORDER BY decision_id DESC", params).fetchall()


def main():
    parser = argparse.ArgumentParser(description="Record and list project decisions")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="record a decision")
    add.add_argument("--project", required=True)
    add.add_argument("--session", required=True, type=int)
    add.add_argument("--topic", required=True)
    add.add_argument("--summary", required=True)
    add.add_argument("--reason")
    add.add_argument("--glossary-id", type=int)

    show = sub.add_parser("list", help="show decisions, newest first")
    show.add_argument("--project", required=True)
    show.add_argument("--topic", help="only topics containing this text")

    args = parser.parse_args()
    conn = open_project(args.project)
    if args.command == "add":
        new_id = add_decision(conn, args.session, args.topic, args.summary, args.reason, args.glossary_id)
        print(f"Decision {new_id} recorded.")
    else:
        for d in list_decisions(conn, args.topic):
            print(f"#{d['decision_id']} [{d['created_at']}] {d['topic']}: {d['summary']}")
            if d["reason"]:
                print(f"    Reason: {d['reason']}")


if __name__ == "__main__":
    main()
