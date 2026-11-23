"""Load A's restaurant list and give every restaurant a permanent numeric ID."""
from pathlib import Path

import pandas as pd

# Columns the rest of the team needs (C looks places up by name/address/phone,
# D shows website/phone). 'open' (all "to confirm") and 'date_checked' (empty)
# are dropped.
KEEP = ["restaurant_id", "name", "batch", "menu_url", "menu_format",
        "website", "address", "phone", "notes"]


def load_restaurants(csv_path, ids_path="restaurant_ids.csv"):
    # A's file is cp1252, not UTF-8. Without this, apostrophes come out as garbage.
    df = pd.read_csv(csv_path, encoding="cp1252")
    df["name"] = df["name"].str.strip()

    # Stable IDs: the first time we see a name we give it the next free number
    # and save it. Later runs reuse the saved number, so removing a closed
    # restaurant from the CSV never shifts anyone else's ID.
    ids_file = Path(ids_path)
    if ids_file.exists():
        saved = pd.read_csv(ids_file)
        id_map = dict(zip(saved["name"], saved["restaurant_id"]))
    else:
        id_map = {}

    next_id = max(id_map.values(), default=0) + 1
    for name in df["name"]:
        if name not in id_map:
            id_map[name] = next_id
            next_id += 1

    pd.DataFrame({"name": list(id_map), "restaurant_id": list(id_map.values())}
                 ).to_csv(ids_file, index=False)

    df["restaurant_id"] = df["name"].map(id_map).astype(int)

    # Chroma metadata can't hold NaN/None, so blanks become empty strings.
    df = df.fillna("")
    return df[KEEP]


if __name__ == "__main__":
    restaurants = load_restaurants("Grubbz AI restaurant list for B - 25 restaurantsin.csv")
    print(restaurants[["restaurant_id", "name", "batch", "menu_format"]].to_string(index=False))
    print(len(restaurants), "restaurants")
