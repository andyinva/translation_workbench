#!/usr/bin/env python3
"""
create_structure.py

Builds the folder layout for the translation_workbench project.
Works the same on Ubuntu, WSL and Windows 11 because it uses pathlib.

Usage:
    python create_structure.py                  (run from inside translation_workbench)
    python create_structure.py /path/to/folder  (or give the folder explicitly)

The script is safe to run more than once. It never deletes anything and it
never overwrites a file that already exists.
"""

import sys
from pathlib import Path


class WorkbenchBuilder:
    """Creates the standard directory tree and starter files."""

    # Folders to create, written relative to the workbench root.
    FOLDERS = [
        "engine",                        # shared Python code (classes, importers, checkers)
        "sources",                       # original-language texts and lexicon data
        "projects/english_literal",      # first translation project
        "projects/english_literal/notes",  # session handoffs and exports
        "docs",                          # plan, schema notes, session instructions
    ]

    # Starter files: path relative to the root -> text to put inside.
    STARTER_FILES = {
        "README.md": (
            "# translation_workbench\n\n"
            "engine/    shared Python code used by every project\n"
            "sources/   original-language texts and lexicon data (shared)\n"
            "projects/  one folder per translation project, each with its own database\n"
            "docs/      plans, schema notes and session instructions\n"
        ),
        "projects/english_literal/style_rules.md": (
            "# Style rules: english_literal\n\n"
            "Style profile: literal\n\n"
            "## Rules\n"
            "- (add the translation philosophy and rules for this project here)\n"
        ),
        "projects/english_literal/notes/README.md": (
            "Session handoff summaries and exported notes for this project go here.\n"
        ),
        "docs/README.md": (
            "Planning documents, database schema notes and session instructions go here.\n"
        ),
        "engine/__init__.py": (
            '"""Shared engine code for translation_workbench."""\n'
        ),
        # MIT license text. Change the year or name here if needed.
        "LICENSE": (
            "MIT License\n\n"
            "Copyright (c) 2026 Andrew Hopkins\n\n"
            "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
            "of this software and associated documentation files (the \"Software\"), to deal\n"
            "in the Software without restriction, including without limitation the rights\n"
            "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
            "copies of the Software, and to permit persons to whom the Software is\n"
            "furnished to do so, subject to the following conditions:\n\n"
            "The above copyright notice and this permission notice shall be included in all\n"
            "copies or substantial portions of the Software.\n\n"
            "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n"
            "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
            "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
            "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
            "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
            "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
            "SOFTWARE.\n"
        ),
        # Keeps databases, caches and private files out of Git.
        ".gitignore": (
            "# SQLite databases (publish these as release files instead)\n"
            "*.db\n"
            "*.db-journal\n"
            "*.sqlite\n\n"
            "# Python caches and virtual environments\n"
            "__pycache__/\n"
            "*.pyc\n"
            ".venv/\n"
            "venv/\n\n"
            "# Local settings and editor files\n"
            ".env\n"
            ".vscode/\n"
            ".idea/\n\n"
            "# Raw downloaded source texts (record where to get them in docs instead)\n"
            "sources/raw/\n"
        ),
    }

    def __init__(self, root: Path):
        # Store the root folder as an absolute, fully resolved path.
        self.root = root.expanduser().resolve()

    def build(self) -> None:
        """Create every folder, then every starter file."""
        if not self.root.is_dir():
            # Stop early if the root folder was not created yet.
            sys.exit(f"Root folder not found: {self.root}")

        print(f"Building structure in: {self.root}")
        self._make_folders()
        self._make_files()
        print("Done.")

    def _make_folders(self) -> None:
        """Create each folder, including any missing parent folders."""
        for rel in self.FOLDERS:
            folder = self.root / rel
            # exist_ok=True means running the script twice causes no error.
            folder.mkdir(parents=True, exist_ok=True)
            print(f"  folder: {rel}")

    def _make_files(self) -> None:
        """Write each starter file, skipping any that already exist."""
        for rel, text in self.STARTER_FILES.items():
            path = self.root / rel
            if path.exists():
                print(f"  kept:   {rel} (already exists)")
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"  file:   {rel}")


def main() -> None:
    # Use the folder given on the command line, or the current folder.
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    WorkbenchBuilder(root).build()


if __name__ == "__main__":
    main()
