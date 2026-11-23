"""Fetch menu text for the restaurants A marked as plain 'text'.

Usage:
    python scrape_menus.py                 # all restaurants with menu_format == "text"
    python scrape_menus.py --only 1,2,3    # just these restaurant_ids
    python scrape_menus.py --force         # re-fetch even if menus/<id>.txt exists

Output:
    menus/<restaurant_id>.txt   plain menu text, one file per restaurant
    menus/_log.csv              one row per attempt: id, name, url, status, chars, fetched_at

Files that already exist are skipped, so a menu you pasted by hand is never overwritten.
Pages that come back nearly empty (usually menus loaded by JavaScript) are flagged
"SHORT": open those in a browser and paste the text into menus/<id>.txt yourself.
"""
import argparse
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from loader import load_restaurants

CSV_PATH = "Grubbz AI restaurant list for B - 25 restaurantsin.csv"
MENU_DIR = Path("menus")
MIN_CHARS = 400        # below this, the page probably didn't give us a real menu
PAUSE_SECONDS = 2      # be polite: pause between requests
HEADERS = {"User-Agent": "Mozilla/5.0 (Student project)"}


def page_to_text(html_bytes):
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{2,}", "\n", text)


def fetch_menu(url):
    """Return (status, text). status is 'ok', 'SHORT', or 'ERROR: ...'."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        return f"ERROR: {e}", ""
    text = page_to_text(response.content)
    return ("ok" if len(text) >= MIN_CHARS else "SHORT"), text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated restaurant_ids")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    restaurants = load_restaurants(CSV_PATH)
    todo = restaurants[restaurants["menu_format"] == "text"]
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        todo = todo[todo["restaurant_id"].isin(wanted)]

    MENU_DIR.mkdir(exist_ok=True)
    log_rows = []

    for _, row in todo.iterrows():
        rid, name, url = row["restaurant_id"], row["name"], row["menu_url"]
        out_file = MENU_DIR / f"{rid}.txt"

        if out_file.exists() and not args.force:
            print(f"{rid:>3}  {name:<30} skipped (file exists)")
            continue

        status, text = fetch_menu(url)
        if text:                                   # keep SHORT pages too, for inspection
            out_file.write_text(text, encoding="utf-8")
        log_rows.append({"restaurant_id": rid, "name": name, "menu_url": url,
                         "status": status, "chars": len(text),
                         "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
        print(f"{rid:>3}  {name:<30} {status:<8} {len(text):>6} chars")
        time.sleep(PAUSE_SECONDS)

    if log_rows:
        log_path = MENU_DIR / "_log.csv"
        new_log = pd.DataFrame(log_rows)
        if log_path.exists():
            new_log = pd.concat([pd.read_csv(log_path), new_log], ignore_index=True)
        new_log.to_csv(log_path, index=False)

    needs_manual = restaurants[restaurants["menu_format"] != "text"]
    print(f"\nNot attempted (A flagged these as needing a browser, PDF, images, or unread): "
          f"{len(needs_manual)} restaurants.")


if __name__ == "__main__":
    main()
