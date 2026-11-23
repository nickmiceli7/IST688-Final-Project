"""Find each restaurant's Google place ID once and save it to place_ids.csv.

Uses Places API (New) Text Search with "<name>, <address>, Syracuse, NY", restricted to a
rectangle around downtown Syracuse.

Usage:
    python resolve_place_ids.py            # the first 10 (IDs 1-9 and 22)
    python resolve_place_ids.py --all      # all 25
    python resolve_place_ids.py --strip    # after review: drop matched_name/matched_address

Google's terms allow storing the place ID only. matched_name and matched_address are
Google content kept just long enough for a person to review the match; run --strip
before committing so only the place ID and our own review columns stay in the repo.
Existing rows are kept unless --force is given.
"""
import argparse
import csv
from datetime import date
from pathlib import Path

from matching import name_matches, same_address
from places import FIRST_10, PLACE_IDS_PATH, TIMEOUT, get_google_key

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
SEARCH_MASK = "places.id,places.displayName,places.formattedAddress"
# Downtown Syracuse, with margin for Armory Square and Franklin Square.
DOWNTOWN = {"rectangle": {"low": {"latitude": 43.036, "longitude": -76.165},
                          "high": {"latitude": 43.060, "longitude": -76.135}}}
COLUMNS = ["restaurant_id", "name", "place_id", "matched_name", "matched_address",
           "resolved_on", "needs_review"]


# ---------- network (replaced by fakes in tests) ----------
def search_text(query, key):
    import requests
    response = requests.post(SEARCH_URL, timeout=TIMEOUT,
                             headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": SEARCH_MASK},
                             json={"textQuery": query, "locationRestriction": DOWNTOWN, "pageSize": 3})
    response.raise_for_status()
    return response.json().get("places", [])


# ---------- pure ----------
def review_reason(row, match):
    """'' when name and address both clearly match A's row, else why a person should look."""
    if match is None:
        return "no result"
    found_name = (match.get("displayName") or {}).get("text", "")
    found_address = match.get("formattedAddress", "")
    reasons = []
    if not name_matches(row["name"], found_name):
        reasons.append("name differs")
    if not same_address(row["address"], found_address):
        reasons.append("address differs")
    return "; ".join(reasons)


def resolve_row(row, places_found, today):
    match = places_found[0] if places_found else None
    return {
        "restaurant_id": int(row["restaurant_id"]),
        "name": row["name"],
        "place_id": match["id"] if match else "",
        "matched_name": (match.get("displayName") or {}).get("text", "") if match else "",
        "matched_address": match.get("formattedAddress", "") if match else "",
        "resolved_on": today,
        "needs_review": review_reason(row, match),
    }


# ---------- file ----------
def read_rows(path=PLACE_IDS_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {int(r["restaurant_id"]): r for r in csv.DictReader(f)}


def write_rows(rows, path=PLACE_IDS_PATH, columns=COLUMNS):
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for rid in sorted(rows):
            writer.writerow(rows[rid])


def main():
    from loader import load_restaurants
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true", help="look up rows that already have an ID")
    parser.add_argument("--strip", action="store_true", help="drop the Google name/address columns")
    args = parser.parse_args()

    rows = read_rows()
    if args.strip:
        write_rows(rows, columns=[c for c in COLUMNS if c not in ("matched_name", "matched_address")])
        print(f"Kept only restaurant_id, name, place_id, resolved_on, needs_review in {PLACE_IDS_PATH}.")
        return

    key = get_google_key()
    if not key:
        raise SystemExit("No Google key. Set GOOGLE_PLACES_API_KEY or add it to .streamlit/secrets.toml")
    df = load_restaurants("Grubbz AI restaurant list for B - 25 restaurantsin.csv")
    if not args.all:
        df = df[df["restaurant_id"].isin(FIRST_10)]

    today = date.today().isoformat()
    for _, row in df.iterrows():
        rid = int(row["restaurant_id"])
        if rid in rows and rows[rid].get("place_id") and not args.force:
            continue
        query = f"{row['name']}, {row['address']}, Syracuse, NY"
        try:
            found = search_text(query, key)
        except Exception as e:
            print(f"{rid:>3}  {row['name']:<30} FAILED: {e}")
            continue
        rows[rid] = resolve_row(row, found, today)
        r = rows[rid]
        flag = f"REVIEW: {r['needs_review']}" if r["needs_review"] else "ok"
        print(f"{rid:>3}  {row['name']:<30} -> {r['matched_name']} | {r['matched_address']}  [{flag}]")

    write_rows(rows)
    print(f"\nSaved {PLACE_IDS_PATH}. Review every row marked REVIEW, fix place_id by hand if needed, "
          f"clear needs_review, then run: python resolve_place_ids.py --strip")


if __name__ == "__main__":
    main()
