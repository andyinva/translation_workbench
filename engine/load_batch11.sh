#!/bin/bash
# load_batch11.sh: logs the new transliteration rules, then loads batch 11 into lexical session 10.
# Changes only entries that are still 'proposed' (8290, 9099, 9438, 9346, 3790) and adds new senses and entries.
cd /home/andrew/projects/translation_workbench/engine || exit 1

python3 log_decision.py add \
  --project ../projects/english_literal/project.db \
  --session 10 \
  --topic "Transliterated words" \
  --summary "Phrases stay together (Yom Kippur, Yom Teruah, tohu va vohu; yom over day inside them). Puns stay together (Pesach and pasach, Hevel and hevel). Spelling: v for a bet without dagesh, b with dagesh. Test 6 applies only to widely known words (Torah and shalom qualify); less known words need two of tests 1 to 5." \
  --reason "Decided by the user on 2026-10-25."

python3 glossary.py load \
  --project ../projects/english_literal/project.db \
  --source ../sources/source_texts.db \
  --session 10 \
  ../projects/english_literal/proposals/batch11_translit.csv
