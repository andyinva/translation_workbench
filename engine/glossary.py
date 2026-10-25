#!/usr/bin/env python3
"""
glossary.py  (place this file in translation_workbench/engine/)

The lexical session's tool for the project glossary. Three commands:

    todo   list the lemmas of a section that still have no approved glossary
           entry, most used first
    set    add or change the glossary entry for one lemma sense
    show   print the glossary rows for one lemma
    load   add many entries from a CSV file (a batch of proposals)
    approve  turn proposed entries into approved ones
    remove   delete an entry that was recorded against the wrong lemma

Examples:
    python3 glossary.py todo --project P.db --source S.db --book Genesis --chapters 1 3
    python3 glossary.py set  --project P.db --source S.db --session 4 --lemma-id 1065 \\
        --rendering create --reason "Only God is the subject of this verb" --status approved
    python3 glossary.py show --project P.db --source S.db --lemma-id 1065

Rules built into `set` (they come from docs/session_roles.md):
    * Only a session whose role is "lexical" may write the glossary, and the
      session must still be open.
    * An approved entry must have a reason.
    * Changing an existing entry also writes a line in the decisions table with
      the old and the new rendering, so the history is never lost.
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

# books.py sits next to this file, whether run as a script or as part of a package.
try:
    from books import book_name, book_number
except ImportError:
    from engine.books import book_name, book_number

# translit = a word kept in plain transliteration (for example yovel); see style_rules.md section 4a
CATEGORIES = ["word", "name", "divine_name", "term", "translit"]
STATUSES = ["proposed", "approved", "revised"]


def open_db(path, read_only=False):
    """Open a SQLite file. Read-only for the source database, which is never changed here."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        sys.exit(f"Database not found: {path}")
    if read_only:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


