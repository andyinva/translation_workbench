# Session roles and protocol

This document defines how working sessions are run in translation_workbench.
It adapts the session discipline from the Gateway Translation guides to the
database-centered design. Put it in `docs/`.

## Core rule

Every session has one role and one scope. Sessions never rely on each other's
memory. They share information only through the project database
(`projects/<name>/project.db`) and the source database
(`sources/source_texts.db`).

A session is given only what it needs for its scope: the briefing, the source
words for its section, and the glossary rows for the lemmas in that section.
It is never given the whole database. (The Gateway project uploaded an 18 MB
JSON file at the start of every session. That is what this design replaces.)

## The five roles

| Role | Job | Reads | Writes | Never touches |
|---|---|---|---|---|
| preparation | Import and check source texts, lexicon data and comparison data | source files | source database | drafts, glossary |
| lexical | Study every occurrence of a lemma, group uses into senses, decide the rendering | occurrences, other translations, lexicons | glossary, decisions | drafts |
| translation | Draft a section using the approved glossary | source words, glossary | drafts, draft_alignment, verse_notes | glossary decisions (it may only propose, with a note) |
| quality | Check drafts against the glossary and other translations | drafts, glossary, comparison data | flags | drafts |
| programming | Build and fix the tools | code, schema | code only | translation content |

A translation session that needs a word rendered differently from the glossary
does not change the glossary. It records the departure in `draft_alignment`
(departs = 1, with a departure_note) and the quality session decides what to do.

## Starting a session

1. State the role and the scope (book and chapter range, or a range of lemmas).
2. Generate the briefing from the database: current section, open flags, the
   style rules, and the last session summary. (The script `engine/handoff.py`
   will be written after the importers.)
3. Insert a row in `sessions` with the role and scope.
4. Confirm a recent backup of the project database exists.

## Ending a session

1. Run the checks for the section worked (glossary conformity, missing
   renderings, open flags). Zero unexplained issues are required before a
   section can be marked `checked`.
2. Write a short handoff summary into `sessions.summary`: what was done, what
   is open, what the next session should start with.
3. Set `sessions.ended_at`.
4. Back up the database to a dated copy. Keep the most recent copies and never
   overwrite an older one.
5. Commit any code changes to Git. Databases are never committed.

## Carried over from the Gateway project

- Validate before closing a session. Every session ends with the checks.
- Project metadata must describe the real scope. In this design the scope is
  calculated from the database, so it cannot drift the way the old
  "Genesis & Exodus" label did.
- Never overwrite an earlier version. Drafts are stored by version number.
- If a session starts losing consistency late in a long run (spelling slips,
  skipped entries), stop and start a fresh session. The handoff summary makes
  that cheap.
- Size sessions to what fits comfortably. The Gateway sessions managed about
  8 to 15 psalms before context limits were reached.
