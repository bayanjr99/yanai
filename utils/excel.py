"""
Excel-helper utilities shared across the project.

Previously each loader (excel_loaders, andromeda_loader, cost_analysis,
analytics, …) had its own private ``_find_col`` with subtle behavioural
differences. This module is the canonical implementation; callers should
``from utils.excel import find_col``.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence


def _norm(s: str) -> str:
    """Loose normalisation for column-name comparison."""
    s = str(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()


def find_col(
    columns: Iterable[str],
    candidates: Sequence[str],
    *,
    exact: bool = False,
) -> str | None:
    """Return the first column in *columns* that matches any *candidate* name.

    Matching is case-insensitive and whitespace-normalised. When ``exact`` is
    False (the default), substring matching is allowed *only* after no exact
    match was found — this preserves precedence so e.g. a column literally
    named ``total_hours`` wins over ``total_reportable_hours`` when ``total
    hours`` is a candidate.

    Returns the actual column name from *columns* (with original casing) or
    None if no candidate matched.
    """
    cols = list(columns)
    if not cols or not candidates:
        return None

    norm_to_orig: dict[str, str] = {_norm(c): c for c in cols}
    cand_norms = [_norm(c) for c in candidates if c]

    # 1. Exact (case-insensitive, whitespace-normalised) match
    for cn in cand_norms:
        if cn in norm_to_orig:
            return norm_to_orig[cn]

    if exact:
        return None

    # 2. Substring fallback — pick the SHORTEST column name that contains
    #    any candidate (avoids accidentally matching a longer, unrelated col).
    matches: list[tuple[int, str]] = []
    for col_norm, col_orig in norm_to_orig.items():
        for cn in cand_norms:
            if cn and cn in col_norm:
                matches.append((len(col_orig), col_orig))
                break
    if matches:
        matches.sort()  # shortest wins
        return matches[0][1]

    return None
