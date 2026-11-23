"""Name and address matching shared by resolve_place_ids.py and inspections.py.

A's addresses look like "301 W. Fayette St." or "201 S. Salina"; Google and the county
write "301 West Fayette Street" or "307-313 SOUTH Clinton STREET". These helpers reduce
both to (street number, street name) and compare names by their words.
"""
import re

DIRECTIONS = {"n", "s", "e", "w", "north", "south", "east", "west"}
STREET_WORDS = {"st", "street", "ave", "avenue", "blvd", "boulevard", "pl", "place",
                "sq", "square", "rd", "road", "suite", "ste"}
NAME_FILLER = {"the", "and", "restaurant", "cafe", "co", "llc", "inc", "bar", "cuisine"}


def _words(text):
    text = str(text).lower().replace("’", "'").replace("'", "")
    return re.findall(r"[a-z0-9]+", text)


def street_key(address):
    """'307-313 SOUTH Clinton STREET, Syracuse' -> ({307, ..., 313}, 'clinton').
    Returns (set of house numbers, street name) or (set(), '') if no number is found."""
    head = str(address).split(",")[0]
    m = re.match(r"\s*(\d+)(?:\s*-\s*(\d+))?\s+(.*)", head)
    if not m:
        return set(), ""
    low = int(m.group(1))
    high = int(m.group(2)) if m.group(2) else low
    numbers = set(range(low, high + 1)) if 0 <= high - low <= 20 else {low}
    words = [w for w in _words(m.group(3)) if w not in DIRECTIONS and w not in STREET_WORDS]
    return numbers, (words[0] if words else "")


def same_address(a, b):
    num_a, street_a = street_key(a)
    num_b, street_b = street_key(b)
    return bool(num_a & num_b) and street_a != "" and street_a == street_b


def name_words(name):
    return {w for w in _words(name) if w not in NAME_FILLER}


def name_matches(a, b):
    """True when every meaningful word of the shorter name appears in the longer one.
    'Eden' ~ 'Eden Hospitality', 'Sakana-Ya Sushi Bar' ~ 'Sakana-Ya & Namu' (sakana, ya)."""
    wa, wb = name_words(a), name_words(b)
    if not wa or not wb:
        return False
    small, big = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return len(small & big) / len(small) >= 0.6
