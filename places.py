"""The Checker's first tool: check_place(restaurant_id) reads Google Places (New) live.

Google's terms allow storing only the place ID. Everything else (status, hours, services,
phone) is fetched live and kept in an in-memory cache for CACHE_TTL seconds. Nothing from
Google is written to disk.

Usage:
    python places.py --first10      # milestone demo: status, today's hours, services
    python places.py --all
    python places.py --ids 5,6

API key: GOOGLE_PLACES_API_KEY in the environment, or in .streamlit/secrets.toml.
"""
import argparse
import csv
import os
import time as _time
import tomllib
from datetime import datetime
from pathlib import Path

from timecheck import TZ, hours_today

PLACE_IDS_PATH = Path("place_ids.csv")
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
# Kept small on purpose: the field mask sets the billing tier.
FIELD_MASK = ",".join([
    "businessStatus", "regularOpeningHours", "currentOpeningHours", "dineIn", "takeout",
    "delivery", "reservable", "nationalPhoneNumber", "websiteUri", "googleMapsUri"])
CACHE_TTL = 30 * 60
TIMEOUT = 10
FIRST_10 = [1, 2, 3, 4, 5, 6, 7, 8, 9, 22]

_cache = {}     # restaurant_id -> (expires_at, result). Memory only.


# ---------- key and stored place IDs ----------
def get_google_key():
    """GOOGLE_PLACES_API_KEY from the environment, then .streamlit/secrets.toml. None if absent."""
    if os.environ.get("GOOGLE_PLACES_API_KEY"):
        return os.environ["GOOGLE_PLACES_API_KEY"]
    secrets = Path(".streamlit/secrets.toml")
    if secrets.exists():
        data = tomllib.loads(secrets.read_text(encoding="utf-8"))
        if isinstance(data.get("GOOGLE_PLACES_API_KEY"), str):
            return data["GOOGLE_PLACES_API_KEY"]
        for key, value in data.items():
            if "google" in key.lower() and isinstance(value, str):
                return value
    return None


def load_place_ids(path=PLACE_IDS_PATH):
    """{restaurant_id: place_id} from place_ids.csv (blank IDs skipped)."""
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {int(r["restaurant_id"]): r["place_id"] for r in csv.DictReader(f) if r.get("place_id")}


# ---------- network (replaced by fakes in tests) ----------
def fetch_place_details(place_id, key):
    """One Place Details (New) call. Returns Google's JSON dict or raises."""
    import requests
    response = requests.get(DETAILS_URL.format(place_id=place_id), timeout=TIMEOUT,
                            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK})
    if response.status_code != 200:
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = ""
        raise RuntimeError(f"Google Places returned {response.status_code} {message}".strip())
    return response.json()


# ---------- pure ----------
def summarize_place(data, restaurant_id, place_id, now):
    """Google's JSON -> the plain dict the Checker uses."""
    regular = data.get("regularOpeningHours") or {}
    current = data.get("currentOpeningHours") or {}
    return {
        "ok": True,
        "restaurant_id": restaurant_id,
        "place_id": place_id,
        "business_status": data.get("businessStatus"),
        "hours_text": regular.get("weekdayDescriptions") or [],
        "current_hours_text": current.get("weekdayDescriptions") or [],
        "regular_periods": regular.get("periods") or [],
        "current_periods": current.get("periods") or [],
        "services": {"dine_in": data.get("dineIn"), "takeout": data.get("takeout"),
                     "delivery": data.get("delivery"), "reservable": data.get("reservable")},
        "phone": data.get("nationalPhoneNumber", ""),
        "website": data.get("websiteUri", ""),
        "maps_url": data.get("googleMapsUri", ""),
        "fetched_at": now.isoformat(timespec="seconds"),
    }


def failure(restaurant_id, error):
    return {"ok": False, "restaurant_id": restaurant_id, "error": error}


# ---------- the tool ----------
def check_place(restaurant_id, fetch=None, key=None, place_ids=None, now=None):
    """Live Google Places facts for one restaurant.

    Returns summarize_place(...) on success, or {"ok": False, "error": "..."} when there is
    no key, no stored place ID, or the call fails. Successes are cached in memory for
    CACHE_TTL seconds; failures are not cached, so the next request tries again.
    """
    restaurant_id = int(restaurant_id)
    hit = _cache.get(restaurant_id)
    if hit and hit[0] > _time.monotonic():
        return hit[1]

    place_id = (place_ids if place_ids is not None else load_place_ids()).get(restaurant_id)
    if not place_id:
        return failure(restaurant_id, "no Google place ID stored for this restaurant")
    key = key or get_google_key()
    if not key:
        return failure(restaurant_id, "no Google Places API key is set")
    try:
        data = (fetch or fetch_place_details)(place_id, key)
    except Exception as e:      # network, quota, bad key: the Checker keeps the restaurant
        return failure(restaurant_id, f"Google Places call failed ({type(e).__name__}: {e})")

    result = summarize_place(data, restaurant_id, place_id, now or datetime.now(TZ))
    _cache[restaurant_id] = (_time.monotonic() + CACHE_TTL, result)
    return result


def clear_cache():
    _cache.clear()


# ---------- CLI ----------
def _yn(value):
    return {True: "yes", False: "no", None: "?"}[value]


def print_table(ids, names):
    now = datetime.now(TZ)
    print(f"Google Places, checked {now:%a %b %d %Y %I:%M %p} (America/New_York)\n")
    print(f"{'id':>3}  {'restaurant':<28} {'status':<20} {'dine':>4} {'take':>4} {'deliv':>5} {'resv':>4}  today's hours")
    for rid in ids:
        r = check_place(rid)
        name = names.get(rid, "?")[:28]
        if not r["ok"]:
            print(f"{rid:>3}  {name:<28} NOT CHECKED: {r['error']}")
            continue
        s = r["services"]
        today = hours_today(r["current_hours_text"] or r["hours_text"], now) or "hours not listed"
        print(f"{rid:>3}  {name:<28} {str(r['business_status']):<20} {_yn(s['dine_in']):>4} "
              f"{_yn(s['takeout']):>4} {_yn(s['delivery']):>5} {_yn(s['reservable']):>4}  {today}")
    print("\n? = Google Places does not say. Hours can be out of date and do not show paused online ordering.")


if __name__ == "__main__":
    from loader import load_restaurants
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--first10", action="store_true")
    group.add_argument("--all", action="store_true")
    group.add_argument("--ids")
    args = parser.parse_args()

    df = load_restaurants("Grubbz AI restaurant list for B - 25 restaurantsin.csv")
    names = dict(zip(df["restaurant_id"].astype(int), df["name"]))
    if args.all:
        ids = sorted(names)
    elif args.ids:
        ids = [int(x) for x in args.ids.split(",")]
    else:
        ids = FIRST_10
    if not get_google_key():
        raise SystemExit("No Google key. Set GOOGLE_PLACES_API_KEY (see contributions/ashish.md).")
    print_table(ids, names)
