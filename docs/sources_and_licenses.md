# Sources and licenses

A record of every outside data source used by translation_workbench, where it
came from, and the terms it carries. Update this file whenever a source is
added, and check it before publishing anything derived from a source.
Put this file in `docs/`.

## Open Scriptures Hebrew Bible (OSHB, "morphhb")

- **Used for:** the Hebrew source text, with a lemma and morphology tag on
  every word (the `words` and `lemmas` tables in the source database).
- **Where it came from:** https://github.com/openscriptures/morphhb
  (local copy: `OHSB/morphhb-master` on the external drive).
- **Version in the local copy:** the revision notes in `wlc/Gen.xml` list the
  full morphology release of 2018.12.14 and the update to WLC version 4.20.
- **Terms:** the lemma and morphology data are licensed under Creative Commons
  Attribution 4.0 International (CC BY 4.0). The text of the Westminster
  Leningrad Codex is in the public domain. This is stated in the project
  README and in the work header of `wlc/Gen.xml`.
- **Attribution required:** credit the Open Scriptures Hebrew Bible Project.
- **Suggested credit line:** "Hebrew text, lemma and morphology data from the
  Open Scriptures Hebrew Bible (OSHB) project,
  https://github.com/openscriptures/morphhb, licensed CC BY 4.0. The
  Westminster Leningrad Codex text is in the public domain."
- **Versification:** Masoretic (the file header gives refSystem Bible.MT).
  `wlc/VerseMap.xml` is the likely source for the `verse_map` table.

## Open Scriptures Hebrew Lexicon (HebrewLexicon)

- **Used for:** Hebrew headwords, transliteration, short glosses (lexical index),
  Strong's Hebrew Dictionary and Brown-Driver-Briggs text (tables
  `lexicon_entries`, `strongs_entries`, `bdb_entries`, and the link
  `lemmas.lex_key`).
- **Where it came from:** https://github.com/openscriptures/HebrewLexicon
  (local copy: `hebrewbible/Dump/HebrewLexicon-master/HebrewLexicon-master`).
- **Terms (verified from its readme.md):** released under Creative Commons
  Attribution 4.0 International. The actual texts of Brown, Driver, Briggs and
  of Strong's Hebrew Dictionary remain in the public domain.
- **Attribution required:** credit the Open Scriptures Hebrew Bible Project.
  TWOT numbers are included for reference only (the readme says the TWOT is
  not transcribed).

## STEPBible TAGNT (Translators Amalgamated Greek New Testament)

- **Used for:** the Greek New Testament, with a dictionary form, disambiguated
  Strong's number and morphology on every word, from all the major Greek
  editions (Nestle-Aland 27/28, Tyndale House, SBL, Westcott-Hort, Tregelles,
  Textus Receptus, Byzantine). Stored in `words` (text name "TAGNT"), `lemmas`,
  `source_notes` and `verse_map`.
- **Where it came from:** https://github.com/STEPBible/STEPBible-Data, folder
  "Translators Amalgamated OT+NT", the two files named "TAGNT ...".
- **Terms (verified from the file header and the repository README):** CC BY 4.0.
  Data created by www.STEPBible.org based on work at Tyndale House, Cambridge.
- **Attribution required:** credit STEP Bible (www.STEPBible.org) and Tyndale House.
- **Conditions in the header worth remembering:** the data may be reformatted
  and included in software or publications without asking permission, and any
  changes should be noted. The header asks others not to redistribute the raw
  files themselves and to refer people to github.com/STEPBible instead. The
  workbench therefore publishes only its own database, not the TAGNT files.
- **Not imported:** the English wording (based on the Berean Study Bible, used
  by STEPBible with permission), the Spanish glosses and the context glosses.
  Only the dictionary-form gloss from the Tyndale Brief Lexicon (TBESG) is kept.
- **Greek text choice:** the file merges all editions. `words.word_type` says
  which editions carry each word, so the text a project follows is a setting,
  not a baked-in choice.
- **Local copy:** none needed. Download again from the repository above; keep
  the files under `sources/raw/` (ignored by Git).

## Still to verify before use or publication

| Source | Planned use | Status |
|---|---|---|
| Rahlfs 1935 Septuagint (`LXX-Rahlfs-1935-master`) | Greek Old Testament lemmas and text | License not yet checked. Texts derived from CCAT can carry redistribution limits. |
| `bibles.db` (English translations) | Verse-level comparison, read at run time | Local use only. Never copied into this repository. The six translations under copyright (JUB, LEB, NET, LIT, MKJ, BBE) stay out of anything published. |
| Gateway Translation master JSON | Legacy notes, imported as reference | The author's own work. Imported with a `legacy_` label. |

## Rules

- Raw downloaded sources stay out of Git (`sources/raw/` is ignored). This file
  records where to get them again.
- Databases are not committed to Git. Publish them as release files, and only
  after checking the table above.
