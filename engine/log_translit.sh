#!/bin/bash
# log_translit.sh: records the transliteration rule as a decision in the open lexical session (10).
# Run it once. It only adds one row to the decisions table.
cd /home/andrew/projects/translation_workbench/engine || exit 1

python3 log_decision.py add \
  --project ../projects/english_literal/project.db \
  --session 10 \
  --topic "Transliterated words" \
  --summary "Some Hebrew words stay in plain transliteration (glossary category translit). A word qualifies with two of five tests: no faithful English word; closed set; links passages English separates; uncertain identity; the text plays on the word. Measures qualify automatically. Familiar English form first unless it hides a link. Levels core and optional. First yovel (H3104, all 27 uses). Trial of set A on Lev 25, Josh 6, Ruth 4, Eccl 1, Exod 28." \
  --reason "The translation is for readers who want to know the text better. Keeping a word such as yovel in Hebrew form preserves links that translation hides (Josh 6, Exod 19, Lev 25)."
