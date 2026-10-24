#!/usr/bin/env python3
"""
handoff.py  (place this file in translation_workbench/engine/)

Starts and ends working sessions, following docs/session_roles.md.

Sessions never share memory. They share only the databases, so every session
begins by reading a briefing made from the project database, and ends by
leaving a handoff summary in it.

Commands:
    start   open a session and print the briefing
    end     run the checks, save the handoff summary, close the session, back up
    status  show open sessions, recent sessions and backups

Examples:
    python3 handoff.py start --project ../projects/english_literal/project.db \\
        --role lexical --scope "Lemmas of Genesis 1 to 3" \\
        --source ../sources/source_texts.db --book Genesis --chapters 1 3
    python3 handoff.py end --project ../projects/english_literal/project.db \\
        --session 7 --summary "Decided bara, asah. Open: yatsar. Next: Genesis 2."
    python3 handoff.py status --project ../projects/english_literal/project.db

The project database is only ever changed in the ways the protocol describes
(a new sessions row, the summary and end time). Backups go in a "backups"
folder next to the project database and are never overwritten.
"""

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# books.py sits next to this file, whether run as a script or as part of a package.
try:
    from books import book_name, book_number
except ImportError:
    from engine.books import book_name, book_number

# The five roles from docs/session_roles.md.
ROLES = ["preparation", "lexical", "translation", "quality", "programming"]

# Roles that must end with zero unexplained issues (unless --force is given).
STRICT_ROLES = {"translation", "quality"}

BACKUP_MAX_AGE_HOURS = 24       # a backup older than this is reported as stale
BACKUP_KEEP = 20                # how many dated backups to keep


