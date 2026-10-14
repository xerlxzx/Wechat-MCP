"""NFKC/casefolded literal matching; original text stays in the archive."""
import unicodedata


def normalize(text):
    """NFKC, casefold and collapse whitespace; the same form is indexed and queried."""
    return ' '.join(unicodedata.normalize('NFKC', unicodedata.normalize('NFKC', text).casefold()).split())


def _source_chunks(text):
    """Canonical composition units with their original source start offset.

    Compatibility expansion can produce several units from one source character.
    Combining marks and composable Hangul Jamo stay with their starter, so NFC
    and case folding do not split a source composition across chunk boundaries.
    Only one composition unit is buffered, rather than a map for the whole text.
    """
    pending, start = [], 0
    for index, char in enumerate(text):
        for part in unicodedata.normalize('NFKD', char):
            if pending and unicodedata.combining(part) == 0:
                composed = unicodedata.normalize('NFC', ''.join(pending))
                joins = len(composed) == 1 and len(unicodedata.normalize('NFC', composed + part)) == 1
                if not joins:
                    yield unicodedata.normalize('NFKC', composed.casefold()), start
                    pending = []
            if not pending:
                start = index
            pending.append(part)
    if pending:
        yield unicodedata.normalize('NFKC', unicodedata.normalize('NFC', ''.join(pending)).casefold()), start


def source_offset(text, normalized_offset):
    """Map a normalized code-point position to its original composition unit."""
    position, whitespace, seen = 0, None, False
    for chunk, start in _source_chunks(text):
        for char in chunk:
            if char.isspace():
                if whitespace is None:
                    whitespace = start
                continue
            if whitespace is not None and seen:
                if position == normalized_offset:
                    return whitespace
                position += 1
            whitespace = None
            if position == normalized_offset:
                return start
            position += 1
            seen = True
    return len(text)


def terms(text):
    """Unigram and bigram index tokens of the normalized text, sorted."""
    text = normalize(text)
    singles = {'u' + format(ord(char), 'x') for char in text}
    pairs = {'b' + format(ord(a), 'x') + 'x' + format(ord(b), 'x') for a, b in zip(text, text[1:])}
    return tuple(sorted(singles | pairs))


def match_expression(query, generation, kind):
    """Return (normalized query, FTS expression) scoped to one generation and kind.

    The FTS match only narrows candidates; callers confirm with an exact substring check.
    """
    normalized = normalize(query)
    tokens = terms(normalized)
    wanted = [t for t in tokens if t.startswith('b')] or list(tokens)
    return normalized, ' AND '.join('"' + t + '"' for t in (f'z{generation}', 'k' + kind, *wanted))
