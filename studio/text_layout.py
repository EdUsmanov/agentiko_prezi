"""Formatting controls need layout semantics, not a visible glyph in the font.

Keep original input/fact text unchanged. Only the layout projection omits
Word Joiner and its legacy equivalent; other characters are not filtered.
"""

WORD_JOINERS = frozenset(("\u2060", "\ufeff"))


def without_word_joiners(text):
    return "".join(c for c in text if c not in WORD_JOINERS)


def layout_words(paragraph):
    """Return words as indivisible units, including WJ-linked character pairs.

    A unit may contain a space glued to its neighbour by WJ. Treating that
    space as part of the word conservatively avoids breaking the joined span.
    """
    units = []
    join_next = False
    for char in paragraph:
        if char in WORD_JOINERS:
            join_next = True
            continue
        if join_next and units:
            units[-1] += char
        else:
            units.append(char)
        join_next = False
    word = []
    for unit in units:
        if unit.isspace():
            if word:
                yield word
                word = []
        else:
            word.append(unit)
    if word:
        yield word
