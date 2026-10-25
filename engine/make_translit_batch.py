#!/usr/bin/env python3
"""
make_translit_batch.py  (place this file in translation_workbench/engine/)

Builds a CSV of proposed glossary entries (category translit) from the candidate
list in docs/translit_candidates.md, ready for `glossary.py load`.

How it works:
  1. CandidateList reads the tables of the candidate file (one row per word).
  2. LemmaFinder looks up each Strong's number in the source database.
       - exactly one lemma has the number: a row is written
       - none or several lemmas share the number: no row, and the report says why
         (a human must pick the lemma)
  3. Words that already have a glossary entry are never touched. They are listed
     in the report, so an approved entry is never replaced by a proposal.
  4. The source and project databases are opened read only. Only the CSV is written.

Level (kept in the rule field): strong and rule words are `core`; maybe and
familiar words are `optional`. The held group (torah, hesed and so on) is skipped.
"""

import argparse
import csv
import os
import re
import sqlite3
import sys

# Groups of the candidate file that are not turned into proposals.
SKIPPED_GROUPS = ("Held:",)

# Words that need special handling: Strong's key -> dict(sense, note, skip).
SPECIAL = {
    # The source mixes two words under this number; a human must look first.
    "H8577": {"skip": "the source mixes sea monster and jackal under this number; check the lemmas first"},
    # Two meanings: only the weight is transliterated.
    "H3603": {"sense": "talent", "note": "Weight only (talent). The plain or circle of the Jordan stays English (plain)."},
    "H2563": {"sense": "measure", "note": "Measure only. The sense clay or mortar stays English."},
    "H1350": {"note": "Decide: noun only (kinsman-redeemer) or noun and verb. 104 uses; five in Ruth 4:4."},
    "H1892": {"note": "Also the name Abel (Gen 4); that name is a separate entry."},
}

# The level of each strength class.
LEVEL = {"strong": "core", "rule": "core", "maybe": "optional", "familiar": "optional"}


def strongs_key(text):
    """Turn 'H0430', 'H430' or 'H430a' into 'H430'. Return None when there is no number."""
    match = re.match(r"\s*H0*(\d+)", text or "")
    return "H" + match.group(1) if match else None


class CandidateList:
    """Reads the candidate tables from the markdown file."""

    def __init__(self, path):
        self.path = path

    def rows(self):
        """Return a list of dicts: word, strongs, uses, tests, strength, note, group."""
        found = []
        group = None
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.rstrip("\n")
                if line.startswith("### "):
                    group = line[4:].strip()
                    continue
                if group is None or not line.startswith("|") or line.startswith("| Word") or line.startswith("|---"):
                    continue
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) < 8:
                    continue
                found.append({"word": cells[0], "strongs": strongs_key(cells[1]), "uses": cells[3],
                              "tests": cells[5], "strength": cells[6], "note": cells[7], "group": group})
        return found


class LemmaFinder:
    """Finds source lemmas by Strong's number, and checks the glossary for existing entries."""

    def __init__(self, source_path, project_path):
        for path in (source_path, project_path):
            if not os.path.isfile(path):
                sys.exit(f"File not found: {path}")
        # Both databases are opened read only.
        self.source = sqlite3.connect("file:" + os.path.abspath(source_path) + "?mode=ro", uri=True)
        self.project = sqlite3.connect("file:" + os.path.abspath(project_path) + "?mode=ro", uri=True)
        self.project.row_factory = sqlite3.Row      # so columns can be read by name
        self.by_key = {}
        for lemma_id, number, headword in self.source.execute(
                "SELECT lemma_id, strongs, headword FROM lemmas"):
            key = strongs_key(number)
            if key:
                self.by_key.setdefault(key, []).append((lemma_id, headword))

    def lemmas(self, key):
        """The (lemma_id, headword) pairs that carry this Strong's number."""
        return self.by_key.get(key, [])

    def existing(self, lemma_id, sense):
        """The stored glossary row for this lemma and sense, or None."""
        return self.project.execute(
            "SELECT rendering, status, category FROM glossary WHERE lemma_id = ? AND sense_label = ?",
            (lemma_id, sense)).fetchone()


class BatchBuilder:
    """Combines the list and the finder into CSV rows and a report."""

    COLUMNS = ["lemma_id", "sense", "category", "tier", "rendering", "allowed", "rule", "reason", "status"]

    def __init__(self, candidates, finder):
        self.candidates = candidates
        self.finder = finder
        self.rows = []
        self.report = []

    def build(self):
        """Go through every candidate and fill self.rows and self.report."""
        for cand in self.candidates.rows():
            word, key = cand["word"], cand["strongs"]
            if cand["group"].startswith(SKIPPED_GROUPS):
                self.report.append(f"SKIPPED (held group)       {word} {key}")
                continue
            special = SPECIAL.get(key, {})
            if special.get("skip"):
                self.report.append(f"CHECK FIRST                {word} {key}: {special['skip']}")
                continue
            lemmas = self.finder.lemmas(key)
            if len(lemmas) != 1:
                names = ", ".join(f"{lid} {hw}" for lid, hw in lemmas) or "none found"
                self.report.append(f"PICK THE LEMMA BY HAND      {word} {key}: {names}")
                continue
            lemma_id = lemmas[0][0]
            sense = special.get("sense", "main")
            old = self.finder.existing(lemma_id, sense)
            if old is not None:
                self.report.append(f"ALREADY HAS AN ENTRY       {word} {key} lemma {lemma_id}: "
                                   f"'{old['rendering']}' [{old['status']}], left unchanged")
                continue
            level = LEVEL.get(cand["strength"], "optional")
            rule = f"Level: {level}. " + (special.get("note", "") or "")
            reason = (f"Transliteration rule (style_rules 4a): passes tests {cand['tests'] or 'none'}; "
                      f"strength {cand['strength']}; {cand['uses']} uses.")
            self.rows.append({"lemma_id": lemma_id, "sense": sense, "category": "translit", "tier": "",
                              "rendering": word, "allowed": "", "rule": rule.strip(), "reason": reason,
                              "status": "proposed"})
            self.report.append(f"PROPOSED                   {word} {key} lemma {lemma_id} [{level}]")

    def write(self, out_path):
        """Write the CSV file."""
        with open(out_path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.COLUMNS)
            writer.writeheader()
            writer.writerows(self.rows)


def main():
    parser = argparse.ArgumentParser(description="Build the translit proposals CSV")
    parser.add_argument("--candidates", required=True, help="docs/translit_candidates.md")
    parser.add_argument("--source", required=True, help="sources/source_texts.db")
    parser.add_argument("--project", required=True, help="projects/english_literal/project.db")
    parser.add_argument("--out", required=True, help="CSV file to write")
    args = parser.parse_args()

    builder = BatchBuilder(CandidateList(args.candidates), LemmaFinder(args.source, args.project))
    builder.build()
    builder.write(args.out)
    print("\n".join(builder.report))
    print(f"\n{len(builder.rows)} proposals written to {args.out}")


if __name__ == "__main__":
    main()
