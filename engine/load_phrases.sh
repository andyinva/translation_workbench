#!/bin/bash
# load_phrases.sh: creates the phrase tables, adds the first three phrases and approves them (lexical session 10).
# Safe to run twice: a phrase that already exists is updated, then approved again.
cd /home/andrew/projects/translation_workbench/engine || exit 1
P=../projects/english_literal/project.db

python3 phrases.py add --project $P --session 10 --name "Yom Kippur" --rendering "Yom Kippur" \
  --members "3111:in festival names,3790:yom kippur" \
  --rule "Leviticus 23:27 and 28; 25:9. Yom stays yom inside the name." \
  --reason "Phrases stay together (decided by the user, 2026-10-25)"
python3 phrases.py add --project $P --session 10 --name "Yom Teruah" --rendering "Yom Teruah" \
  --members "3111:in festival names,9346:main" \
  --rule "Numbers 29:1. Yom stays yom inside the name." \
  --reason "Phrases stay together (decided by the user, 2026-10-25)"
python3 phrases.py add --project $P --session 10 --name "tohu va vohu" --rendering "tohu va vohu" \
  --members "9099:main,9438:main" \
  --rule "Genesis 1:2 and Jeremiah 4:23. The conjunction is part of the phrase." \
  --reason "Phrases stay together (decided by the user, 2026-10-25)"

for NAME in "Yom Kippur" "Yom Teruah" "tohu va vohu"; do
  python3 phrases.py approve --project $P --session 10 --name "$NAME" \
    --reason "Decided by the user on 2026-10-25: phrases stay together in transliteration."
done
python3 phrases.py list --project $P
