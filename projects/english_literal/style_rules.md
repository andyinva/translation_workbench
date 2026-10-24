# Style rules: English literal translation

Put this file in `projects/english_literal/` (replace the starter file there).
Every translation, lexical and quality session reads it before working
(`handoff.py start` prints its path).

Each rule is marked:

- **[decided]**: you made this decision. It is also logged in the `decisions` table.
- **[proposed]**: my default, not yet confirmed. Change it here when you decide,
  and log the change as a decision.
- **[open]**: a question with no default yet.

## 1. Purpose and style

- **[decided]** The first translation is literal English, in plain modern English
  words. It follows the order and structure of the original where English allows.
- **[proposed]** When a literal rendering would not be English at all, use the
  nearest plain English that keeps the same words, and record the choice in a
  verse note of kind `difficulty`.
- **[proposed]** No paraphrase, no explanation inside the text. Explanations go
  in verse notes.
- A later project for another language starts from a copy of this file. Only
  sections 1, 4 and 8 usually change (for Hindi, readability may outrank
  literalness, which is a style profile decision made per language).

## 2. Source texts

- **[decided]** Hebrew Bible: OSHB (the Westminster Leningrad Codex with lemmas).
  Text name `OSHB` in the source database.
- **[decided]** Greek New Testament: the Nestle-Aland text. In the database this is
  every TAGNT word whose `word_type` starts with N. The tools use this by default.
- **[decided]** Traditional (Textus Receptus and Byzantine) readings are not
  translated in the text. Each place where the traditional text differs in a way
  that changes the translation gets a verse note of kind `textual` giving the
  traditional reading. The database already holds these as `source_notes`
  (`variant_reading`, `spelling_variants`, `variant_note`).
- **[decided]** Ketiv and qere: translate the qere (the reading spoken aloud, the
  traditional reading). Record the written form (ketiv) in a verse note of kind
  `textual`. The qere is stored in `source_notes` (reading type `x-qere`) and the
  ketiv is the word in the main text.
- **[proposed]** Where the Hebrew text has an accent alternative
  (`source_notes` type `alternative`), translate the main text and mention the
  alternative in a verse note only if it changes the meaning.
- **[proposed]** The Hebrew and Greek texts are not corrected. A suspected error
  in the source (a mistagged lemma, for example) is recorded in a flag, not
  silently fixed.

## 3. Verse numbering

- **[proposed]** Drafts use the numbering of the source text (Hebrew numbering for
  the Old Testament). `verse_map` gives the English numbering, which is shown
  beside the draft when the two differ.
- **[proposed]** A Psalm title is part of Hebrew verse 1 (or verses 1 and 2) and is
  translated as such. In an English layout it is shown as an unnumbered title.

## 4. Consistency and the glossary

- **[decided]** One lemma sense gets one rendering across the whole Bible. The
  approved glossary entry is mandatory. A session does not change the glossary
  unless its role is lexical.
- A translation session that must depart from the glossary records it in
  `draft_alignment` with `departs = 1` and a departure note. The quality session
  decides what to do. A session cannot be closed with an unexplained departure.
- Before a lexical session sets a rendering it reads the word sheet
  (`word_sheet.py`): every use, the KJV rendering, and the other translations.
  The reason for the decision is written in the glossary `reason` field.
- **[proposed]** Different senses of one lemma (such as `1254 a` and `1254 b`)
  get different glossary entries only when the sense difference changes the
  English word. Otherwise one entry covers both.
- **[proposed]** A rendering is a short phrase, not a sentence. If a lemma needs
  three words to render, the glossary says so in `rule`.

## 5. The divine name and titles of God

- **[decided]** יהוה is rendered **Yahweh**.
- **[decided]** אלהים is rendered **God**.
- **[proposed]** אֲדֹנָי is rendered **Lord**, and אדון (a human master) is rendered
  **lord** in lower case. When אדני stands before יהוה the pair is
  **Lord Yahweh**.
- **[proposed]** יה is **Yah**. יהוה צבאות is **Yahweh of hosts**.
- **[proposed]** אֵל and אֱלוֹהַּ are also rendered **God** by default. The lexical
  session may give them separate glossary entries if the sheet shows a reason.
  The reader can tell them from אלהים only through the notes.
- **[proposed]** Greek θεός is **God**, κύριος is **Lord** (lower case **lord** for
  a human master), and Ἰησοῦς Χριστός is **Jesus Christ**.
