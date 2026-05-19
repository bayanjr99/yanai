"""
Match an (employee_id, pdf_site) pair to:
  1. The correct client  (via the costs file)
  2. The correct agreement (via the agreements list)

Country-aware matching
----------------------
When a worker has a country set in the costs file AND there is a
country-specific agreement for that client+site, the country-specific
agreement is used (higher priority than the generic agreement).

This supports multi-rate clients such as ולפמן where Moldovan workers
are billed at a different rate than other workers.
"""

from __future__ import annotations

from utils.hebrew import normalize, similarity, contains

INTERNAL_KEYWORDS = {"ינאי פרסונל", "yanai", "פנימי", "internal"}


def is_internal(client: str) -> bool:
    cn = normalize(client)
    return any(normalize(k) in cn for k in INTERNAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Client lookup from costs dict
# ---------------------------------------------------------------------------

def resolve_client(
    employee_id: str,
    pdf_site: str,
    costs: dict[str, list[dict]],
) -> tuple[str, float, str]:
    """
    Return (client_name, monthly_cost, country) for this employee.

    If the employee has multiple cost entries, pick the one whose site
    best matches the PDF site.  Falls back to the first entry.

    Returns ("", 0.0, "") when the employee is not in the costs file.
    """
    entries = costs.get(str(employee_id), [])
    if not entries:
        return "", 0.0, ""

    if len(entries) == 1:
        e = entries[0]
        return e["client"], e["cost"], e.get("country", "")

    # Multiple entries – pick best site match using Hebrew-aware scoring
    best_score = -1.0
    best_entry = entries[0]
    sn = normalize(pdf_site)
    for e in entries:
        score = similarity(sn, normalize(e["site"]))
        if contains(sn, normalize(e["site"])) or contains(normalize(e["site"]), sn):
            score = max(score, 0.7)
        # Country boost removed: comparing e["country"] to pdf_site (a site name)
        # was semantically wrong (apples vs oranges) and could boost unrelated entries.
        if score > best_score:
            best_score = score
            best_entry = e

    return best_entry["client"], best_entry["cost"], best_entry.get("country", "")


# ---------------------------------------------------------------------------
# Agreement matching
# ---------------------------------------------------------------------------

def find_agreement(
    client: str,
    site: str,
    agreements: list[dict],
    country: str = "",
) -> tuple[dict | None, str]:
    """
    Find the best agreement for a (client, site, country) combination.

    Priority
    --------
    0. Exact client + exact site + exact country  (when worker has country)
    1. Exact client + exact site (agreement has no country restriction)
    2. Exact client + fuzzy site (no country restriction in agreement)
    3. Exact client + wildcard site (catch-all, no country restriction)
    4. Fuzzy client + any site (no country restriction)
    5. None

    Country-specific agreements (those with ag["country"] != "") are ONLY
    matched when the worker's country matches exactly.  They are never used
    as a fallback for workers without a country assignment.

    Returns (agreement_dict | None, reason_string)
    """
    if is_internal(client):
        return None, "internal"

    cn = normalize(client)
    sn = normalize(site)
    co = normalize(country)

    def _ag_country(ag: dict) -> str:
        return normalize(ag.get("country", ""))

    def _ag_usable(ag: dict) -> bool:
        """True if this agreement is accessible to the worker's country."""
        ag_co = _ag_country(ag)
        if ag_co:
            # Country-specific: only for workers with matching country
            return co == ag_co
        # No country restriction: matches all workers
        return True

    # Pass 0 – exact client + exact site + exact country
    if co:
        for ag in agreements:
            if (normalize(ag["client"]) == cn
                    and normalize(ag["site"]) == sn
                    and _ag_country(ag) == co):
                return ag, "exact+country"

    # Pass 1 – exact client + exact site (no country restriction)
    for ag in agreements:
        if normalize(ag["client"]) == cn and normalize(ag["site"]) == sn:
            if _ag_usable(ag):
                return ag, "exact"

    # Pass 2 – exact client + fuzzy site (no country restriction)
    best_sim = 0.0
    best_ag  = None
    for ag in agreements:
        if normalize(ag["client"]) != cn or not _ag_usable(ag):
            continue
        ag_site = normalize(ag["site"])
        if not ag_site:
            continue
        sim = similarity(sn, ag_site)
        if contains(sn, ag_site) or contains(ag_site, sn):
            sim = max(sim, 0.6)
        if sim > best_sim:
            best_sim = sim
            best_ag  = ag

    if best_ag is not None and best_sim >= 0.3:
        return best_ag, f"fuzzy_site({best_sim:.2f})"

    # Pass 3 – exact client, no site restriction (catch-all)
    for ag in agreements:
        if normalize(ag["client"]) == cn and not ag["site"].strip() and _ag_usable(ag):
            return ag, "client_catchall"

    # Pass 4 – fuzzy client (no country restriction)
    best_client_sim = 0.0
    best_client_ag  = None
    for ag in agreements:
        if not _ag_usable(ag):
            continue
        sim = similarity(cn, normalize(ag["client"]))
        if sim > best_client_sim:
            best_client_sim = sim
            best_client_ag  = ag

    if best_client_ag is not None and best_client_sim >= 0.5:
        return best_client_ag, f"fuzzy_client({best_client_sim:.2f})"

    return None, "not_found"
