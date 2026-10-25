#!/bin/bash
# Opens a lexical session and loads batch 8 (the most influential words without glossary entries).
# The entries are loaded as "proposed". Nothing is approved here.
# Run:  bash ~/projects/translation_workbench/projects/english_literal/proposals/load_batch8.sh
set -e
cd ~/projects/translation_workbench/engine
P=../projects/english_literal/project.db
S=../sources/source_texts.db
python3 handoff.py start --project $P --source $S --role lexical --scope "Batch 8: 59 most influential words without glossary entries"
SID=$(python3 -c "import sqlite3;print(sqlite3.connect('$P').execute('select max(session_id) from sessions').fetchone()[0])")
python3 glossary.py load --project $P --source $S --session $SID ../projects/english_literal/proposals/batch8_proposals.csv
echo
echo "Lexical session $SID is open. Proposed entries are loaded. Tell Claude the session number."
