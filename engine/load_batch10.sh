#!/bin/bash
# load_batch10.sh: logs test 6 (public recognition) as a decision, then loads batch 10 into lexical session 10.
# Existing glossary entries are not touched: none of these lemmas has one.
cd /home/andrew/projects/translation_workbench/engine || exit 1

python3 log_decision.py add \
  --project ../projects/english_literal/project.db \
  --session 10 \
  --topic "Transliterated words" \
  --summary "Test 6: a word the public already knows by its Hebrew sound is transliterated (Yom Kippur, Pesach, Sukkot, Shavuot, matzah, menorah). Only names that occur in the text count; Rosh Hashanah and Hanukkah are not imported." \
  --reason "Readers who want to know the text better recognise these Hebrew names. Decided by the user on 2026-10-25."

python3 glossary.py load \
  --project ../projects/english_literal/project.db \
  --source ../sources/source_texts.db \
  --session 10 \
  ../projects/english_literal/proposals/batch10_translit.csv
