"""One action link per recommendation, chosen by mode.

action_links.csv is ours (not Google's): restaurant_id, reservation_url, order_url,
delivery_url, verified_by, verified_on. A team member fills and verifies it.

Rules
  Dine in:  reservation_url -> website if Google says reservable -> call
  Pickup:   the restaurant's own order_url -> call
  Delivery: delivery_url -> order_url if Google says it delivers -> call

Usage:
    python action_links.py --init    # create action_links.csv; prefill order_url from A's menu_url
    python action_links.py           # show the link each restaurant gets in each mode
"""
import argparse
import csv
import re
from pathlib import Path
from urllib.parse import urlparse

LINKS_PATH = Path("action_links.csv")
COLUMNS = ["restaurant_id", "reservation_url", "order_url", "delivery_url", "verified_by", "verified_on"]
# The restaurant's own online-ordering systems (not marketplaces like Grubhub).
ORDER_HOSTS = ("toasttab.com", "toast.app", "toast.site", "order.online", "square.site")


# ---------- pure ----------
def is_order_page(url):
    host = urlparse(str(url)).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in ORDER_HOSTS)


def clean_order_url(url):
    """Drop a Toast item deep link (".../r-<id>/item-churros_...") back to the store page."""
    return re.sub(r"/item-[^/]*$", "", str(url).strip())


def prefill_row(restaurant_id, menu_url):
    return {"restaurant_id": int(restaurant_id), "reservation_url": "",
            "order_url": clean_order_url(menu_url) if is_order_page(menu_url) else "",
            "delivery_url": "", "verified_by": "", "verified_on": ""}


def digits(phone):
    d = re.sub(r"\D", "", str(phone or ""))
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def tel_link(phone):
    d = digits(phone)
    return f"tel:+1{d}" if len(d) == 10 else ""


def pretty_phone(phone):
    d = digits(phone)
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else str(phone or "").strip()


def normalize_mode(mode):
    m = re.sub(r"[^a-z]", "", str(mode or "").lower())
    if m in ("dinein", "dine", "eatin", "reserve", "reservation"):
        return "dine_in"
    if m in ("pickup", "takeout", "takeaway", "togo", "carryout"):
        return "pickup"
    if m == "delivery":
        return "delivery"
    return None


def _call(phone, why):
    url = tel_link(phone)
    if not url:
        return None
    return {"type": "call", "label": f"{why}: {pretty_phone(phone)}", "url": url}


def choose_action_link(mode, restaurant, links_row=None, google=None):
    """Pick exactly one link. restaurant: B's record (website, phone). links_row: our CSV row.
    google: check_place() result or None. Returns {"type","label","url"} or None."""
    links_row = links_row or {}
    google = google if google and google.get("ok") else {}
    services = google.get("services") or {}
    website = restaurant.get("website") or google.get("website", "")
    phone = restaurant.get("phone") or google.get("phone", "")
    mode = normalize_mode(mode) or "dine_in"

    if mode == "pickup":
        if links_row.get("order_url"):
            return {"type": "order", "label": "Order pickup on their site", "url": links_row["order_url"]}
        return _call(phone, "Call to order pickup")

    if mode == "delivery":
        if links_row.get("delivery_url"):
            return {"type": "delivery", "label": "Order delivery", "url": links_row["delivery_url"]}
        if links_row.get("order_url") and services.get("delivery") is True:
            return {"type": "order", "label": "Order delivery on their site", "url": links_row["order_url"]}
        return _call(phone, "Call to ask about delivery")

    if links_row.get("reservation_url"):
        return {"type": "reserve", "label": "Reserve on their site", "url": links_row["reservation_url"]}
    if services.get("reservable") is True and website:
        return {"type": "reserve", "label": "Reserve through their website", "url": website}
    return _call(phone, "Call to reserve")


# ---------- file ----------
def load_action_links(path=LINKS_PATH):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {int(r["restaurant_id"]): r for r in csv.DictReader(f)}


def init_file(df, path=LINKS_PATH):
    """Create or extend action_links.csv. Rows already in the file are never overwritten."""
    rows = load_action_links(path)
    added = 0
    for _, r in df.iterrows():
        rid = int(r["restaurant_id"])
        if rid not in rows:
            rows[rid] = prefill_row(rid, r["menu_url"])
            added += 1
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for rid in sorted(rows):
            writer.writerow({c: rows[rid].get(c, "") for c in COLUMNS})
    return added


if __name__ == "__main__":
    from loader import load_restaurants
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()
    df = load_restaurants("Grubbz AI restaurant list for B - 25 restaurantsin.csv")
    if args.init:
        print(f"Added {init_file(df)} rows to {LINKS_PATH}. Fill the blanks and set verified_by/verified_on.")
    links = load_action_links()
    print(f"{'id':>3}  {'restaurant':<28} dine in / pickup / delivery  (without Google data)")
    for _, r in df.iterrows():
        rid = int(r["restaurant_id"])
        picks = [choose_action_link(m, r.to_dict(), links.get(rid)) for m in ("Dine in", "Pickup", "Delivery")]
        print(f"{rid:>3}  {r['name'][:28]:<28} " + " | ".join(p["label"] if p else "none" for p in picks))
