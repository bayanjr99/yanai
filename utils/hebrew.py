"""Hebrew / mixed-text normalization for matching and comparison."""
import re
import unicodedata


# ── Aggressive-matching patterns (used by match_normalize) ───────────────────
# Mirrors core.standards_v2._norm_for_match so callers can unify on this
# helper without losing matches the standards engine already supports.
_LEGAL_RE = re.compile(
    r"""\s*\(?(?:בע["״"]מ|בעמ|בע'מ|ltd\.?|limited)\)?"""
    r"|\s*\(\d{4}\)"
    r"|\s*חב'\s*"
    r"|\s*ושות'\s*"
    r"|\s*-\s*$",
    re.IGNORECASE,
)
_DOUBLE_YOD = re.compile("יי")
_GERESH = re.compile('[״""]')


def normalize(text: str) -> str:
    """Light normalisation: strip diacritics, collapse whitespace, lowercase.

    For loose token matching. Preserves legal suffixes and punctuation so
    callers that need them (e.g. exact display) can still see them.
    """
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def match_normalize(text: str) -> str:
    """Aggressive normalisation for fuzzy company / site name matching.

    On top of ``normalize`` this also:
      • strips legal suffixes  (בע"מ, בעמ, Ltd.)
      • collapses double-yod   (ייי → י)
      • removes punctuation    (" ' ( ) - . ׳)
      • removes standalone numbers (e.g. "(2024)")

    Used by ``core.standards_v2`` and ``core.matcher`` so that spelling
    variants like ``ולפמן תעשיות בע"מ`` and ``ולפמן תעשיות בעמ`` match.
    """
    if not isinstance(text, str):
        return ""
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _GERESH.sub('"', s)
    s = _LEGAL_RE.sub(" ", s)
    s = re.sub(r"""["'()\-.׳]""", " ", s)
    s = _DOUBLE_YOD.sub("י", s)
    s = re.sub(r"\b\d+\b", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def token_set(text: str) -> set[str]:
    return set(normalize(text).split())


def similarity(a: str, b: str) -> float:
    """Token overlap coefficient [0, 1]: |A∩B| / max(|A|, |B|).  (NOT Jaccard; Jaccard divides by union.)"""
    sa, sb = token_set(a), token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa), len(sb))


def contains(needle: str, haystack: str) -> bool:
    """True if every token in needle appears in haystack."""
    sn = token_set(needle)
    return bool(sn) and sn.issubset(token_set(haystack))