class SessionManager:
    """Opens and closes sessions in one project database."""

    def __init__(self, project_path, source_path=None):
        self.project_path = Path(project_path).expanduser().resolve()
        if not self.project_path.is_file():
            sys.exit(f"Project database not found: {self.project_path}")
        self.source_path = Path(source_path).expanduser().resolve() if source_path else None
        self.backup_dir = self.project_path.parent / "backups"
        self.conn = sqlite3.connect(self.project_path)
        self.conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------
    def start(self, role, scope, book=None, first_chapter=None, last_chapter=None):
        """Open a session and return (session_id, briefing text)."""
        if role not in ROLES:
            sys.exit("Role must be one of: " + ", ".join(ROLES))
        if not scope.strip():
            sys.exit("A session needs a scope, for example --scope \"Genesis 1 to 3\".")

        lines = []
        # Sessions left open by an earlier run are reported, never closed silently.
        stale = self.conn.execute(
            "SELECT session_id, role, scope, started_at FROM sessions WHERE ended_at IS NULL").fetchall()

        cur = self.conn.execute("INSERT INTO sessions (role, scope) VALUES (?, ?)", (role, scope))
        session_id = cur.lastrowid
        self.conn.commit()

        lines.append(f"# Session {session_id}: {role}")
        lines.append(f"Scope: {scope}")
        lines.append(f"Started: {datetime.now():%Y-%m-%d %H:%M}")
        lines.append("")
        lines += self._project_lines()
        lines += self._warning_lines(stale, session_id)
        lines += self._last_summaries(role, session_id)
        lines += self._counts_lines()
        if book:
            lines += self._section_lines(book, first_chapter, last_chapter)
        lines += self._role_rules(role)
        return session_id, "\n".join(lines) + "\n"

    def _project_lines(self):
        info = dict(self.conn.execute("SELECT key, value FROM project_info").fetchall())
        lines = ["## Project",
                 f"- Target language: {info.get('target_language')}",
                 f"- Style profile: {info.get('style_profile')}"]
        rules = self._find_style_rules()
        if rules:
            lines.append(f"- Style rules: {rules}  (read this file before working)")
        else:
            lines.append("- Style rules: none found. Write style_rules.md before translating.")
        lines.append("")
        return lines

    def _find_style_rules(self):
        """Look for style_rules.md in the project folder, then in the repository root."""
        for folder in (self.project_path.parent, self.project_path.parent.parent.parent):
            candidate = folder / "style_rules.md"
            if candidate.is_file():
                return candidate
        return None

    def _warning_lines(self, stale, this_session):
        lines = []
        # A session that never ended did not run its closing checks.
        others = [s for s in stale]
        if others:
            lines.append("## Warnings")
            for s in others:
                lines.append(f"- Session {s['session_id']} ({s['role']}, started {s['started_at']}) "
                             f"was never closed: {s['scope']}. Its work was not checked or summarised.")
        backup = self.latest_backup()
        if backup is None:
            lines += ([] if lines else ["## Warnings"]) + ["- No backup exists yet. Run: handoff.py backup"]
        elif datetime.now() - backup[1] > timedelta(hours=BACKUP_MAX_AGE_HOURS):
            lines += ([] if lines else ["## Warnings"]) + [
                f"- The latest backup is {backup[0].name} from {backup[1]:%Y-%m-%d %H:%M}, "
                f"more than {BACKUP_MAX_AGE_HOURS} hours old."]
        if lines:
            lines.append("")
        return lines

    def _last_summaries(self, role, this_session):
        lines = ["## Handoff from earlier sessions"]
        rows = self.conn.execute(
            "SELECT session_id, role, scope, ended_at, summary FROM sessions "
            "WHERE session_id <> ? AND summary IS NOT NULL ORDER BY session_id DESC LIMIT 3",
            (this_session,)).fetchall()
        same_role = self.conn.execute(
            "SELECT session_id, role, scope, ended_at, summary FROM sessions "
            "WHERE session_id <> ? AND role = ? AND summary IS NOT NULL ORDER BY session_id DESC LIMIT 1",
            (this_session, role)).fetchone()
        shown = {r["session_id"] for r in rows}
        if same_role and same_role["session_id"] not in shown:
            rows = list(rows) + [same_role]
        if not rows:
            lines.append("- No earlier session has left a summary.")
        for r in rows:
            lines.append(f"- Session {r['session_id']} ({r['role']}, {r['ended_at'] or 'not closed'}), "
                         f"scope: {r['scope']}")
            lines.append(f"  {r['summary']}")
        lines.append("")
        return lines

    def _counts_lines(self):
        def count(sql, *args):
            return self.conn.execute(sql, args).fetchone()[0]
        glossary = dict(self.conn.execute("SELECT status, COUNT(*) FROM glossary GROUP BY status").fetchall())
        drafts = dict(self.conn.execute("SELECT status, COUNT(*) FROM drafts GROUP BY status").fetchall())
        flags = self.conn.execute(
            "SELECT kind, COUNT(*) FROM flags WHERE status = 'open' GROUP BY kind").fetchall()
        lines = ["## Where things stand",
                 f"- Glossary entries: {sum(glossary.values())} " + self._dict_text(glossary),
                 f"- Drafts: {sum(drafts.values())} " + self._dict_text(drafts),
                 f"- Open flags: {sum(n for _k, n in flags)} "
                 + ("(" + ", ".join(f"{k} {n}" for k, n in flags) + ")" if flags else ""),
                 f"- Decisions logged: {count('SELECT COUNT(*) FROM decisions')}",
                 ""]
        return lines

    @staticmethod
    def _dict_text(counts):
        return "(" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ")" if counts else ""

    def _section_lines(self, book, first_chapter, last_chapter):
        """Facts about the book and chapters the session will work on."""
        try:
            number = book_number(book)
        except KeyError as err:
            sys.exit(str(err.args[0]))
        first = first_chapter or 1
        last = last_chapter or first_chapter or 999
        lines = [f"## Section: {book_name(number)} chapters {first} to {last if last != 999 else 'end'}"]

        rows = self.conn.execute(
            "SELECT status, COUNT(*) FROM drafts WHERE book = ? AND chapter BETWEEN ? AND ? "
            "GROUP BY status", (number, first, last)).fetchall()
        lines.append("- Drafts here: " + (", ".join(f"{s} {n}" for s, n in rows) or "none yet"))
        flags = self.conn.execute(
            "SELECT COUNT(*) FROM flags f JOIN drafts d ON d.draft_id = f.draft_id "
            "WHERE f.status = 'open' AND d.book = ? AND d.chapter BETWEEN ? AND ?",
            (number, first, last)).fetchone()[0]
        lines.append(f"- Open flags on these drafts: {flags}")
        notes = self.conn.execute(
            "SELECT kind, COUNT(*) FROM verse_notes WHERE book = ? AND chapter BETWEEN ? AND ? "
            "GROUP BY kind", (number, first, last)).fetchall()
        if notes:
            lines.append("- Verse notes here: " + ", ".join(f"{k} {n}" for k, n in notes))

        lines += self._missing_glossary(number, first, last)
        lines.append("")
        return lines

    def _missing_glossary(self, book, first, last):
        """Lemmas used in this section that have no approved glossary entry yet."""
        if self.source_path is None:
            return ["- (Give --source to see which lemmas still lack a glossary entry.)"]
        if not self.source_path.is_file():
            sys.exit(f"Source database not found: {self.source_path}")
        src = sqlite3.connect(self.source_path.as_uri() + "?mode=ro", uri=True)
        try:
            used = src.execute(
                "SELECT l.lemma_id, COUNT(*) FROM words w JOIN lemmas l ON l.lemma_id = w.lemma_id "
                "WHERE w.book = ? AND w.chapter BETWEEN ? AND ? "
                "AND (w.word_type IS NULL OR w.word_type LIKE 'N%') "
                "AND l.strongs <> '' GROUP BY l.lemma_id", (book, first, last)).fetchall()
        finally:
            src.close()
        approved = {r[0] for r in self.conn.execute(
            "SELECT lemma_id FROM glossary WHERE status = 'approved'")}
        missing = [(lid, n) for lid, n in used if lid not in approved]
        uses_missing = sum(n for _lid, n in missing)
        return [f"- Lemmas used in this section: {len(used)}. Without an approved glossary entry: "
                f"{len(missing)} (covering {uses_missing} word uses)."]

    def _role_rules(self, role):
        rules = {
            "preparation": "Writes the source database only. Never touches drafts or the glossary.",
            "lexical": "Writes the glossary and decisions. Never touches drafts.",
            "translation": "Writes drafts, alignment and verse notes. May not change the glossary: "
                           "record a departure in draft_alignment (departs = 1 with a note).",
            "quality": "Writes flags only. Never edits drafts.",
            "programming": "Changes code only. Never touches translation content.",
        }
        return ["## Your limits", f"- {rules[role]}",
                "- Before ending: run `handoff.py end` so the checks run and a summary is saved.", ""]

    # ------------------------------------------------------------------
    # End
    # ------------------------------------------------------------------
    def end(self, session_id, summary, force=False):
        """Run the checks, then close the session. Returns True if it was closed."""
        row = self.conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            sys.exit(f"No session {session_id} in this project.")
        if row["ended_at"]:
            sys.exit(f"Session {session_id} was already closed at {row['ended_at']}.")
        if not summary or len(summary.strip()) < 20:
            sys.exit("Write a handoff summary of at least a sentence: what was done, "
                     "what is open, what the next session starts with.")

        issues = self.run_checks(session_id)
        print(f"Checks for session {session_id} ({row['role']}):")
        if issues:
            for text in issues:
                print("  - " + text)
        else:
            print("  no issues found")

        if issues and row["role"] in STRICT_ROLES and not force:
            print(f"\nNot closed: a {row['role']} session needs zero unexplained issues. "
                  "Fix them, or close anyway with --force (the issues are then recorded in the summary).")
            return False

        full = summary.strip()
        if issues:
            full += f"\n[Closed with {len(issues)} unresolved issue(s): " + "; ".join(issues) + "]"
        self.conn.execute(
            "UPDATE sessions SET summary = ?, ended_at = datetime('now') WHERE session_id = ?",
            (full, session_id))
        self.conn.commit()
        print(f"Session {session_id} closed.")

        backup = self.backup()
        print(f"Backup written: {backup}")
        print("Reminder: commit any code changes to Git. The databases are never committed.")
        return True

    def run_checks(self, session_id):
        """The closing checks. Each returns plain sentences describing a problem."""
        issues = []

        # 1. A departure from the glossary must say why.
        n = self.conn.execute(
            "SELECT COUNT(*) FROM draft_alignment a JOIN drafts d ON d.draft_id = a.draft_id "
            "WHERE d.session_id = ? AND a.departs = 1 AND (a.departure_note IS NULL OR TRIM(a.departure_note) = '')",
            (session_id,)).fetchone()[0]
        if n:
            issues.append(f"{n} departure(s) from the glossary have no departure_note")

        # 2. A rendering that differs from the glossary but is not marked as a departure.
        rows = self.conn.execute(
            "SELECT a.alignment_id, a.rendered_as, g.rendering, g.allowed_variants "
            "FROM draft_alignment a JOIN drafts d ON d.draft_id = a.draft_id "
            "JOIN glossary g ON g.glossary_id = a.glossary_id "
            "WHERE d.session_id = ? AND a.departs = 0", (session_id,)).fetchall()
        mismatches = 0
        for r in rows:
            allowed = {self._norm(r["rendering"])}
            allowed |= {self._norm(v) for v in re.split(r"[;,]", r["allowed_variants"] or "") if v.strip()}
            if self._norm(r["rendered_as"]) not in allowed:
                mismatches += 1
        if mismatches:
            issues.append(f"{mismatches} word(s) differ from the glossary but are not marked as departures")

        # 3. Words in this session's drafts that are not tied to any glossary entry.
        n = self.conn.execute(
            "SELECT COUNT(*) FROM draft_alignment a JOIN drafts d ON d.draft_id = a.draft_id "
            "WHERE d.session_id = ? AND a.glossary_id IS NULL", (session_id,)).fetchone()[0]
        if n:
            issues.append(f"{n} word(s) have no glossary entry")

        # 4. Flags raised in this session that are still open.
        n = self.conn.execute(
            "SELECT COUNT(*) FROM flags WHERE session_id = ? AND status = 'open'", (session_id,)).fetchone()[0]
        if n:
            issues.append(f"{n} flag(s) raised in this session are still open")
        return issues

    @staticmethod
    def _norm(text):
        """Compare renderings without caring about case or extra spaces."""
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    # ------------------------------------------------------------------
    # Backups and status
    # ------------------------------------------------------------------
    def backup(self):
        """Copy the project database to a new dated file. Older copies are never overwritten."""
        self.backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.backup_dir / f"{self.project_path.stem}_{stamp}.db"
        counter = 1
        while target.exists():              # two backups in one second still get different names
            target = self.backup_dir / f"{self.project_path.stem}_{stamp}_{counter}.db"
            counter += 1
        dest = sqlite3.connect(target)
        try:
            self.conn.backup(dest)          # SQLite's own safe copy, correct even if the file is in use
        finally:
            dest.close()
        self._prune_backups()
        return target

    def _backups(self):
        pattern = f"{self.project_path.stem}_*.db"
        files = sorted(self.backup_dir.glob(pattern)) if self.backup_dir.is_dir() else []
        return [(f, datetime.fromtimestamp(f.stat().st_mtime)) for f in files]

    def latest_backup(self):
        found = self._backups()
        return max(found, key=lambda x: x[1]) if found else None

    def _prune_backups(self):
        """Keep the newest BACKUP_KEEP backups of this project, remove only older ones."""
        found = sorted(self._backups(), key=lambda x: x[1], reverse=True)
        for path, _when in found[BACKUP_KEEP:]:
            path.unlink()

    def status(self):
        lines = ["## Open sessions"]
        rows = self.conn.execute(
            "SELECT session_id, role, scope, started_at FROM sessions WHERE ended_at IS NULL").fetchall()
        lines += [f"- {r['session_id']} ({r['role']}) {r['scope']} since {r['started_at']}" for r in rows] \
            or ["- none"]
        lines.append("")
        lines.append("## Recent sessions")
        rows = self.conn.execute(
            "SELECT session_id, role, scope, ended_at FROM sessions ORDER BY session_id DESC LIMIT 8").fetchall()
        lines += [f"- {r['session_id']} {r['role']}: {r['scope']} ({'closed ' + r['ended_at'] if r['ended_at'] else 'OPEN'})"
                  for r in rows] or ["- none"]
        lines.append("")
        backup = self.latest_backup()
        lines.append("## Backups")
        lines.append(f"- latest: {backup[0].name} ({backup[1]:%Y-%m-%d %H:%M})" if backup else "- none yet")
        return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Start and end translation_workbench sessions")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="open a session and print the briefing")
    start.add_argument("--project", required=True, help="path to project.db")
    start.add_argument("--role", required=True, choices=ROLES)
    start.add_argument("--scope", required=True, help="what this session will cover")
    start.add_argument("--source", help="path to source_texts.db, to count lemmas lacking a glossary entry")
    start.add_argument("--book", help="book of the section, for section facts")
    start.add_argument("--chapters", nargs="+", type=int, metavar="N",
                       help="first chapter, and optionally the last")

    end = sub.add_parser("end", help="run the checks, save the summary, close and back up")
    end.add_argument("--project", required=True)
    end.add_argument("--session", required=True, type=int)
    end.add_argument("--summary", required=True, help="what was done, what is open, what comes next")
    end.add_argument("--force", action="store_true",
                     help="close a translation or quality session even though checks found issues")

    for name, text in (("status", "show open sessions and backups"), ("backup", "make a dated backup now")):
        sub.add_parser(name, help=text).add_argument("--project", required=True)

    args = parser.parse_args()
    manager = SessionManager(args.project, getattr(args, "source", None))

    if args.command == "start":
        chapters = args.chapters or []
        session_id, briefing = manager.start(args.role, args.scope, args.book,
                                             chapters[0] if chapters else None,
                                             chapters[1] if len(chapters) > 1 else None)
        print(briefing)
        print(f"(Remember the session number: {session_id}. You need it to end the session.)")
    elif args.command == "end":
        sys.exit(0 if manager.end(args.session, args.summary, args.force) else 1)
    elif args.command == "status":
        print(manager.status())
    elif args.command == "backup":
        print(f"Backup written: {manager.backup()}")


if __name__ == "__main__":
    main()
