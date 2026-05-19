"""
Internal entities — companies/business-units that appear in the data as
"clients" but are actually parts of OUR OWN company.

Costs against these entities are real (we pay employees) but there is
intentionally no external billing — they are overhead / internal operations.

Anywhere the code flags "client with cost but no billing" as a problem,
it should `from core.internal_entities import is_internal, INTERNAL_ENTITIES`
and skip these.

To add another internal entity, just append a normalized name below.
Matching is Hebrew-aware (normalizes diacritics, whitespace, common spellings).
"""

from __future__ import annotations

from typing import Iterable

try:
    # Re-use the project's Hebrew normalisation when available
    from utils.hebrew import normalize as _hnorm
except Exception:  # pragma: no cover — defensive fallback
    def _hnorm(s: str) -> str:
        return (s or "").strip().lower()


# Canonical Hebrew names of internal entities.
# Add a row to this set to mark a new entity as "ours" (overhead).
# Include all spelling variants you might see in the source files
# (with/without בע"מ, English vs Hebrew, etc.) — the matching is
# Hebrew-aware (normalises diacritics + whitespace) but spelling variants
# need to be listed explicitly.
INTERNAL_ENTITIES: frozenset[str] = frozenset({
    "ינאי פרסונל",
    "ינאי פרסונל בע\"מ",
    "ינאי פרסונל בעמ",
    "YANAI PERSONNEL",
    "Yanai Personnel",
})


# Pre-normalised lookup set for fast comparisons.
_INTERNAL_NORM: frozenset[str] = frozenset(_hnorm(x) for x in INTERNAL_ENTITIES)


def is_internal(client_name: str | None) -> bool:
    """Return True if *client_name* refers to one of our internal entities.

    Hebrew-aware: ignores diacritics, surrounding whitespace and case.
    Safe to call with None or empty strings.
    """
    if not client_name:
        return False
    return _hnorm(str(client_name)) in _INTERNAL_NORM


def is_internal_row(row, client_col: str = "client", site_col: str = "site") -> bool:
    """True if EITHER the client or the site of *row* names an internal entity.

    Use this instead of ``is_internal(row["client"])`` when you want to
    exclude rows where ינאי פרסונל appears as a site name (e.g. internal
    admin work logged under a generic ``client`` label).
    """
    c = getattr(row, client_col, None) if not isinstance(row, dict) else row.get(client_col)
    s = getattr(row, site_col,   None) if not isinstance(row, dict) else row.get(site_col)
    return is_internal(c) or is_internal(s)


def internal_mask(df, client_col: str = "client", site_col: str = "site"):
    """Vectorised boolean mask: True for rows whose client OR site is internal.

    Preferred over per-row ``apply`` for large DataFrames.
    """
    m_client = df[client_col].astype(str).map(is_internal) if client_col in df.columns else False
    m_site   = df[site_col].astype(str).map(is_internal)   if site_col   in df.columns else False
    return m_client | m_site


def filter_external(df, client_col: str = "client", site_col: str = "site"):
    """Return rows where neither *client_col* nor *site_col* is an internal entity.

    Convenience wrapper for pandas DataFrames; falls back to a list-comp
    if pandas is unavailable (e.g. in lightweight scripts).
    """
    try:
        import pandas as pd  # noqa: F401
        return df[~internal_mask(df, client_col, site_col)]
    except Exception:
        return [row for row in df
                if not (is_internal(getattr(row, client_col, None)) or
                        is_internal(getattr(row, site_col, None)))]


def split_internal_external(df, client_col: str = "client", site_col: str = "site"):
    """Return ``(internal_df, external_df)`` partitioned by entity type.

    A row is INTERNAL if either its client OR its site names an internal
    entity (see :func:`is_internal_row`).
    """
    mask = internal_mask(df, client_col, site_col)
    return df[mask], df[~mask]


def internal_list() -> Iterable[str]:
    """Return the human-readable list of internal entities (for UI display)."""
    return sorted(INTERNAL_ENTITIES)
