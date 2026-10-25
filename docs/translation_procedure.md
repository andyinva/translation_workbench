# Translation procedure (proposed)

Status: proposed by Andrew on 2026-10-24. Not yet logged as a decision.
Put this file in `docs/`. It adds to `session_roles.md` and `style_rules.md`; it
does not replace them. Where it differs from the earlier plan (translate
Genesis 1 to 3 in book order), the differences are listed in section 7.

## 1. Idea in one paragraph

Translate the Old Testament in the order of influence, not the order of the
books. First rank every Hebrew word by how much it matters to the rest of the
text. Build a lexicon in that order, with a short list of English words allowed
for each sense. Then choose "translation sets": verses, scattered across the
Old Testament, that share an influential word. Translate each set together, then
grow it outward to whole chapters. Every verse is tried in two or three versions
and compared with other verses that share its influential words, so the choice
of English words is made against the largest context available, not just the
chapter.

## 2. Step 1: rank the words (the influence list)

Each Hebrew lemma gets a score from several measures. Candidate measures:

| Measure | Meaning | Source |
|---|---|---|
| Uses | How many times the lemma occurs | source_texts.db (`lemma_counts`) |
| Cross references | How often verses containing the lemma are cross referenced | bibles.db cross references |
| Shadow | How much influence the word casts over nearby words | Word Atlas |
| Pull | How strongly other words draw on the word | Word Atlas |
| Motifs | Whether the lemma belongs to a motif | Scripture Motifs |

Rules:

- Each measure is stored separately, never only as one blended number, so the
  weights can be changed later and the effect seen.
- Two orderings are kept: **most influential first** (decides what is translated
  first) and **least influential first** (the tail of the lexicon, handled last
  or in bulk).
- A separate **difficulty** flag marks lemmas that are rare or uncertain
  (one-time words, disputed meaning). Rare is not the same as unimportant: a
  word can be low in influence and still need care.
- Word Atlas measures were built on KJV roots joined through Strong's numbers.
  They are joined to Hebrew lemmas by Strong's number and the join is
  approximate. This is recorded in the table notes.

## 3. Step 2: build the lexicon

For each lemma, in influence order:

1. Group its uses under senses (the existing glossary `sense_label`).
2. For each sense, list the English words that may be used (the existing
   `rendering` plus `allowed_variants`).
3. For each of those English words, list synonyms that the lexical session
   accepts as alternatives. These are what lets a verse set offer many options.
4. Record the reason for each choice (`reason`), as now.

Rule: **only words established in the lexicon may be used in a translation.**
If a verse needs a word that is not there, a lexical session adds it first
(with a reason). A translation session never adds one.

## 4. Step 3: choose translation sets

- Score each verse by the influence of the words in it and around it.
- Pick an anchor lemma (for example one very influential word that occurs 10
  times). The verses that contain it, wherever they are in the Old Testament,
  form one translation set.
- A set is translated together so related texts mature together.
- After the set, translate the chapters those verses belong to. Chapters
  fill in around the early verses.

## 5. Step 4: translate a verse (the verse set)

1. List the lexicon words and synonyms for every word of the verse. This list
   is the **verse set**.
2. Build two or three versions of the verse from it.
3. Compare each version with the other verses that share its influential
   words, whether in the same set or elsewhere in the Old Testament.
4. Choose one version. Keep the others, with the reason for the choice.
5. A departure from the lexicon is recorded in `draft_alignment`
   (`departs = 1`) as now.

## 6. What the tools need

New or changed (to be built, none exists yet):

- `word_priority` table in the project database: one row per lemma with each
  measure, difficulty flag, and the date and source of the numbers.
- `import_priority.py`: reads the Word Atlas and cross reference numbers and
  fills `word_priority`. Read only access to atlas.db and bibles.db.
- `lexicon_synonyms` table: sense, English word, accepted synonym, note.
- `translation_sets` and `translation_set_verses` tables: anchor lemma, verses,
  status.
- Candidate versions: a verse can hold two or three candidates, with one
  chosen.
- `verse_sheet.py`: for one verse, shows each word with its lexicon options,
  and the other verses that share its influential words with their current
  renderings (the verse version of `word_sheet.py`).
- A list command: translation sets in order, with progress.

## 7. How this changes the earlier plan

- Order of work changes from book order to influence order (style_rules.md,
  section 10, point 1 would be replaced).
- The glossary and word sheet already built are kept; they become the core of
  the lexicon.
- Genesis 1 to 3 can stay as the test of the machinery, because the text is
  known well.
- The verse-by-verse "draft one section" model needs the candidate versions
  described above.

## 8. Risks to watch

- A verse translated early, away from its story, may fit the anchor word and
  not the narrative. The chapter step and the quality session are the defence.
- Influence measures built on the KJV may repeat the KJV's choices. The Hebrew
  lemma counts and the motif data are the counterweight.
- Particles, prefixes and verb forms are frequent but not "influential" in
  this sense. They need rules in `style_rules.md`, not a ranking.
- The lexicon rule can slow work if adding words is hard. A lexical session
  should be quick to run when a gap is found.
