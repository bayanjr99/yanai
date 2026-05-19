"""
core/standards_v2.py — Apply billing standards to the full pipeline DataFrame.

Single public function: apply_standards(df, data_dir) → df

Uses core.standards_loader (read-only, unchanged) as the data source.
Client-name matching order:
  1. Direct pipeline-name == standards client_full
  2. Normalised fuzzy (_norm_for_match)
  3. Fallback "כל האתרים" wildcard
  4. no_pricing for clients in _no_match list
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

# ── Name normalisation ────────────────────────────────────────────────────────
# NOTE: this normalizer is intentionally kept LOCAL (not delegated to
# utils.hebrew.match_normalize) because the test suite is calibrated to this
# exact behaviour, including site-name fuzzy matching that depends on the
# specific pattern set below. The semantically equivalent helper in
# utils.hebrew.match_normalize remains available for new callers.

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


def _norm_for_match(s: str) -> str:
    """Normalise a Hebrew company/site name for fuzzy matching."""
    s = str(s).strip()
    s = _GERESH.sub('"', s)
    s = _LEGAL_RE.sub(" ", s)
    s = re.sub(r"""["'()\-.׳]""", " ", s)
    s = _DOUBLE_YOD.sub("י", s)
    s = re.sub(r"\b\d+\b", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


# ── Main function ─────────────────────────────────────────────────────────────

def apply_standards(
    df: pd.DataFrame,
    data_dir: Path | str = "data",
) -> pd.DataFrame:
    """
    Match every row in df to a billing rule from תקן.xlsx and compute:
      std_client, std_site, match_type, match_score, site_match_type,
      match_confidence, hourly_rate, daily_rate, ot_hourly_rate,
      std_hours_month, daily_min_hours, billing_type, billing_kind,
      include_breaks, billable_hours, shortage_hours, completion_pct,
      expected_billing, shortage_revenue, agreed_rate
    """
    from core.standards_loader import load_standards, site_billing_lookup, get_billing_rule

    data_dir = Path(data_dir)
    df = df.copy()

    # ── Column defaults ───────────────────────────────────────────────────────
    _DEFAULTS: dict = {
        "std_client":       "",
        "std_site":         "",
        "match_type":       "none",
        "match_score":      0,
        "site_match_type":  "none",
        "match_confidence": "none",
        "hourly_rate":      0.0,
        "daily_rate":       0.0,
        "ot_hourly_rate":   0.0,
        "std_hours_month":  float("nan"),
        "daily_min_hours":  float("nan"),
        "billing_type":     "unknown",
        "billing_kind":     "unknown",
        "include_breaks":   False,
        "billable_hours":   0.0,
        "shortage_hours":   0.0,
        "completion_pct":   float("nan"),
        "expected_billing": 0.0,
        "agreed_rate":      float("nan"),
    }
    for col, val in _DEFAULTS.items():
        if col not in df.columns:
            df[col] = val

    # ── Load standards ────────────────────────────────────────────────────────
    std = load_standards(data_dir)
    if std.empty:
        return df

    lookup = site_billing_lookup(std)

    # ── Load no_match list ────────────────────────────────────────────────────
    _no_match: set[str] = set()
    _map_path = Path(__file__).parent / "client_mapping.json"
    if _map_path.exists():
        try:
            with open(_map_path, encoding="utf-8") as _f:
                _jd = json.load(_f)
            _no_match = set(_jd.get("_no_match", []))
        except Exception:
            pass

    # ── Norm-cache for fuzzy matching ─────────────────────────────────────────
    _std_clients = std["client_full"].unique()
    _std_norm_map: dict[str, str] = {c: _norm_for_match(c) for c in _std_clients}

    # ── Per (client, site, country) rule cache ──────────────────────────────
    # Country-aware: e.g. ולפמן/ולפמן is ₪70 for non-Moldovans, ₪85 for
    # Moldovan workers (country=מודובנים).
    _rule_cache: dict[tuple, dict | None] = {}

    def _find_rule(client: str, site: str, country: str = "") -> dict | None:
        key = (client, site, country)
        if key in _rule_cache:
            return _rule_cache[key]

        if client in _no_match:
            _rule_cache[key] = None
            return None

        # 1. Direct lookup (pipeline name == standards client_full) with country
        rule = get_billing_rule(client, site, lookup, country=country)

        # 2. Fuzzy: normalise client and compare, still passing country
        if rule is None:
            c_n = _norm_for_match(client)
            for std_c, std_c_n in _std_norm_map.items():
                if std_c_n == c_n:
                    rule = get_billing_rule(std_c, site, lookup, country=country)
                    if rule is None:
                        rule = get_billing_rule(std_c, "כל האתרים", lookup, country=country)
                    if rule:
                        break

        _rule_cache[key] = rule
        return rule

    # ── Build a per-(client, site, country) rule table then merge ───────────
    # Including country lets us apply different rates for different worker
    # nationalities at the same site (e.g. ולפמן Moldovan vs others).
    _has_country = "country" in df.columns
    _key_cols = ["client", "site", "country"] if _has_country else ["client", "site"]
    unique_pairs = (
        df[_key_cols]
        .fillna("")
        .astype(str)
        .drop_duplicates()
    )

    rule_rows: list[dict] = []
    for _, pair in unique_pairs.iterrows():
        client = str(pair["client"])
        site   = str(pair["site"])
        country = str(pair["country"]).strip() if _has_country else ""
        if country in ("-", "nan", "None"):
            country = ""
        rule = _find_rule(client, site, country)

        row: dict = {"client": client, "site": site}
        if _has_country:
            row["country"] = country

        if rule is None:
            bk = "no_pricing" if client in _no_match else "unknown"
            row.update({
                "std_client": "", "std_site": "",
                "match_type": "none", "match_score": 0,
                "site_match_type": "none", "match_confidence": "none",
                "billing_kind": bk, "billing_type": "unknown",
                "hourly_rate": 0.0, "daily_rate": 0.0, "ot_hourly_rate": 0.0,
                "agreed_rate": float("nan"), "include_breaks": False,
                "std_hours_month": float("nan"), "daily_min_hours": float("nan"),
            })
        else:
            btype = str(rule.get("billing_type", "hourly"))
            rate  = rule.get("rate")
            hr    = float(rate) if (rate is not None and btype != "daily") else 0.0
            dr    = float(rate) if (rate is not None and btype == "daily")  else 0.0
            ot_r  = rule.get("ot_hourly_rate")
            comp  = rule.get("completion_target_hours")
            dmin  = rule.get("daily_min_hours")

            row.update({
                "std_client":       str(rule.get("client_full", "")),
                "std_site":         str(rule.get("site", "")),
                "match_type":       "exact",
                "match_score":      4,
                "site_match_type":  "specific" if str(rule.get("site", "")) != "כל האתרים" else "wildcard",
                "match_confidence": "high",
                "billing_kind":     str(rule.get("billing_kind", "unknown")),
                "billing_type":     btype,
                "hourly_rate":      hr,
                "daily_rate":       dr,
                "ot_hourly_rate":   float(ot_r) if ot_r is not None else 0.0,
                "agreed_rate":      float(rate) if rate is not None else float("nan"),
                "include_breaks":   bool(rule.get("include_breaks", False)),
                "std_hours_month":  float(comp) if comp is not None else float("nan"),
                "daily_min_hours":  float(dmin) if dmin is not None else float("nan"),
            })

        rule_rows.append(row)

    rules_df = pd.DataFrame(rule_rows)

    # Merge rule columns into df (drop existing to avoid _x/_y conflicts).
    # Merge on (client, site, country) when country is present so that
    # different rates per nationality at the same site are preserved.
    _merge_keys = ["client", "site"] + (["country"] if _has_country else [])
    _rule_cols = [c for c in rules_df.columns if c not in _merge_keys]
    for c in _rule_cols:
        if c in df.columns:
            df = df.drop(columns=[c])

    if _has_country:
        # Normalise df's country column the same way we did for unique_pairs
        df = df.assign(country=df["country"].fillna("").astype(str).str.strip())
        df.loc[df["country"].isin(("-", "nan", "None")), "country"] = ""
        # rules_df.country already normalised
        rules_df["country"] = rules_df["country"].fillna("").astype(str).str.strip()

    df = df.merge(
        rules_df,
        on=_merge_keys,
        how="left",
    )

    # ── Vectorised derived fields ─────────────────────────────────────────────
    _h    = pd.to_numeric(df["total_hours"],    errors="coerce").fillna(0.0)
    _d    = pd.to_numeric(df.get("work_days", pd.Series(0, index=df.index)), errors="coerce").fillna(0.0)
    _hr   = pd.to_numeric(df["hourly_rate"],    errors="coerce").fillna(0.0)
    _dr   = pd.to_numeric(df["daily_rate"],     errors="coerce").fillna(0.0)
    _ot   = pd.to_numeric(df["ot_hourly_rate"], errors="coerce").fillna(0.0)
    _std  = pd.to_numeric(df["std_hours_month"],errors="coerce").fillna(0.0)
    _dmin = pd.to_numeric(df["daily_min_hours"],errors="coerce").fillna(10.0)
    _bk   = df["billing_kind"]
    _has_std = _std > 0

    # billable_hours = max(actual, std) for hourly_with_completion, else actual
    _billable = np.where(_bk == "hourly_with_completion", np.maximum(_h, _std), _h)
    df["billable_hours"] = np.round(_billable, 2)

    # shortage_hours = max(0, std - actual) when has monthly target
    _shortage = np.where(_has_std, np.maximum(_std - _h, 0.0), 0.0)
    df["shortage_hours"] = np.round(_shortage, 2)

    # completion_pct = min(actual / std × 100, 100) when has target
    _compl = np.where(
        _has_std & (_std > 0),
        np.minimum(_h / np.where(_std > 0, _std, 1) * 100, 100.0),
        float("nan"),
    )
    df["completion_pct"] = np.round(_compl, 1)

    # expected_billing by billing_kind
    _ot_hours = np.maximum(_h - _d * _dmin, 0.0)

    _formulas: dict[str, np.ndarray] = {
        "hourly_no_completion":  (_h * _hr).values,
        "hourly_with_completion": (np.maximum(_h, _std) * _hr).values,
        "daily_no_ot":           (_d * _dr).values,
        "daily_with_ot":         (_d * _dr + _ot_hours * _ot).values,
        "daily_or_monthly_min":  np.minimum((_d * _hr * _dmin).values, (_std * _hr).values),
        "daily_min_only":        (np.maximum(_h, _d * _dmin) * _hr).values,
    }

    _expected = np.zeros(len(df), dtype=float)
    for kind, formula in _formulas.items():
        _mask = (_bk == kind).values
        _expected = np.where(_mask, formula, _expected)

    # fallback: mixed / unknown / no_pricing / missing_data → h × hr (conservative)
    _covered = _bk.isin(list(_formulas.keys())).values
    _expected = np.where(~_covered, (_h * _hr).values, _expected)

    df["expected_billing"] = np.round(_expected, 2)

    # shortage_revenue: extra profit from billing the floor
    df["shortage_revenue"] = (df["shortage_hours"] * df["hourly_rate"]).round(2)

    return df
