#!/bin/bash
# load_batch9.sh: builds the transliteration proposals and loads them into lexical session 10.
# Step 1 writes projects/english_literal/proposals/batch9_translit.csv and a report.
# Step 2 loads the CSV as proposed entries. Existing glossary entries are never changed.
cd /home/andrew/projects/translation_workbench/engine || exit 1

python3 make_translit_batch.py \
  --candidates ../docs/translit_candidates.md \
  --source ../sources/source_texts.db \
  --project ../projects/english_literal/project.db \
  --out ../projects/english_literal/proposals/batch9_translit.csv \
  | tee ../projects/english_literal/proposals/batch9_report.txt

python3 glossary.py load \
  --project ../projects/english_literal/project.db \
  --source ../sources/source_texts.db \
  --session 10 \
  ../projects/english_literal/proposals/batch9_translit.csv
