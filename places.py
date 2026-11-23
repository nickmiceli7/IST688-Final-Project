"""The Checker's first tool: check_place(restaurant_id) reads Google Places (New) live.

Google's terms allow storing only the place ID. Everything else (status, hours, services,
phone) is fetched live and kept in an in-memory cache for CACHE_TTL seconds. Nothing from
Google is written to disk.

Usage:
    python places.py --first10      # milestone demo: status, today's hours, services
    python places.py --all
    python places.py --ids 5,6

API key: GOOGLE_PLACES_API_KEY in the environment, Streamlit secrets, or .streamlit/secrets.toml.

Plug-and-play: with a key set and no place_ids.csv yet, check_place matches each restaurant
live (Text Search on A's name and address) and uses the match only when both name and
address clearly agree. Those matches stay in memory. Reviewed IDs in place_ids.csv
(written by resolve_place_ids.py) take priority.
"""
import argparse
import csv
import os
import sys
import time as _time
import tomllib
from datetime import datetime
from functools import lru_cache
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
_live_ids = {}  # restaurant_id -> place_id matched live ("" = no clear match). Memory only.


# ---------- key and stored place IDs ----------
def get_google_key():
    """GOOGLE_PLACES_API_KEY from the environment, then Streamlit secrets (inside the app),
    then .streamlit/secrets.toml. None if absent. The key is never printed."""
    if os.environ.get("GOOGLE_PLACES_API_KEY"):
        return os.environ["GOOGLE_PLACES_API_KEY"]
    if "streamlit" in sys.modules:          # running inside the app: use st.secrets
        try:
            import streamlit as st
            # load_if_toml_exists() checks quietly; reading st.secrets with no secrets file
            # shows an error box in the app on some Streamlit versions.
            if st.secrets.load_if_toml_exists():
                value = st.secrets.get("GOOGLE_PLACES_API_KEY")
                if value:
                    return str(value)
        except Exception:
            pass
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
    """{restaurant_id: place_id} from place_ids.csv. Rows with no ID, or still marked
    needs_review, are skipped so an unconfirmed match is never used."""
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {int(r["restaurant_id"]): r["place_id"] for r in csv.DictReader(f)
                if r.get("place_id") and not (r.get("needs_review") or "").strip()}


@lru_cache(maxsize=1)
def _restaurant_table():
    """A's name and address per restaurant_id, for live matching."""
    from loader import load_restaurants
    df = load_restaurants("Grubbz AI restaurant list for B - 25 restaurantsin.csv")
    return {int(r["restaurant_id"]): {"name": r["name"], "address": r["address"]}
            for _, r in df.iterrows()}


def _restaurant_row(restaurant_id):
    return _restaurant_table().get(int(restaurant_id))


def live_place_id(restaurant_id, key, search=None, row=None):
    """Match a restaurant to Google live. Returns the place ID only when name and address
    both clearly match A's row, otherwise "". Kept in memory; failures are retried later."""
    restaurant_id = int(restaurant_id)
    if restaurant_id in _live_ids:
        return _live_ids[restaurant_id]
    from resolve_place_ids import review_reason, search_text
    row = row or _restaurant_row(restaurant_id)
    if not row:
        return ""
    try:
        found = (search or search_text)(f"{row['name']}, {row['address']}, Syracuse, NY", key)
    except Exception:
        return ""
    match = found[0] if found else None
    place_id = match["id"] if match and review_reason(row, match) == "" else ""
    _live_ids[restaurant_id] = place_id
    return place_id


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
def check_place(restaurant_id, fetch=None, key=None, place_ids=None, now=None,
                search=None, auto_match=True):
    """Live Google Places facts for one restaurant.

    Place ID: the reviewed one in place_ids.csv, else (auto_match) a live match that
    clearly agrees with A's name and address. Returns summarize_place(...) on success, or
    {"ok": False, "error": "..."} when there is no key, no usable place ID, or the call
    fails. Successes are cached in memory for CACHE_TTL seconds; failures are not cached.
    """
    restaurant_id = int(restaurant_id)
    hit = _cache.get(restaurant_id)
    if hit and hit[0] > _time.monotonic():
        return hit[1]

    key = key or get_google_key()
    if not key:
        return failure(restaurant_id, "no Google Places API key is set")
    place_id = (place_ids if place_ids is not None else load_place_ids()).get(restaurant_id)
    source = "reviewed"
    if not place_id and auto_match:
        place_id, source = live_place_id(restaurant_id, key, search), "matched live"
    if not place_id:
        return failure(restaurant_id, "no Google place ID stored or clearly matched for this restaurant")
    try:
        data = (fetch or fetch_place_details)(place_id, key)
    except Exception as e:      # network, quota, bad key: the Checker keeps the restaurant
        return failure(restaurant_id, f"Google Places call failed ({type(e).__name__}: {e})")

    result = summarize_place(data, restaurant_id, place_id, now or datetime.now(TZ))
    result["place_id_source"] = source
    _cache[restaurant_id] = (_time.monotonic() + CACHE_TTL, result)
    return result


def clear_cache():
    _cache.clear()
    _live_ids.clear()


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
        mark = "*" if r.get("place_id_source") == "matched live" else " "
        print(f"{rid:>3}{mark} {name:<28} {str(r['business_status']):<20} {_yn(s['dine_in']):>4} "
              f"{_yn(s['takeout']):>4} {_yn(s['delivery']):>5} {_yn(s['reservable']):>4}  {today}")
    print("\n? = Google Places does not say. Hours can be out of date and do not show paused online ordering.")
    print("* = place matched live on name and address, not yet reviewed (run resolve_place_ids.py to review).")


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
