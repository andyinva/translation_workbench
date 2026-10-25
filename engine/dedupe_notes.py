"""Removes exact duplicate verse notes and exact duplicate decisions from a project database.

A duplicate is a row with the same text as an earlier row (same verse and kind for notes,
same topic and summary for decisions). The earliest row is kept. Without --apply the
script only reports what it would remove. With --apply it first copies the database to
a backup file next to it, then deletes the duplicates.

Usage:
    python3 dedupe_notes.py --project ../projects/english_literal/project.db
    python3 dedupe_notes.py --project ../projects/english_literal/project.db --apply
"""
import argparse
import shutil
import sqlite3
from datetime import datetime


def find_duplicates(conn):
    """Return (note_ids, decision_ids) of rows to remove."""
    notes, seen = [], set()
    for row in conn.execute("SELECT note_id, book, chapter, verse, kind, note FROM verse_notes ORDER BY note_id"):
        key = row[1:]
        if key in seen:
            notes.append(row[0])
        seen.add(key)
    decisions, seen = [], set()
    for row in conn.execute("SELECT decision_id, topic, summary FROM decisions ORDER BY decision_id"):
        key = row[1:]
        if key in seen:
            decisions.append(row[0])
        seen.add(key)
    return notes, decisions


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="path to project.db")
    parser.add_argument("--apply", action="store_true", help="really delete (default is a report only)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.project)
    notes, decisions = find_duplicates(conn)
    print(f"Duplicate verse notes: {notes or 'none'}")
    print(f"Duplicate decisions:   {decisions or 'none'}")
    if not args.apply:
        print("Report only. Add --apply to remove them (a backup is made first).")
        return
    if not notes and not decisions:
        return
    backup = args.project + ".before_dedupe_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(args.project, backup)
    with conn:
        conn.executemany("DELETE FROM verse_notes WHERE note_id = ?", [(n,) for n in notes])
        conn.executemany("DELETE FROM decisions WHERE decision_id = ?", [(d,) for d in decisions])
    print(f"Removed {len(notes)} note(s) and {len(decisions)} decision(s). Backup: {backup}")


if __name__ == "__main__":
    main()
