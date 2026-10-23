#!/usr/bin/env python3
"""
books.py  (place this file in translation_workbench/engine/)

The 66 books of the Bible in the standard Protestant order, with the OSIS
abbreviation used by morphhb and similar data sets.

Every database in this project stores a book as a number from 1 to 66, so the
importers use this module to turn names such as "1 Samuel" or the OSIS code
"1Sam" into the right number.

Usage:
    from books import book_number, book_name, osis_id
    book_number("Psalms")    # 19
    book_number("I Samuel")  # 9
    book_name(19)            # "Psalms"
    osis_id(19)              # "Ps"
"""

import re

# (number, English name, OSIS abbreviation)
BOOKS = [
    (1, "Genesis", "Gen"), (2, "Exodus", "Exod"), (3, "Leviticus", "Lev"),
    (4, "Numbers", "Num"), (5, "Deuteronomy", "Deut"), (6, "Joshua", "Josh"),
    (7, "Judges", "Judg"), (8, "Ruth", "Ruth"), (9, "1 Samuel", "1Sam"),
    (10, "2 Samuel", "2Sam"), (11, "1 Kings", "1Kgs"), (12, "2 Kings", "2Kgs"),
    (13, "1 Chronicles", "1Chr"), (14, "2 Chronicles", "2Chr"), (15, "Ezra", "Ezra"),
    (16, "Nehemiah", "Neh"), (17, "Esther", "Esth"), (18, "Job", "Job"),
    (19, "Psalms", "Ps"), (20, "Proverbs", "Prov"), (21, "Ecclesiastes", "Eccl"),
    (22, "Song of Solomon", "Song"), (23, "Isaiah", "Isa"), (24, "Jeremiah", "Jer"),
    (25, "Lamentations", "Lam"), (26, "Ezekiel", "Ezek"), (27, "Daniel", "Dan"),
    (28, "Hosea", "Hos"), (29, "Joel", "Joel"), (30, "Amos", "Amos"),
    (31, "Obadiah", "Obad"), (32, "Jonah", "Jonah"), (33, "Micah", "Mic"),
    (34, "Nahum", "Nah"), (35, "Habakkuk", "Hab"), (36, "Zephaniah", "Zeph"),
    (37, "Haggai", "Hag"), (38, "Zechariah", "Zech"), (39, "Malachi", "Mal"),
    (40, "Matthew", "Matt"), (41, "Mark", "Mark"), (42, "Luke", "Luke"),
    (43, "John", "John"), (44, "Acts", "Acts"), (45, "Romans", "Rom"),
    (46, "1 Corinthians", "1Cor"), (47, "2 Corinthians", "2Cor"), (48, "Galatians", "Gal"),
    (49, "Ephesians", "Eph"), (50, "Philippians", "Phil"), (51, "Colossians", "Col"),
    (52, "1 Thessalonians", "1Thess"), (53, "2 Thessalonians", "2Thess"),
    (54, "1 Timothy", "1Tim"), (55, "2 Timothy", "2Tim"), (56, "Titus", "Titus"),
    (57, "Philemon", "Phlm"), (58, "Hebrews", "Heb"), (59, "James", "Jas"),
    (60, "1 Peter", "1Pet"), (61, "2 Peter", "2Pet"), (62, "1 John", "1John"),
    (63, "2 John", "2John"), (64, "3 John", "3John"), (65, "Jude", "Jude"),
    (66, "Revelation", "Rev"),
]

# Other spellings that turn up in source files (already in normalized form).
ALIASES = {
    "psalm": 19,
    "song of songs": 22,
    "canticles": 22,
    "canticle of canticles": 22,
    "qoheleth": 21,
    "revelation of john": 66,
    "revelations": 66,
    "apocalypse": 66,
}

_ROMAN = {"i": "1", "ii": "2", "iii": "3"}
_ORDINAL = {"1st": "1", "2nd": "2", "3rd": "3",
            "first": "1", "second": "2", "third": "3"}


def _normalize(name):
    """Lower-case a book name and standardize its leading number."""
    s = name.lower().replace(".", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # "1st john" and "2nd kings" lose their ending first, so they read "1 john", "2 kings"
    s = re.sub(r"^(\d)(?:st|nd|rd)\b", r"\1", s)
    # "1samuel" becomes "1 samuel"
    s = re.sub(r"^(\d)(?=[a-z])", r"\1 ", s)
    # "i samuel", "ii kings", "1st john", "second peter" become "1 samuel", etc.
    first, _, rest = s.partition(" ")
    if rest and first in _ROMAN:
        s = _ROMAN[first] + " " + rest
    elif rest and first in _ORDINAL:
        s = _ORDINAL[first] + " " + rest
    return s


# Build the lookup table once, when the module is loaded.
_LOOKUP = dict(ALIASES)
for _num, _name, _osis in BOOKS:
    _LOOKUP[_normalize(_name)] = _num
    _LOOKUP[_normalize(_osis)] = _num

_BY_NUMBER = {num: (name, osis) for num, name, osis in BOOKS}


def book_number(name):
    """Return the book number (1 to 66) for a name, or raise KeyError."""
    key = _normalize(str(name))
    if key not in _LOOKUP:
        raise KeyError(f"Unknown book name: {name!r}")
    return _LOOKUP[key]


def book_name(number):
    """Return the English name for a book number."""
    return _BY_NUMBER[number][0]


def osis_id(number):
    """Return the OSIS abbreviation for a book number."""
    return _BY_NUMBER[number][1]


if __name__ == "__main__":
    # Quick self-check: 66 books, numbered 1 to 66, with a few spot checks.
    assert [b[0] for b in BOOKS] == list(range(1, 67))
    assert book_number("Psalms") == 19 and book_number("I Samuel") == 9
    assert book_number("Song of Songs") == 22 and book_number("1Sam") == 9
    assert book_number("2nd Kings") == 12 and book_number("Revelation") == 66
    print("books.py self-check passed")
