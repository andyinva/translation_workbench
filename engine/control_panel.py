#!/usr/bin/env python3
"""
control_panel.py  (place this file in translation_workbench/engine/, next to panel_data.py)

The control panel for the translation workbench. Stage 1 has one tab:

    Review   the glossary entries that wait for a decision. For each one you can
             approve it, approve it after editing the wording, reject it, or only
             leave a comment. Every decision goes through the same tools as the
             command line (glossary.py), so the checks and the decision log still apply.

Your decisions and comments are also saved in control/user_notes.json in the repo,
and Claude writes its comments to control/claude_notes.json. The panel shows
Claude's comments next to the entry they are about.

Run it from the engine folder:
    python3 control_panel.py

It needs PyQt6:  pip install PyQt6
"""

import argparse
import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, QAbstractItemView,
)

from panel_data import NotesChannel, PanelError, ReviewStore

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROJECT = os.path.join(HERE, "..", "projects", "english_literal", "project.db")
DEFAULT_SOURCE = os.path.join(HERE, "..", "sources", "source_texts.db")
DEFAULT_CONTROL = os.path.join(HERE, "..", "control")

CATEGORIES = ["word", "name", "divine_name", "term", "translit"]


class ReviewTab(QWidget):
    """The list of proposed entries on the left and the decision form on the right."""

    def __init__(self, store, notes, status_bar):
        super().__init__()
        self.store = store
        self.notes = notes
        self.status_bar = status_bar
        self.rows = []              # the proposed entries now in the table
        self.current = None         # the entry now shown in the form
        self._build()
        self.reload_sessions()
        self.reload()

    # ------------------------------------------------------------------
    # Building the screen
    # ------------------------------------------------------------------
    def _build(self):
        outer = QVBoxLayout(self)

        # Top bar: which lexical session the decisions are recorded in.
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Lexical session:"))
        self.session_box = QComboBox()
        self.session_box.setMinimumWidth(320)
        bar.addWidget(self.session_box)
        new_button = QPushButton("Start a new session")
        new_button.clicked.connect(self.start_session)
        bar.addWidget(new_button)
        bar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by English or Hebrew ...")
        self.search.textChanged.connect(self.fill_table)
        self.search.setMinimumWidth(240)
        bar.addWidget(self.search)
        self.only_claude = QCheckBox("Only entries Claude commented on")
        self.only_claude.stateChanged.connect(self.fill_table)
        bar.addWidget(self.only_claude)
        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(self.reload)
        bar.addWidget(reload_button)
        outer.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        # Left: the table of entries.
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Rank", "Hebrew", "Rendering", "Sense", "Claude"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 55)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 70)
        self.table.itemSelectionChanged.connect(self.show_selected)
        splitter.addWidget(self.table)

        # Right: the decision form.
        form_widget = QWidget()
        form_outer = QVBoxLayout(form_widget)

        self.title = QLabel("")
        title_font = QFont()
        title_font.setPointSize(22)
        self.title.setFont(title_font)
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form_outer.addWidget(self.title)
        self.subtitle = QLabel("")
        self.subtitle.setWordWrap(True)
        form_outer.addWidget(self.subtitle)

        form = QFormLayout()
        self.rendering = QLineEdit()
        form.addRow("Rendering:", self.rendering)
        self.allowed = QLineEdit()
        self.allowed.setPlaceholderText("other acceptable words, separated by semicolons")
        form.addRow("Also allowed:", self.allowed)
        kind = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItems(CATEGORIES)
        kind.addWidget(self.category)
        kind.addWidget(QLabel("Tier (names):"))
        self.tier = QSpinBox()
        self.tier.setRange(0, 3)
        self.tier.setSpecialValueText("none")
        kind.addWidget(self.tier)
        kind.addStretch()
        form.addRow("Kind:", kind)
        self.rule = QPlainTextEdit()
        self.rule.setFixedHeight(60)
        form.addRow("Rule:", self.rule)
        self.reason = QPlainTextEdit()
        self.reason.setFixedHeight(70)
        form.addRow("Reason:", self.reason)
        form_outer.addLayout(form)

        self.others = QLabel("")
        self.others.setWordWrap(True)
        form_outer.addWidget(self.others)

        form_outer.addWidget(QLabel("Where the word occurs:"))
        self.examples = QPlainTextEdit()
        self.examples.setReadOnly(True)
        self.examples.setFixedHeight(90)
        form_outer.addWidget(self.examples)

        form_outer.addWidget(QLabel("Claude's comments on this entry:"))
        self.claude_box = QPlainTextEdit()
        self.claude_box.setReadOnly(True)
        self.claude_box.setFixedHeight(70)
        form_outer.addWidget(self.claude_box)

        form_outer.addWidget(QLabel("Your comment (for Claude to read):"))
        self.comment = QPlainTextEdit()
        self.comment.setFixedHeight(60)
        form_outer.addWidget(self.comment)

        buttons = QHBoxLayout()
        self.approve_button = QPushButton("Approve")
        self.approve_button.setToolTip("Approve the entry. If you changed any field it is saved as revised.")
        self.approve_button.clicked.connect(self.do_approve)
        self.reject_button = QPushButton("Reject")
        self.reject_button.setToolTip("Remove the entry. Needs a reason, written in the Reason box.")
        self.reject_button.clicked.connect(self.do_reject)
        self.comment_button = QPushButton("Save comment only")
        self.comment_button.clicked.connect(self.do_comment)
        for button in (self.approve_button, self.reject_button, self.comment_button):
            buttons.addWidget(button)
        buttons.addStretch()
        form_outer.addLayout(buttons)

        splitter.addWidget(form_widget)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([520, 700])
        self.set_form_enabled(False)

    def set_form_enabled(self, enabled):
        """The form is greyed out when no entry is selected."""
        for widget in (self.rendering, self.allowed, self.category, self.tier, self.rule, self.reason,
                       self.comment, self.approve_button, self.reject_button, self.comment_button):
            widget.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Loading and showing
    # ------------------------------------------------------------------
    def reload_sessions(self):
        """Fill the session box with the open lexical sessions."""
        self.session_box.clear()
        for s in self.store.lexical_sessions():
            self.session_box.addItem(f"Session {s['session_id']}: {s['scope']}", s["session_id"])
        if self.session_box.count() == 0:
            self.session_box.addItem("No open lexical session (press Start a new session)", None)

    def reload(self):
        """Read the proposed entries again."""
        self.rows = self.store.proposed()
        self.fill_table()
        self.status_bar.showMessage(f"{len(self.rows)} entries are waiting for a decision.")

    def fill_table(self):
        """Show the rows that match the filter."""
        text = self.search.text().strip().lower()
        flagged = self.notes.claude_lemma_ids()
        shown = []
        for r in self.rows:
            if self.only_claude.isChecked() and r["lemma_id"] not in flagged:
                continue
            haystack = " ".join(str(r[k] or "") for k in ("rendering", "headword", "transliteration", "gloss_hint")).lower()
            if text and text not in haystack:
                continue
            shown.append(r)
        self.shown = shown
        self.table.blockSignals(True)
        self.table.setRowCount(len(shown))
        for i, r in enumerate(shown):
            cells = [str(r["rank_most"] or "-"), r["headword"] or "", r["rendering"], r["sense_label"],
                     "note" if r["lemma_id"] in flagged else ""]
            for j, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if j == 4 and value:
                    item.setForeground(QColor("#b03a00"))
                self.table.setItem(i, j, item)
        self.table.blockSignals(False)
        self.current = None
        self.set_form_enabled(False)
        if shown:
            self.select_index(0)

    def selected_index(self):
        """The row number now selected in the table (-1 if none)."""
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else self.table.currentRow()

    def select_index(self, index):
        """Select a row and make it the current one, so the form follows."""
        self.table.setCurrentCell(index, 0)
        self.table.selectRow(index)
        # If the row was already selected Qt sends no signal, so fill the form directly.
        self.show_selected()

    def selected_row(self):
        index = self.selected_index()
        return self.shown[index] if 0 <= index < len(self.shown) else None

    def show_selected(self):
        """Fill the form with the entry that was clicked."""
        r = self.selected_row()
        if r is None:
            return
        self.current = r
        self.title.setText(f"{r['headword'] or ''}   {r['transliteration'] or ''}")
        rank = f"rank {r['rank_most']}" if r["rank_most"] else "not ranked"
        strongs = r["strongs"] or "no Strong's number"
        self.subtitle.setText(
            f"Lemma {r['lemma_id']} ({strongs}), sense '{r['sense_label']}'. "
            f"Lexicon gloss: {r['gloss_hint'] or '-'}. {r['uses'] or 0} uses, {rank}.")
        self.rendering.setText(r["rendering"] or "")
        self.allowed.setText(r["allowed_variants"] or "")
        self.category.setCurrentText(r["category"])
        self.tier.setValue(r["tier"] or 0)
        self.rule.setPlainText(r["rule"] or "")
        self.reason.setPlainText(r["reason"] or "")
        self.comment.clear()
        others = self.store.other_senses(r["lemma_id"], r["sense_label"])
        self.others.setText("Other senses of this word: " + "; ".join(
            f"{o['sense_label']} = {o['rendering']} [{o['status']}]" for o in others) if others else "")
        self.examples.setPlainText("\n".join(f"{ref}    {form}" for ref, form in self.store.examples(r["lemma_id"])))
        notes = self.notes.claude_notes_for(r["lemma_id"], r["sense_label"])
        self.claude_box.setPlainText("\n\n".join(f"[{n.get('time', '')}] {n.get('text', '')}" for n in notes))
        self.set_form_enabled(True)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def session_id(self):
        """The chosen session number, or None (and a message) when there is none."""
        value = self.session_box.currentData()
        if value is None:
            QMessageBox.information(self, "Session needed",
                                    "Start a lexical session first (the button at the top). Decisions are recorded in it.")
        return value

    def start_session(self):
        scope, ok = QInputDialog.getText(self, "New lexical session", "What is this session for?",
                                         text="Review of proposed entries")
        if not ok:
            return
        try:
            number = self.store.start_lexical_session(scope)
        except PanelError as problem:
            QMessageBox.warning(self, "Not started", str(problem))
            return
        self.reload_sessions()
        self.status_bar.showMessage(f"Lexical session {number} started.")

    def fields(self):
        """The values now in the form."""
        tier = self.tier.value() or None
        return {"rendering": self.rendering.text(), "allowed": self.allowed.text().strip(),
                "rule": self.rule.toPlainText().strip(), "category": self.category.currentText(),
                "tier": tier, "reason": self.reason.toPlainText().strip()}

    def after_action(self, row_index):
        """Reload the list and select the entry that took the place of the one just settled."""
        self.reload()
        if self.shown:
            self.select_index(min(row_index, len(self.shown) - 1))

    def do_approve(self):
        r, session = self.current, self.session_id()
        if r is None or session is None:
            return
        index = self.selected_index()
        try:
            status = self.store.approve(session, r["lemma_id"], r["sense_label"], self.fields())
        except PanelError as problem:
            QMessageBox.warning(self, "Not approved", str(problem))
            return
        self.notes.add_user_note(r["lemma_id"], r["sense_label"], status, self.comment.toPlainText(),
                                 before=r["rendering"], after=self.rendering.text().strip())
        self.status_bar.showMessage(f"{r['rendering']} -> {self.rendering.text().strip()}: {status}.")
        self.after_action(index)

    def do_reject(self):
        r, session = self.current, self.session_id()
        if r is None or session is None:
            return
        reason = self.reason.toPlainText().strip()
        if not reason:
            QMessageBox.warning(self, "Reason needed", "Write why in the Reason box before rejecting.")
            return
        answer = QMessageBox.question(self, "Reject this entry?",
                                      f"'{r['rendering']}' will be removed from the glossary. The removal and "
                                      "your reason stay in the decision log.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        index = self.selected_index()
        try:
            self.store.reject(session, r["lemma_id"], r["sense_label"], reason)
        except PanelError as problem:
            QMessageBox.warning(self, "Not rejected", str(problem))
            return
        self.notes.add_user_note(r["lemma_id"], r["sense_label"], "rejected",
                                 reason + ("\n" + self.comment.toPlainText() if self.comment.toPlainText().strip() else ""),
                                 before=r["rendering"])
        self.status_bar.showMessage(f"Rejected '{r['rendering']}'.")
        self.after_action(index)

    def do_comment(self):
        r = self.current
        text = self.comment.toPlainText().strip()
        if r is None or not text:
            QMessageBox.information(self, "Nothing to save", "Write a comment first.")
            return
        self.notes.add_user_note(r["lemma_id"], r["sense_label"], "comment", text, before=r["rendering"])
        self.comment.clear()
        self.status_bar.showMessage("Comment saved for Claude.")


class ControlPanel(QMainWindow):
    """The main window. Later stages add more tabs next to Review."""

    def __init__(self, project, source, control):
        super().__init__()
        self.setWindowTitle("Translation Workbench - Control Panel")
        self.resize(1250, 780)
        store = ReviewStore(project, source)
        notes = NotesChannel(control)
        tabs = QTabWidget()
        tabs.addTab(ReviewTab(store, notes, self.statusBar()), "Review")
        self.setCentralWidget(tabs)


def main():
    parser = argparse.ArgumentParser(description="Control panel for the translation workbench")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--control", default=DEFAULT_CONTROL, help="folder for the notes files")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    try:
        window = ControlPanel(os.path.abspath(args.project), os.path.abspath(args.source),
                              os.path.abspath(args.control))
    except PanelError as problem:
        QMessageBox.critical(None, "Cannot start", str(problem))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