- **[proposed]** Where the New Testament quotes the Old Testament and κύριος
  stands for יהוה, translate **Lord** and add a verse note giving the Old
  Testament reading.

## 6. Names of people and places

- **[decided]** Use the familiar English forms: Jesus, Moses, Jerusalem, Isaac.
- **[decided]** The Hebrew definite article before a proper name is not translated
  ("Machpelah", not "the Machpelah"; "Moriah", not "the Moriah"). This covers
  names of places, peoples and persons. An exception is recorded in the glossary
  `rule` field when English really keeps the article (a title such as "the
  Adversary", or a name English always uses with "the").
- **[proposed]** The familiar form is the one in the major English translations
  (KJV and ASV spelling, modernised where the two differ). The glossary entry
  for a name has `category` = name and `tier` = 3.
- **[proposed]** The same person has the same name in both testaments. The Old
  Testament form is used in the New Testament (Elijah, Isaiah, Judah),
  except where English tradition gives the New Testament a distinct form
  (Jesus for Ἰησοῦς, but **Joshua** where Ἰησοῦς means Joshua, as in Acts 7:45
  and Hebrews 4:8, with a verse note).
- **[proposed]** Tier 1 (always transliterate) and tier 2 (transliterate when the
  text explains the name) are used only where the text itself plays on a name,
  as in Genesis 17:5 where God gives the name Abraham with its meaning. The name
  is kept in its familiar form and a verse note says what it means.

## 7. Grammar and wording

- **[proposed]** The conjunction "and" (Hebrew waw, Greek καί) is translated
  "and" wherever it is in the source. It is not dropped for style.
- **[proposed]** Hebrew verb forms are rendered by the English tense that fits the
  sense. The Hebrew form and the reason are shown in the alignment only when
  the choice is not obvious.
- **[proposed]** Words that English needs but the original does not have are put in
  square brackets: "the [one] who". No italics. A word that must be added
  often (such as a copula) is covered by a glossary rule.
- **[decided]** A word with no English equivalent (the direct object marker אֵת,
  H853, is the main case) gets a glossary entry whose rendering is the fixed token
  `(none)`, with category `term` and a rule saying why. An empty rendering is never
  used, because empty also means "not yet decided". A pronoun suffix on the marker
  (as in "buried him") is translated as the pronoun; only the marker is `(none)`.
- **[decided]** Second person pronouns (אַתָּה and the others) are rendered "you"
  everywhere. Number and gender stay in the source tagging, and a verse note
  mentions them only where they are critical to the meaning.
- **[proposed]** The definite article is translated where the source has it and is
  not added where the source lacks it, unless English cannot do without it.
- **[proposed]** Numbers, weights and measures keep their source units (cubit,
  ephah, shekel). No conversion in the text. A note may give a modern
  equivalent.
- **[proposed]** Idioms are rendered word for word if the result is clear in
  English. If it is not, use the glossary sense and add a verse note of kind
  `lexical` naming the literal words.
- **[proposed]** Pronoun references that are ambiguous in the source stay
  ambiguous in English. A note may say how others read them.

## 8. Punctuation and layout

- **[proposed]** Use ordinary modern English punctuation. The source punctuation
  marks are not imported, so punctuation is a translator decision.
- **[proposed]** Quotation marks are used for direct speech. Poetry is set in lines
  following the source's lines where the source shows them.

## 9. Notes

Notes belong to a verse (`verse_notes`) or a chapter (`chapter_notes`). Kinds:

- `lexical`: why a word was rendered as it was, or what its literal sense is.
- `textual`: ketiv, traditional readings, accent alternatives, variants.
- `difficulty`: a place where the rendering is uncertain.
- `other`: anything else.

The Gateway notes in the project database all have kinds that begin with
`legacy_`. They are reference only. Nothing from them is copied into a draft or
glossary without a lexical session checking it.

## 10. Order of work (proposed)

1. Lexical sessions work through the most frequent lemmas first, then the
   lemmas of the pilot book, until every lemma in the pilot book has an
   approved glossary entry.
2. A translation session drafts a section only when its lemmas are covered.
3. A quality session checks the drafts and raises flags. Flags are resolved
   before a section is marked `checked`.
4. Nothing marked `frozen` is changed without a new version and a decision.
