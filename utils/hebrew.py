"""Hebrew / mixed-text normalization for matching and comparison."""
import re
import unicodedata


def normalize(text: str) -> str:
    """Strip diacritics, collapse whitespace, lowercase."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def token_set(text: str) -> set[str]:
    return set(normalize(text).split())


def similarity(a: str, b: str) -> float:
    """Token-overlap Jaccard similarity [0, 1]."""
    sa, sb = token_set(a), token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa), len(sb))


def contains(needle: str, haystack: str) -> bool:
    """True if every token in needle appears in haystack."""
    sn = token_set(needle)
    return bool(sn) and sn.issubset(token_set(haystack))
