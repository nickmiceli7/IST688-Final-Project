"""The Auditor: list what a team member should verify, and log what they found.

For each restaurant it flags:
  (a) no stored Google place ID, or a Google business status other than OPERATIONAL
  (b) a live menu page whose text changed since the last stored hash
      (pages that can't be read automatically, such as Toast, are "check by hand", not "changed")
  (c) user reports from reports.csv made since the previous audit
plus one item to compare the list with the current Downtown Committee dining guide.

Files
  audit/audit_list_<date>.csv   restaurant_id, name, item, finding, suggested_action
  audit/audit_log.csv           date, restaurant_id, item, finding, action_taken, checked_by  (append-only)
  menu_hashes.csv               restaurant_id, menu_url, sha256, checked_on  (our own data)
  reports.csv                   restaurant_id, reported_on, text  (created with headers if missing)

Usage:
    python audit.py                                        # run the audit, write today's list
    python audit.py --record 6 "closed" "removed" --by Ashish [--item business_status]
"""
import argparse
import csv
import hashlib
import html
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

AUDIT_DIR = Path("audit")
LOG_PATH = AUDIT_DIR / "audit_log.csv"
HASHES_PATH = Path("menu_hashes.csv")
REPORTS_PATH = Path("reports.csv")
LIST_COLUMNS = ["restaurant_id", "name", "item", "finding", "suggested_action"]
LOG_COLUMNS = ["date", "restaurant_id", "item", "finding", "action_taken", "checked_by"]
HASH_COLUMNS = ["restaurant_id", "menu_url", "sha256", "checked_on"]
REPORT_COLUMNS = ["restaurant_id", "reported_on", "text"]
MIN_TEXT = 400            # less visible text than this means the menu is loaded by script
SCRIPT_HOSTS = ("toasttab.com", "toast.app", "toast.site", "order.online", "square.site")
USER_AGENT = "GrubbzAuditor/1.0 (IST 688 class project; monthly menu check)"


# ---------- small CSV helpers ----------
def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, columns, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows({c: r.get(c, "") for c in columns} for r in rows)


def append_csv(path, columns, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if new:
            writer.writeheader()
        writer.writerow({c: row.get(c, "") for c in columns})


def ensure_reports_file(path=REPORTS_PATH):
    if not Path(path).exists():
        write_csv(path, REPORT_COLUMNS, [])


# ---------- network (replaced by fakes in tests) ----------
def fetch_page(url):
    """Returns the page's HTML, or raises."""
    import requests
    response = requests.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    return response.text


# ---------- pure ----------
def visible_text(page_html):
    text = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page_html)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip().lower()


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def menu_finding(url, page_html, error, previous):
    """Compare a fetched menu page with the stored hash.
    Returns (finding or None, suggested_action or None, new hash or None)."""
    host = urlparse(url).netloc.lower()
    script_host = any(host == h or host.endswith("." + h) for h in SCRIPT_HOSTS)
    if error or page_html is None:
        return (f"Menu page could not be fetched ({error}); check by hand.",
                "Open the menu page and compare it with the stored dishes.", None)
    text = visible_text(page_html)
    if script_host or len(text) < MIN_TEXT:
        return ("Menu page loads its dishes by script and can't be compared automatically; check by hand.",
                "Open the menu page and compare it with the stored dishes.", None)
    new = text_hash(text)
    if previous and previous.get("sha256") and previous.get("menu_url") == url and previous["sha256"] != new:
        return (f"Menu page text changed since {previous.get('checked_on', 'the last audit')}.",
                "Compare with the stored dishes; collect the menu again if dishes or prices changed.", new)
    return None, None, new


def status_finding(place):
    """Google check -> (item, finding, suggested_action) or None when OPERATIONAL."""
    if not place.get("ok"):
        if "place ID" in place.get("error", ""):
            return ("place_id", "No Google place ID is stored.",
                    "Run resolve_place_ids.py and review the match.")
        return ("business_status", f"Could not check Google Places ({place.get('error')}).",
                "Check the restaurant's status by hand on its site or by phone.")
    status = place.get("business_status")
    if status == "OPERATIONAL":
        return None
    return ("business_status", f"Google Places lists the business status as {status or 'missing'}.",
            "Confirm by phone or on its site. If it has closed, remove it from the list and log it.")


def previous_audit_date(audit_dir=AUDIT_DIR, today=None):
    dates = sorted(p.stem.replace("audit_list_", "") for p in Path(audit_dir).glob("audit_list_*.csv"))
    dates = [d for d in dates if today is None or d < today]
    return dates[-1] if dates else ""