class GlossaryTool:
    """Reads the source database and reads and writes the project glossary."""

    def __init__(self, project_path, source_path):
        self.project = open_db(project_path)
        self.source = open_db(source_path, read_only=True)

    # ------------------------------------------------------------------
    # todo
    # ------------------------------------------------------------------
    def todo(self, book, first_chapter, last_chapter, limit):
        """Lemmas of the section with no approved glossary entry, most used first."""
        number = self._book(book)
        rows = self.source.execute(
            "SELECT l.lemma_id, l.lemma, l.strongs, l.headword, l.gloss_hint, l.transliteration, "
            "       COUNT(*) AS uses_here, "
            "       (SELECT lc.occurrences FROM lemma_counts lc WHERE lc.lemma_id = l.lemma_id) AS uses_total "
            "FROM words w JOIN lemmas l ON l.lemma_id = w.lemma_id "
            "WHERE w.book = ? AND w.chapter BETWEEN ? AND ? "
            "AND (w.word_type IS NULL OR w.word_type LIKE 'N%') "
            "GROUP BY l.lemma_id ORDER BY uses_here DESC, l.lemma_id",
            (number, first_chapter, last_chapter)).fetchall()

        entries = {}        # lemma_id -> list of (sense, status, rendering)
        for g in self.project.execute("SELECT lemma_id, sense_label, status, rendering FROM glossary"):
            entries.setdefault(g["lemma_id"], []).append((g["sense_label"], g["status"], g["rendering"]))

        done = [r for r in rows if any(s == "approved" for _x, s, _y in entries.get(r["lemma_id"], []))]
        todo = [r for r in rows if r not in done]
        title = f"{book_name(number)} {first_chapter}" + (f"-{last_chapter}" if last_chapter != first_chapter else "")
        out = [f"Section {title}: {len(rows)} lemmas, {len(done)} approved, {len(todo)} still to do",
               f"(uses_here counts words in this section; total counts the whole Bible)", ""]

        words = [r for r in todo if r["strongs"]]
        prefixes = [r for r in todo if not r["strongs"]]
        out.append(f"{'id':>6}  {'here':>5} {'total':>6}  {'word':<14} {'translit':<14} {'strong':<8} {'hint':<22} status")
        for r in words[:limit]:
            current = ", ".join(f"{s}:{rend} [{st}]" for s, st, rend in entries.get(r["lemma_id"], []))
            out.append(f"{r['lemma_id']:>6}  {r['uses_here']:>5} {r['uses_total'] or 0:>6}  "
                       f"{(r['headword'] or r['lemma'])[:14]:<14} {(r['transliteration'] or '')[:14]:<14} "
                       f"{(r['strongs'] or '')[:8]:<8} "
                       f"{(r['gloss_hint'] or '')[:22]:<22} {current or 'none'}")
        if len(words) > limit:
            out.append(f"... {len(words) - limit} more words. Raise --limit to see them.")
        if prefixes:
            out.append("")
            out.append("Prefix and particle lemmas (no Strong's number) still to do:")
            for r in prefixes:
                out.append(f"{r['lemma_id']:>6}  {r['uses_here']:>5}  code '{r['lemma']}'  {r['gloss_hint'] or ''}")
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------
    # set
    # ------------------------------------------------------------------
    def set_entry(self, session_id, lemma_id, rendering, sense="main", category="word", tier=None,
                  pronunciation=None, allowed=None, rule=None, reason=None, status="proposed"):
        """Insert or update one glossary entry. Returns a sentence describing what happened."""
        session = self.project.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if session is None:
            sys.exit(f"No session {session_id}. Start one first with handoff.py start.")
        if session["role"] != "lexical":
            sys.exit(f"Session {session_id} is a {session['role']} session. Only a lexical session "
                     "may write the glossary. Translation sessions record departures instead.")
        if session["ended_at"]:
            sys.exit(f"Session {session_id} is closed.")

        lemma = self.source.execute("SELECT * FROM lemmas WHERE lemma_id = ?", (lemma_id,)).fetchone()
        if lemma is None:
            sys.exit(f"No lemma {lemma_id} in the source database.")
        if not rendering.strip():
            sys.exit("A rendering cannot be empty.")
        if status == "approved" and not (reason or "").strip():
            sys.exit("An approved entry needs a reason (--reason), so the decision can be revisited.")
        if category == "name" and tier is None:
            sys.exit("A name entry needs a tier: 1 always transliterate, 2 when the text explains the name, "
                     "3 the familiar form.")

        name = lemma["headword"] or lemma["lemma"]
        existing = self.project.execute(
            "SELECT * FROM glossary WHERE lemma_id = ? AND sense_label = ?", (lemma_id, sense)).fetchone()
        if existing is None:
            self.project.execute(
                "INSERT INTO glossary (lemma_id, sense_label, category, tier, rendering, pronunciation, "
                "allowed_variants, rule, reason, status, session_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (lemma_id, sense, category, tier, rendering.strip(), pronunciation, allowed, rule,
                 reason, status, session_id))
            self.project.commit()
            return f"Added {name} ({sense}): {rendering.strip()} [{status}]"

        # An existing entry is updated, and the change is written to the decision log.
        if existing["rendering"] == rendering.strip() and existing["status"] == status:
            summary = f"Details updated for '{rendering.strip()}' (rule, allowed variants or reason)"
        else:
            summary = (f"Rendering changed from '{existing['rendering']}' [{existing['status']}] "
                       f"to '{rendering.strip()}' [{status}]")
        self.project.execute(
            "UPDATE glossary SET category = ?, tier = ?, rendering = ?, pronunciation = ?, "
            "allowed_variants = ?, rule = ?, reason = ?, status = ?, session_id = ?, "
            "updated_at = datetime('now') WHERE glossary_id = ?",
            (category, tier, rendering.strip(), pronunciation, allowed, rule,
             reason if reason is not None else existing["reason"], status, session_id,
             existing["glossary_id"]))
        self.project.execute(
            "INSERT INTO decisions (topic, summary, reason, glossary_id, session_id) VALUES (?,?,?,?,?)",
            (f"Glossary: {name} ({sense})", summary, reason, existing["glossary_id"], session_id))
        self.project.commit()
        return f"Updated {name} ({sense}): {summary}. Change logged."

    # ------------------------------------------------------------------
    # load: many entries from a CSV file
    # ------------------------------------------------------------------
    CSV_COLUMNS = ["lemma_id", "sense", "category", "tier", "rendering", "allowed", "rule", "reason", "status"]

    def load_csv(self, session_id, csv_path):
        """Add or update entries from a CSV file. A row that matches the database is skipped."""
        path = Path(csv_path).expanduser().resolve()
        if not path.is_file():
            sys.exit(f"CSV file not found: {path}")
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = [c for c in self.CSV_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                sys.exit("The CSV file lacks these columns: " + ", ".join(missing))
            rows = list(reader)

        added = unchanged = 0
        problems = []
        for number, row in enumerate(rows, start=2):        # row 1 is the header
            try:
                lemma_id = int(row["lemma_id"])
                tier = int(row["tier"]) if (row["tier"] or "").strip() else None
                fields = dict(sense=(row["sense"] or "main").strip(), category=(row["category"] or "word").strip(),
                              tier=tier, allowed=(row["allowed"] or "").strip() or None,
                              rule=(row["rule"] or "").strip() or None,
                              reason=(row["reason"] or "").strip() or None,
                              status=(row["status"] or "proposed").strip())
                if fields["category"] not in CATEGORIES or fields["status"] not in STATUSES:
                    raise ValueError("category or status is not one of the allowed values")
                if self._same_as_stored(lemma_id, row["rendering"].strip(), fields):
                    unchanged += 1
                    continue
                self.set_entry(session_id, lemma_id, row["rendering"], **fields)
                added += 1
            except SystemExit as err:           # set_entry reports its refusals this way
                problems.append(f"row {number}: {err}")
            except (ValueError, KeyError) as err:
                problems.append(f"row {number}: {err}")
        lines = [f"Loaded {path.name}: {added} added or changed, {unchanged} already matched, "
                 f"{len(problems)} problem(s)."]
        lines += ["  " + text for text in problems]
        return "\n".join(lines) + "\n", not problems

    def _same_as_stored(self, lemma_id, rendering, fields):
        """True if the database already holds exactly this entry."""
        row = self.project.execute(
            "SELECT * FROM glossary WHERE lemma_id = ? AND sense_label = ?",
            (lemma_id, fields["sense"])).fetchone()
        if row is None:
            return False
        return (row["rendering"] == rendering and row["category"] == fields["category"]
                and row["tier"] == fields["tier"] and row["allowed_variants"] == fields["allowed"]
                and row["rule"] == fields["rule"] and row["reason"] == fields["reason"]
                and row["status"] == fields["status"])

    # ------------------------------------------------------------------
    # approve: proposed entries become approved
    # ------------------------------------------------------------------
    def approve(self, session_id, lemma_ids=None):
        """Approve proposed entries (all of them, or only the lemmas given). A reason is required."""
        sql = "SELECT * FROM glossary WHERE status = 'proposed'"
        params = []
        if lemma_ids:
            sql += " AND lemma_id IN (" + ",".join("?" * len(lemma_ids)) + ")"
            params = list(lemma_ids)
        rows = self.project.execute(sql + " ORDER BY lemma_id, sense_label", params).fetchall()
        done, skipped = 0, []
        for g in rows:
            if not (g["reason"] or "").strip():
                skipped.append(f"lemma {g['lemma_id']} ({g['sense_label']}): no reason recorded")
                continue
            self.set_entry(session_id, g["lemma_id"], g["rendering"], g["sense_label"], g["category"],
                           g["tier"], g["pronunciation"], g["allowed_variants"], g["rule"], g["reason"],
                           "approved")
            done += 1
        lines = [f"Approved {done} entries."] + ["  skipped " + t for t in skipped]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # remove: undo an entry entered by mistake
    # ------------------------------------------------------------------
    def remove_entry(self, session_id, lemma_id, sense, reason):
        """Delete one glossary entry. The removal and its reason are kept in the decision log."""
        session = self.project.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if session is None or session["role"] != "lexical" or session["ended_at"]:
            sys.exit(f"Session {session_id} must be an open lexical session.")
        if not (reason or "").strip():
            sys.exit("A reason is required (--reason), so the removal can be understood later.")
        g = self.project.execute("SELECT * FROM glossary WHERE lemma_id = ? AND sense_label = ?",
                                 (lemma_id, sense)).fetchone()
        if g is None:
            sys.exit(f"No entry for lemma {lemma_id} with sense '{sense}'.")
        for table, what in (("draft_alignment", "a draft alignment"), ("flags", "a flag")):
            if self.project.execute(f"SELECT 1 FROM {table} WHERE glossary_id = ? LIMIT 1",
                                    (g["glossary_id"],)).fetchone():
                sys.exit(f"This entry is used by {what}. Change it with 'set' instead of removing it.")
        lemma = self.source.execute("SELECT * FROM lemmas WHERE lemma_id = ?", (lemma_id,)).fetchone()
        name = (lemma["headword"] or lemma["lemma"]) if lemma else str(lemma_id)
        # The decision row is written first, without a link to the entry that is about to disappear.
        self.project.execute(
            "INSERT INTO decisions (topic, summary, reason, session_id) VALUES (?,?,?,?)",
            (f"Glossary removal: {name} ({sense})",
             f"Removed entry '{g['rendering']}' [{g['status']}] for lemma {lemma_id}", reason.strip(), session_id))
        # Decisions that pointed at the entry keep their text but lose the link.
        self.project.execute("UPDATE decisions SET glossary_id = NULL WHERE glossary_id = ?", (g["glossary_id"],))
        self.project.execute("DELETE FROM glossary WHERE glossary_id = ?", (g["glossary_id"],))
        self.project.commit()
        return f"Removed {name} ({sense}): '{g['rendering']}'. Removal logged.\n"

    # ------------------------------------------------------------------
    # show
    # ------------------------------------------------------------------
    def show(self, lemma_id):
        lemma = self.source.execute("SELECT * FROM lemmas WHERE lemma_id = ?", (lemma_id,)).fetchone()
        if lemma is None:
            sys.exit(f"No lemma {lemma_id} in the source database.")
        out = [f"Lemma {lemma_id}: {lemma['headword'] or lemma['lemma']} | code {lemma['lemma']} | "
               f"{lemma['strongs'] or '-'} | hint: {lemma['gloss_hint'] or '-'}"]
        rows = self.project.execute(
            "SELECT * FROM glossary WHERE lemma_id = ? ORDER BY sense_label", (lemma_id,)).fetchall()
        if not rows:
            out.append("No glossary entry yet.")
        for g in rows:
            kind = g["category"] + (f", tier {g['tier']}" if g["tier"] else "")
            out.append(f"- sense '{g['sense_label']}' [{g['status']}] ({kind})  RENDERING: {g['rendering']}")
            for label, key in (("also allowed", "allowed_variants"), ("rule", "rule"),
                               ("reason", "reason"), ("pronunciation", "pronunciation")):
                if g[key]:
                    out.append(f"    {label}: {g[key]}")
        return "\n".join(out) + "\n"

    @staticmethod
    def _book(name):
        try:
            return book_number(name)
        except KeyError as err:
            sys.exit(str(err.args[0]))


def main():
    parser = argparse.ArgumentParser(description="Project glossary tool for lexical sessions")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--project", required=True, help="path to project.db")
        p.add_argument("--source", required=True, help="path to source_texts.db")

    todo = sub.add_parser("todo", help="lemmas of a section without an approved entry")
    common(todo)
    todo.add_argument("--book", required=True)
    todo.add_argument("--chapters", required=True, nargs="+", type=int, metavar="N",
                      help="first chapter, and optionally the last")
    todo.add_argument("--limit", type=int, default=40, help="most words to list (default 40)")

    setp = sub.add_parser("set", help="add or change a glossary entry")
    common(setp)
    setp.add_argument("--session", required=True, type=int, help="an open lexical session")
    setp.add_argument("--lemma-id", required=True, type=int)
    setp.add_argument("--rendering", required=True, help="the English rendering")
    setp.add_argument("--sense", default="main", help="sense label, default main")
    setp.add_argument("--category", default="word", choices=CATEGORIES)
    setp.add_argument("--tier", type=int, choices=[1, 2, 3], help="for names only")
    setp.add_argument("--pronunciation")
    setp.add_argument("--allowed", help="other acceptable renderings, separated by semicolons")
    setp.add_argument("--rule", help="when this sense applies")
    setp.add_argument("--reason", help="why; required for an approved entry")
    setp.add_argument("--status", default="proposed", choices=STATUSES)

    show = sub.add_parser("show", help="print the entries for one lemma")
    common(show)
    show.add_argument("--lemma-id", required=True, type=int)

    load = sub.add_parser("load", help="add many entries from a CSV file")
    common(load)
    load.add_argument("--session", required=True, type=int, help="an open lexical session")
    load.add_argument("csv_file", help="columns: " + ", ".join(GlossaryTool.CSV_COLUMNS))

    remove = sub.add_parser("remove", help="delete an entry recorded against the wrong lemma")
    common(remove)
    remove.add_argument("--session", required=True, type=int, help="an open lexical session")
    remove.add_argument("--lemma-id", required=True, type=int)
    remove.add_argument("--sense", default="main")
    remove.add_argument("--reason", required=True)

    approve = sub.add_parser("approve", help="turn proposed entries into approved ones")
    common(approve)
    approve.add_argument("--session", required=True, type=int, help="an open lexical session")
    approve.add_argument("--lemma-ids", nargs="+", type=int, metavar="ID",
                         help="only these lemmas (default: every proposed entry)")

    args = parser.parse_args()
    tool = GlossaryTool(args.project, args.source)
    if args.command == "todo":
        first = args.chapters[0]
        last = args.chapters[1] if len(args.chapters) > 1 else first
        print(tool.todo(args.book, first, last, args.limit))
    elif args.command == "set":
        print(tool.set_entry(args.session, args.lemma_id, args.rendering, args.sense, args.category,
                             args.tier, args.pronunciation, args.allowed, args.rule, args.reason, args.status))
    elif args.command == "load":
        text, ok = tool.load_csv(args.session, args.csv_file)
        print(text)
        sys.exit(0 if ok else 1)
    elif args.command == "remove":
        print(tool.remove_entry(args.session, args.lemma_id, args.sense, args.reason))
    elif args.command == "approve":
        print(tool.approve(args.session, args.lemma_ids))
    else:
        print(tool.show(args.lemma_id))


if __name__ == "__main__":
    main()
