#!/usr/bin/env python3
"""
draft.py  (place this file in translation_workbench/engine/)

The translation session's tool. It shows a verse with the approved glossary
entry for each source word, records a draft of the verse together with its
alignment to the glossary, keeps several candidate versions side by side, and
lets you choose one.

Commands:
    sheet   print the verse sheet: every source word with its glossary entry
            (add --template FILE to write the alignment CSV to fill in)
    add     record a draft version of a verse, with its alignment CSV
    choose  mark one version as the chosen one (the others become rejected)
    note    add a verse note
    show    print the drafts and notes of a chapter or a verse

A typical run for one verse:
    python3 draft.py sheet --project P --source S --book Genesis --chapter 23 --verse 9 --template v9.csv
    (edit v9.csv: the rendered_as column, one row per source word)
    python3 draft.py add   --project P --source S --session 5 --book Genesis --chapter 23 --verse 9 \\
        --text "that he may give me the cave of Machpelah ..." --alignment v9.csv
    python3 draft.py choose --project P --session 5 --draft-id 1 --seed --reason "Clearest on its own"

Rules built in (they come from docs/session_roles.md and style_rules.md):
    * Only an open session with the role "translation" may write drafts.
    * Only words that have an approved glossary entry can be used. A missing
      entry is reported so a lexical session can add it first.
    * rendered_as names the glossary word that was used (the base form, such as
      "say"), not the inflected word in the text ("said"). It must be the
      glossary rendering or one of its allowed variants. Anything else is a
      departure: put 1 in the departs column and explain it in the note column.
    * Old versions are never overwritten. A new text is a new version.

Draft statuses used here: candidate (one of several under consideration),
seed (chosen, but provisional until the chapter has been translated),
draft (chosen), rejected, and the later checked, frozen and legacy.
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

# books.py and schema.py sit next to this file, whether run as a script or as a package.
try:
    from books import book_name, book_number
    from schema import ProjectSchema
    from phrases import find_matches
except ImportError:
    from engine.books import book_name, book_number
    from engine.schema import ProjectSchema
    from engine.phrases import find_matches

# Glossary statuses that count as established. "proposed" does not.
ESTABLISHED = ("approved", "revised")
# Draft statuses that are still open to a choice.
OPEN_STATUSES = ("candidate", "seed", "draft")
TEMPLATE_COLUMNS = ["position", "surface", "lemma_id", "sense", "rendered_as", "departs", "note"]


def open_db(path, read_only=False):
    """Open a SQLite file. The source database is opened read-only."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        sys.exit(f"Database not found: {path}")
    if read_only:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def norm(text):
    """Compare renderings without caring about case or extra spaces."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class DraftTool:
    """Reads the source verse and the glossary, and writes drafts to the project database."""

    def __init__(self, project_path, source_path=None):
        self.project = open_db(project_path)
        # The drafts table gains a reason column; add it to older databases quietly.
        ProjectSchema(project_path).add_missing_columns(self.project)
        self.project.commit()
        self.source = open_db(source_path, read_only=True) if source_path else None

    # ------------------------------------------------------------------
    # Reading the source verse and the glossary
    # ------------------------------------------------------------------
    def _text_id(self, book):
        """The source text for a book: the Hebrew OT for books 1 to 39, the Greek NT after that."""
        name = "OSHB" if book <= 39 else "TAGNT"
        row = self.source.execute("SELECT text_id FROM texts WHERE name = ?", (name,)).fetchone()
        if row is None:
            sys.exit(f"The source text {name} is not in the source database.")
        return row["text_id"]

    def verse_words(self, book, chapter, verse):
        """Every part of the verse in reading order, with its lemma."""
        sql = ("SELECT w.word_id, w.position, w.word_number, w.surface, w.lemma_id, w.morph, "
               "       l.lemma, l.strongs, l.transliteration, l.headword, l.gloss_hint "
               "FROM words w LEFT JOIN lemmas l ON l.lemma_id = w.lemma_id "
               "WHERE w.text_id = ? AND w.book = ? AND w.chapter = ? AND w.verse = ? ")
        if book > 39:
            sql += "AND (w.word_type IS NULL OR w.word_type LIKE 'N%') "      # Nestle-Aland text
        rows = self.source.execute(sql + "ORDER BY w.position", (self._text_id(book), book, chapter, verse)).fetchall()
        if not rows:
            sys.exit(f"No source words for {book_name(book)} {chapter}:{verse}.")
        return rows

    def glossary_for(self, lemma_ids):
        """Glossary rows for the lemmas, grouped by lemma_id."""
        found = {}
        if not lemma_ids:
            return found
        marks = ",".join("?" * len(lemma_ids))
        for g in self.project.execute(
                f"SELECT * FROM glossary WHERE lemma_id IN ({marks}) ORDER BY sense_label", list(lemma_ids)):
            found.setdefault(g["lemma_id"], []).append(g)
        return found

    @staticmethod
    def allowed_words(entry):
        """The renderings a glossary entry permits: the rendering plus its allowed variants."""
        words = {norm(entry["rendering"])}
        words |= {norm(v) for v in re.split(r"[;,]", entry["allowed_variants"] or "") if v.strip()}
        return words

    # ------------------------------------------------------------------
    # sheet
    # ------------------------------------------------------------------
    def sheet(self, book, chapter, verse, template_path=None):
        """The verse sheet. Optionally writes the alignment template."""
        words = self.verse_words(book, chapter, verse)
        lemma_ids = {w["lemma_id"] for w in words if w["lemma_id"]}
        glossary = self.glossary_for(lemma_ids)

        # The verse text: the parts of one whole word are joined without a space.
        pieces, last = [], None
        for w in words:
            if w["word_number"] != last:
                pieces.append(w["surface"])
                last = w["word_number"]
            else:
                pieces[-1] += w["surface"]
        out = [f"# Verse sheet: {book_name(book)} {chapter}:{verse}", "", " ".join(pieces), "",
               "## Source words, with the glossary entry for each"]
        out.append(f"{'pos':>3}  {'word':<10} {'translit':<12} {'morph':<10} {'lemma':>6}  glossary")
        template_rows, missing = [], []
        for w in words:
            entries = glossary.get(w["lemma_id"], []) if w["lemma_id"] else []
            if not w["lemma_id"]:
                text = "(no lemma: a suffix or other part)"
            elif not entries:
                text = "NO ENTRY"
                missing.append(w)
            else:
                text = "; ".join(self._entry_text(e) for e in entries)
                if not any(e["status"] in ESTABLISHED for e in entries):
                    missing.append(w)
            out.append(f"{w['position']:>3}  {w['surface']:<10} {(w['transliteration'] or '')[:12]:<12} "
                       f"{(w['morph'] or '')[:10]:<10} {w['lemma_id'] or '':>6}  {text}")
            template_rows.append(self._template_row(w, entries))

        if missing:
            out += ["", "## Not ready: these words have no approved glossary entry. "
                        "A lexical session must add them before this verse can be drafted."]
            for w in missing:
                out.append(f"  position {w['position']}, lemma {w['lemma_id']} {w['headword'] or w['lemma']} "
                           f"({w['strongs'] or 'no Strong number'})")
        # Phrases (glossary_phrases) that occur in this verse must be written as one unit.
        found = find_matches(self.project, words)
        if found:
            out += ["", "## Phrases in this verse (write each as one unit)"]
            for m in found:
                tag = "" if m["status"] in ESTABLISHED else f" [{m['status']}, not yet enforced]"
                out.append(f"  {m['name']}: '{m['rendering']}' at positions {m['positions']}{tag}")
        out += self._drafts_lines(book, chapter, verse)
        out += self._notes_lines(book, chapter, verse)

        if template_path:
            self._write_template(template_path, template_rows)
            out += ["", f"Alignment template written: {template_path}"]
        return "\n".join(out) + "\n"

    @staticmethod
    def _entry_text(entry):
        """One glossary entry as a short line."""
        text = f"{entry['sense_label']}: {entry['rendering']}"
        if entry["allowed_variants"]:
            text += f" (also {entry['allowed_variants']})"
        return text + ("" if entry["status"] in ESTABLISHED else f" [{entry['status']}]")

    @staticmethod
    def _template_row(word, entries):
        """A starting row for the alignment CSV. Parts without a lemma stay out of the alignment."""
        ready = [e for e in entries if e["status"] in ESTABLISHED]
        sense = rendered = ""
        if len(ready) == 1:
            sense, rendered = ready[0]["sense_label"], ready[0]["rendering"]
        return [word["position"], word["surface"], word["lemma_id"] or "", sense, rendered, "", ""]

    @staticmethod
    def _write_template(path, rows):
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(TEMPLATE_COLUMNS)
            writer.writerows(rows)

    def _drafts_lines(self, book, chapter, verse):
        rows = self.project.execute(
            "SELECT * FROM drafts WHERE book = ? AND chapter = ? AND verse = ? ORDER BY version",
            (book, chapter, verse)).fetchall()
        if not rows:
            return ["", "## Drafts: none yet"]
        out = ["", "## Drafts"]
        for d in rows:
            out.append(f"  #{d['draft_id']} v{d['version']} [{d['status']}] {d['text']}")
        return out

    def _notes_lines(self, book, chapter, verse):
        rows = self.project.execute(
            "SELECT kind, note FROM verse_notes WHERE book = ? AND chapter = ? AND verse = ? "
            "AND kind NOT LIKE 'legacy_%' ORDER BY note_id", (book, chapter, verse)).fetchall()
        legacy = self.project.execute(
            "SELECT COUNT(*) FROM verse_notes WHERE book = ? AND chapter = ? AND verse = ? "
            "AND kind LIKE 'legacy_%'", (book, chapter, verse)).fetchone()[0]
        out = []
        if rows:
            out += ["", "## Notes"] + [f"  [{r['kind']}] {r['note']}" for r in rows]
        if legacy:
            out += ["", f"({legacy} earlier Gateway note(s) exist for this verse; reference only.)"]
        qere = self.source.execute(
            "SELECT catch_word, reading_type, surface FROM source_notes WHERE book = ? AND chapter = ? "
            "AND verse = ? AND reading_type IS NOT NULL", (book, chapter, verse)).fetchall()
        if qere:
            out += ["", "## Source readings (ketiv and qere): translate the qere, note the ketiv"]
            out += [f"  {q['catch_word']} -> {q['reading_type']}: {q['surface']}" for q in qere]
        return out

    # ------------------------------------------------------------------
    # add
    # ------------------------------------------------------------------
    def add(self, session_id, book, chapter, verse, text, alignment_path, status="candidate"):
        """Record one draft version with its alignment. Everything is checked before anything is written."""
        self._require_session(session_id, "translation")
        if status not in ("candidate", "seed", "draft"):
            sys.exit("--status must be candidate, seed or draft.")
        if not text.strip():
            sys.exit("The draft text is empty.")
        words = self.verse_words(book, chapter, verse)
        by_position = {w["position"]: w for w in words}
        rows = self._read_alignment(alignment_path)
        glossary = self.glossary_for({w["lemma_id"] for w in words if w["lemma_id"]})

        problems, warnings, aligned = [], [], []
        seen = set()
        for number, row in enumerate(rows, start=2):                  # row 1 is the header
            try:
                position = int(row["position"])
            except (TypeError, ValueError):
                problems.append(f"row {number}: position is not a number")
                continue
            word = by_position.get(position)
            if word is None:
                problems.append(f"row {number}: this verse has no source part at position {position}")
                continue
            if word["lemma_id"] is None:
                continue            # suffixes and other parts without a lemma are not aligned
            seen.add(position)
            result = self._check_row(number, word, row, glossary.get(word["lemma_id"], []), text)
            if isinstance(result, str):
                problems.append(result)
            else:
                aligned.append(result["row"])
                warnings += result["warnings"]
        for w in words:
            if w["lemma_id"] and w["position"] not in seen:
                problems.append(f"position {w['position']} ({w['surface']}) is missing from the alignment")

        problems += self._phrase_problems(words, rows, text)

        if problems:
            print("Nothing was saved. Fix these first:")
            for p in problems:
                print("  - " + p)
            sys.exit(1)

        version = self.project.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM drafts WHERE book = ? AND chapter = ? AND verse = ?",
            (book, chapter, verse)).fetchone()[0]
        with self.project:                                            # one transaction
            cur = self.project.execute(
                "INSERT INTO drafts (book, chapter, verse, version, text, status, session_id) "
                "VALUES (?,?,?,?,?,?,?)", (book, chapter, verse, version, text.strip(), status, session_id))
            draft_id = cur.lastrowid
            for a in aligned:
                self.project.execute(
                    "INSERT INTO draft_alignment (draft_id, word_id, glossary_id, rendered_as, departs, "
                    "departure_note) VALUES (?,?,?,?,?,?)",
                    (draft_id, a["word_id"], a["glossary_id"], a["rendered_as"], a["departs"], a["note"]))
        lines = [f"Saved draft #{draft_id}: {book_name(book)} {chapter}:{verse} version {version} [{status}], "
                 f"{len(aligned)} words aligned."]
        lines += ["  warning: " + w for w in warnings]
        return "\n".join(lines) + "\n"

    def _phrase_problems(self, words, rows, text):
        """Problems for the approved phrases found in this verse.

        A phrase passes when its text appears in the draft as one unit, or when every member
        word is marked as a departure with a note. The sense column must name the phrase's sense.
        """
        by_position = {}
        for row in rows:
            try:
                by_position[int(row["position"])] = row
            except (TypeError, ValueError):
                pass
        problems = []
        for m in find_matches(self.project, words, only_established=True):
            member_rows = [by_position.get(p) for p in m["positions"]]
            if any(r is None for r in member_rows):
                continue                      # a missing row is reported by the normal check
            departing = all((r["departs"] or "").strip() in ("1", "y", "yes") for r in member_rows)
            if departing:
                continue
            for r, (lemma_id, sense) in zip(member_rows, m["members"]):
                if (r["sense"] or "").strip() != sense:
                    problems.append(f"phrase {m['name']}: the sense for position {r['position']} must be '{sense}'")
            if norm(m["rendering"]) not in norm(text):
                problems.append(f"phrase {m['name']}: the text must contain '{m['rendering']}' as one unit "
                                "(or mark every member word as a departure with a note)")
        return problems

    @staticmethod
    def _read_alignment(path):
        """Read the alignment CSV and check that it has the expected columns."""
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            sys.exit(f"Alignment file not found: {path}")
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            missing = [c for c in TEMPLATE_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                sys.exit("The alignment file lacks these columns: " + ", ".join(missing))
            return list(reader)

    def _check_row(self, number, word, row, entries, text):
        """Check one alignment row against the glossary. Returns a problem string or a result dict."""
        label = f"row {number} ({word['surface']})"
        ready = [e for e in entries if e["status"] in ESTABLISHED]
        if not ready:
            return f"{label}: no approved glossary entry for lemma {word['lemma_id']}. Ask for a lexical session."
        sense = (row["sense"] or "").strip()
        if not sense:
            if len(ready) > 1:
                return f"{label}: this lemma has {len(ready)} senses; name one in the sense column"
            entry = ready[0]
        else:
            matches = [e for e in ready if e["sense_label"] == sense]
            if not matches:
                return f"{label}: no approved sense '{sense}' (have: {', '.join(e['sense_label'] for e in ready)})"
            entry = matches[0]
        rendered = (row["rendered_as"] or "").strip()
        if not rendered:
            return f"{label}: rendered_as is empty"
        departs = (row["departs"] or "").strip() in ("1", "y", "yes")
        note = (row["note"] or "").strip()
        in_glossary = norm(rendered) in self.allowed_words(entry)
        if not in_glossary and not departs:
            return (f"{label}: '{rendered}' is not the glossary rendering ('{entry['rendering']}'"
                    f"{', also ' + entry['allowed_variants'] if entry['allowed_variants'] else ''}). "
                    "Use the glossary word, or put 1 in departs and explain in note.")
        if departs and not note:
            return f"{label}: a departure needs an explanation in the note column"
        warnings = []
        base = norm(rendered)
        if rendered != "(none)" and len(base) >= 3 and base[:3] not in norm(text):
            warnings.append(f"{label}: nothing in the text starts like '{rendered}'")
        return {"row": {"word_id": word["word_id"], "glossary_id": entry["glossary_id"],
                        "rendered_as": rendered, "departs": 1 if departs else 0, "note": note or None},
                "warnings": warnings}

    # ------------------------------------------------------------------
    # choose, note, show
    # ------------------------------------------------------------------
    def choose(self, session_id, draft_id, seed=False, reason=None):
        """Make one version the chosen one. The other open versions of the verse become rejected."""
        self._require_session(session_id, "translation")
        d = self.project.execute("SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()
        if d is None:
            sys.exit(f"No draft #{draft_id}.")
        if d["status"] in ("checked", "frozen", "legacy"):
            sys.exit(f"Draft #{draft_id} is {d['status']} and cannot be re-chosen here.")
        if not (reason or "").strip():
            sys.exit("A reason is required (--reason), so the choice can be understood later.")
        new_status = "seed" if seed else "draft"
        with self.project:
            self.project.execute(
                "UPDATE drafts SET status = 'rejected', reason = COALESCE(reason, 'Another version was chosen') "
                "WHERE book = ? AND chapter = ? AND verse = ? AND draft_id <> ? AND status IN "
                "('candidate','seed','draft')", (d["book"], d["chapter"], d["verse"], draft_id))
            self.project.execute("UPDATE drafts SET status = ?, reason = ? WHERE draft_id = ?",
                                 (new_status, reason.strip(), draft_id))
        return (f"Draft #{draft_id} ({book_name(d['book'])} {d['chapter']}:{d['verse']} v{d['version']}) "
                f"is now [{new_status}]. Other open versions of the verse are rejected.\n")

    def add_note(self, session_id, book, chapter, verse, kind, text, draft_id=None):
        """Add a verse note."""
        self._require_session(session_id, "translation")
        if kind not in ("lexical", "textual", "difficulty", "other"):
            sys.exit("--kind must be lexical, textual, difficulty or other.")
        if not text.strip():
            sys.exit("The note is empty.")
        cur = self.project.execute(
            "INSERT INTO verse_notes (book, chapter, verse, kind, note, draft_id, session_id) VALUES (?,?,?,?,?,?,?)",
            (book, chapter, verse, kind, text.strip(), draft_id, session_id))
        self.project.commit()
        return f"Note {cur.lastrowid} saved on {book_name(book)} {chapter}:{verse} [{kind}].\n"

    def show(self, book, chapter, verse=None):
        """Print the chosen text of a chapter, with other versions and notes indented below."""
        sql = "SELECT * FROM drafts WHERE book = ? AND chapter = ? "
        params = [book, chapter]
        if verse:
            sql += "AND verse = ? "
            params.append(verse)
        rows = self.project.execute(sql + "ORDER BY verse, version", params).fetchall()
        if not rows:
            return "No drafts here yet.\n"
        out, current = [f"# {book_name(book)} {chapter}", ""], None
        for d in rows:
            if d["verse"] != current:
                current = d["verse"]
                out.append(f"Verse {current}")
            out.append(f"  #{d['draft_id']} v{d['version']} [{d['status']}] {d['text']}")
            if d["reason"]:
                out.append(f"      reason: {d['reason']}")
        notes = self.project.execute(
            "SELECT verse, kind, note FROM verse_notes WHERE book = ? AND chapter = ? AND kind NOT LIKE 'legacy_%' "
            + ("AND verse = ? " if verse else "") + "ORDER BY verse, note_id", params).fetchall()
        if notes:
            out += ["", "Notes"] + [f"  {n['verse']} [{n['kind']}] {n['note']}" for n in notes]
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------
    def _require_session(self, session_id, role):
        """The session must exist, be open, and have the right role."""
        s = self.project.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if s is None:
            sys.exit(f"No session {session_id}. Start one with handoff.py start.")
        if s["ended_at"]:
            sys.exit(f"Session {session_id} is closed.")
        if s["role"] != role:
            sys.exit(f"Session {session_id} is a {s['role']} session. Only a {role} session may do this.")


def main():
    parser = argparse.ArgumentParser(description="Draft verses against the glossary")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, need_source=False, verse_required=True):
        p.add_argument("--project", required=True)
        p.add_argument("--source", required=need_source)
        p.add_argument("--book", required=True)
        p.add_argument("--chapter", required=True, type=int)
        p.add_argument("--verse", type=int, required=verse_required)

    sheet = sub.add_parser("sheet", help="print the verse sheet")
    common(sheet, need_source=True)
    sheet.add_argument("--template", help="write the alignment CSV to fill in")

    add = sub.add_parser("add", help="record a draft version")
    common(add, need_source=True)
    add.add_argument("--session", required=True, type=int)
    add.add_argument("--text", required=True)
    add.add_argument("--alignment", required=True, help="the alignment CSV")
    add.add_argument("--status", default="candidate", choices=["candidate", "seed", "draft"])

    choose = sub.add_parser("choose", help="choose one version of a verse")
    choose.add_argument("--project", required=True)
    choose.add_argument("--session", required=True, type=int)
    choose.add_argument("--draft-id", required=True, type=int)
    choose.add_argument("--seed", action="store_true", help="mark as a provisional seed verse")
    choose.add_argument("--reason", required=True)

    note = sub.add_parser("note", help="add a verse note")
    common(note)
    note.add_argument("--session", required=True, type=int)
    note.add_argument("--kind", default="lexical", choices=["lexical", "textual", "difficulty", "other"])
    note.add_argument("--text", required=True)
    note.add_argument("--draft-id", type=int)

    show = sub.add_parser("show", help="print drafts and notes")
    common(show, verse_required=False)

    args = parser.parse_args()
    tool = DraftTool(args.project, getattr(args, "source", None))
    book = book_number(args.book) if hasattr(args, "book") else None
    if args.command == "sheet":
        print(tool.sheet(book, args.chapter, args.verse, args.template))
    elif args.command == "add":
        print(tool.add(args.session, book, args.chapter, args.verse, args.text, args.alignment, args.status))
    elif args.command == "choose":
        print(tool.choose(args.session, args.draft_id, args.seed, args.reason))
    elif args.command == "note":
        print(tool.add_note(args.session, book, args.chapter, args.verse, args.kind, args.text, args.draft_id))
    else:
        print(tool.show(book, args.chapter, args.verse))


if __name__ == "__main__":
    main()
