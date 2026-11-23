"""The Checker's second tool: get_inspection(restaurant_id).

Source: "Onondaga County Restaurant Inspections" (dataset tbxv-5tbd on health.data.ny.gov),
one row per establishment with its last inspection. Fields used (checked against the live
dataset on Nov 22, 2026): nys_health_operation_id, facility, address, date, violations,
total_critical_violations, municipality.

Shows the date, the violations as the county wrote them, the critical count the county
labels, and a link to the record. Never computes or shows a score or grade.

Usage:
    python inspections.py --match      # match all 25 and write inspection_ids.csv for review
    python inspections.py              # show the last inspection for each matched restaurant
"""
import argparse
import csv
import re
import time as _time
from pathlib import Path

from matching import name_matches, same_address

DATASET = "tbxv-5tbd"
API_URL = f"https://health.data.ny.gov/resource/{DATASET}.json"
DATASET_URL = f"https://health.data.ny.gov/d/{DATASET}"
SOURCE = "Onondaga County Health Department via health.data.ny.gov"
IDS_PATH = Path("inspection_ids.csv")
ID_COLUMNS = ["restaurant_id", "name", "nys_health_operation_id", "facility", "facility_address",
              "needs_review"]
CACHE_TTL = 12 * 60 * 60
TIMEOUT = 15

_cache = {}


# ---------- network (replaced by fakes in tests) ----------
def socrata_get(params):
    import requests
    response = requests.get(API_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_by_operation_id(operation_id):
    rows = socrata_get({"nys_health_operation_id": str(operation_id)})
    return rows[0] if rows else None


def fetch_syracuse_facilities():
    return socrata_get({"$select": "nys_health_operation_id,facility,address",
                        "$where": "municipality='SYRACUSE'", "$limit": 5000})


# ---------- pure ----------
def record_url(operation_id):
    return f"{API_URL}?nys_health_operation_id={operation_id}"


def split_violations(text):
    """The county's text, split into one item per violation. [] for 'No violations found.'
    Items are separated by '; Item ...'; some items contain a ';' of their own, so split only
    where the next item starts."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text or text.lower().startswith("no violations"):
        return []
    return [v.strip().rstrip(";").strip() for v in re.split(r";\s*(?=Item\s)", text) if v.strip(" ;")]


def summarize_inspection(row):
    count = row.get("total_critical_violations")
    return {
        "date": str(row.get("date", ""))[:10],
        "violations": split_violations(row.get("violations")),
        "critical_count": int(float(count)) if count not in (None, "") else None,
        "facility": row.get("facility", ""),
        "record_url": record_url(row["nys_health_operation_id"]),
        "dataset_url": DATASET_URL,
        "source": SOURCE,
    }


def match_restaurant(restaurant, facilities):
    """Best county facility for one of A's rows.
    Returns (facility row or None, needs_review text; '' when name and address both match)."""
    at_address = [f for f in facilities if same_address(restaurant["address"], f.get("address", ""))]
    both = [f for f in at_address if name_matches(restaurant["name"], f.get("facility", ""))]
    if len(both) == 1:
        return both[0], ""
    if len(both) > 1:
        return both[0], f"{len(both)} facilities match name and address"
    named = [f for f in facilities if name_matches(restaurant["name"], f.get("facility", ""))]
    if len(named) == 1:
        return named[0], "address differs"
    if at_address:
        others = ", ".join(f.get("facility", "") for f in at_address)
        return None, f"no name match; at this address: {others}"
    return None, "no match in county data"


# ---------- file ----------
def load_inspection_ids(path=IDS_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {int(r["restaurant_id"]): r["nys_health_operation_id"]
                for r in csv.DictReader(f) if r.get("nys_health_operation_id")}


def write_inspection_ids(df, facilities, path=IDS_PATH):
    out = []
    for _, r in df.iterrows():
        match, review = match_restaurant(r, facilities)
        out.append({"restaurant_id": int(r["restaurant_id"]), "name": r["name"],
                    "nys_health_operation_id": match["nys_health_operation_id"] if match else "",
                    "facility": match.get("facility", "") if match else "",
                    "facility_address": match.get("address", "") if match else "",
                    "needs_review": review})
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ID_COLUMNS)
        writer.writeheader()
        writer.writerows(out)
    return out


# ---------- the tool ----------
def get_inspection(restaurant_id, fetch=None, ids=None):
    """Last county inspection for one restaurant, or None if it has no matched record or the
    data can't be reached. Cached in memory for CACHE_TTL seconds."""
    restaurant_id = int(restaurant_id)
    hit = _cache.get(restaurant_id)
    if hit and hit[0] > _time.monotonic():
        return hit[1]
    operation_id = (ids if ids is not None else load_inspection_ids()).get(restaurant_id)
    if not operation_id:
        return None
    try:
        row = (fetch or fetch_by_operation_id)(operation_id)
    except Exception:
        return None
    result = summarize_inspection(row) if row else None
    _cache[restaurant_id] = (_time.monotonic() + CACHE_TTL, result)
    return result


def clear_cache():
    _cache.clear()


if __name__ == "__main__":
    from loader import load_restaurants
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", action="store_true")
    args = parser.parse_args()
    df = load_restaurants("Grubbz AI restaurant list for B - 25 restaurantsin.csv")
    if args.match:
        rows = write_inspection_ids(df, fetch_syracuse_facilities())
        for r in rows:
            flag = f"REVIEW: {r['needs_review']}" if r["needs_review"] else "ok"
            print(f"{r['restaurant_id']:>3}  {r['name']:<30} -> {r['facility'] or '-':<36} [{flag}]")
        print(f"\nSaved {IDS_PATH}. Check each REVIEW row, fix it by hand if needed, then clear needs_review.")
    else:
        names = dict(zip(df["restaurant_id"].astype(int), df["name"]))
        for rid in sorted(load_inspection_ids()):
            ins = get_inspection(rid)
            if not ins:
                print(f"{rid:>3}  {names.get(rid, '?'):<30} no record")
                continue
            crit = f", {ins['critical_count']} critical" if ins["critical_count"] is not None else ""
            print(f"{rid:>3}  {names.get(rid, '?'):<30} last inspected {ins['date']}: "
                  f"{len(ins['violations'])} violations listed{crit}")