def report_items(reports, names, since):
    out = []
    for r in reports:
        if since and r.get("reported_on", "") <= since:
            continue
        rid = int(r["restaurant_id"]) if str(r.get("restaurant_id", "")).strip().isdigit() else ""
        out.append({"restaurant_id": rid, "name": names.get(rid, ""), "item": "user report",
                    "finding": f"Reported {r.get('reported_on', '')}: {r.get('text', '')}",
                    "suggested_action": "Check the report on the restaurant's site or by phone."})
    return out


def run_audit(df, place_lookup, page_fetch, hashes, reports, today, since):
    """Returns (audit list rows, updated hash rows). No file I/O, so it can be tested."""
    items, new_hashes = [], dict(hashes)
    names = dict(zip(df["restaurant_id"].astype(int), df["name"]))
    for _, row in df.iterrows():
        rid, name, url = int(row["restaurant_id"]), row["name"], str(row["menu_url"]).strip()

        try:
            place = place_lookup(rid)
        except Exception as e:
            place = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        found = status_finding(place)
        if found:
            items.append({"restaurant_id": rid, "name": name, "item": found[0],
                          "finding": found[1], "suggested_action": found[2]})

        if not url:
            items.append({"restaurant_id": rid, "name": name, "item": "menu",
                          "finding": "No menu URL on file.", "suggested_action": "Find the menu page."})
            continue
        page, error = None, None
        try:
            page = page_fetch(url)
        except Exception as e:
            error = type(e).__name__
        finding, action, new = menu_finding(url, page, error, hashes.get(rid))
        if finding:
            items.append({"restaurant_id": rid, "name": name, "item": "menu",
                          "finding": finding, "suggested_action": action})
        if new:
            new_hashes[rid] = {"restaurant_id": rid, "menu_url": url, "sha256": new, "checked_on": today}

    items.extend(report_items(reports, names, since))
    items.append({"restaurant_id": "", "name": "All restaurants", "item": "restaurant list",
                  "finding": "Compare the list with the current Downtown Committee of Syracuse dining guide.",
                  "suggested_action": "Add or remove restaurants to match the guide, and log each change."})
    return items, new_hashes


def load_last_verified(path=LOG_PATH):
    """{restaurant_id: latest date in the audit log}. The Host shows it as 'last verified'."""
    out = {}
    for r in read_csv(path):
        rid = str(r.get("restaurant_id", "")).strip()
        if rid.isdigit():
            out[int(rid)] = max(out.get(int(rid), ""), r.get("date", ""))
    return out


def default_item(rid, audit_dir=AUDIT_DIR):
    """The item for --record: the only flagged item for this restaurant in the latest list."""
    lists = sorted(Path(audit_dir).glob("audit_list_*.csv"))
    if not lists:
        return "general"
    items = [r["item"] for r in read_csv(lists[-1]) if r.get("restaurant_id") == str(rid)]
    return items[0] if len(items) == 1 else "general"


# ---------- CLI ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", nargs=3, metavar=("RESTAURANT_ID", "FINDING", "ACTION"))
    parser.add_argument("--by", help="who checked it")
    parser.add_argument("--item", help="what was checked (default: the item flagged in the latest list)")
    args = parser.parse_args()
    today = date.today().isoformat()

    if args.record:
        if not args.by:
            raise SystemExit("Say who checked it: --by <name>")
        rid, finding, action = args.record
        row = {"date": today, "restaurant_id": rid, "item": args.item or default_item(rid),
               "finding": finding, "action_taken": action, "checked_by": args.by}
        append_csv(LOG_PATH, LOG_COLUMNS, row)
        print(f"Logged to {LOG_PATH}: {row}")
        return

    from loader import load_restaurants
    from places import check_place, get_google_key
    if not get_google_key():
        print("Warning: no Google key, so every business status is listed as 'could not check'.")
    ensure_reports_file()
    df = load_restaurants("Grubbz AI restaurant list for B - 25 restaurantsin.csv")
    hashes = {int(r["restaurant_id"]): r for r in read_csv(HASHES_PATH)}
    since = previous_audit_date(today=today)
    items, new_hashes = run_audit(df, check_place, fetch_page, hashes, read_csv(REPORTS_PATH), today, since)

    out = AUDIT_DIR / f"audit_list_{today}.csv"
    write_csv(out, LIST_COLUMNS, items)
    write_csv(HASHES_PATH, HASH_COLUMNS, [new_hashes[k] for k in sorted(new_hashes)])
    if not LOG_PATH.exists():
        write_csv(LOG_PATH, LOG_COLUMNS, [])
    for it in items:
        print(f"{str(it['restaurant_id']):>3}  {it['name'][:28]:<28} {it['item']:<16} {it['finding']}")
    print(f"\n{len(items)} items in {out}. Verify each one, then log it, for example:\n"
          f'  python audit.py --record 6 "still open; Toast ordering paused" "no change" --by Ashish')


if __name__ == "__main__":
    main()
