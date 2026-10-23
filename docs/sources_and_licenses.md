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

## Still to verify before use or publication

| Source | Planned use | Status |
|---|---|---|
| Rahlfs 1935 Septuagint (`LXX-Rahlfs-1935-master`) | Greek Old Testament lemmas and text | License not yet checked. Texts derived from CCAT can carry redistribution limits. |
| Hebrew lexicon files (`Lexicon`, `Lex2`: BDB, Strong's, lexical index) | Gloss hints in the `lemmas` table | License to be confirmed from the source README. |
| Greek New Testament source | Greek NT lemmas and text | Not chosen yet. |
| `bibles.db` (English translations) | Verse-level comparison, read at run time | Local use only. Never copied into this repository. The six translations under copyright (JUB, LEB, NET, LIT, MKJ, BBE) stay out of anything published. |
| Gateway Translation master JSON | Legacy notes, imported as reference | The author's own work. Imported with a `legacy_` label. |

## Rules

- Raw downloaded sources stay out of Git (`sources/raw/` is ignored). This file
  records where to get them again.
- Databases are not committed to Git. Publish them as release files, and only
  after checking the table above.
