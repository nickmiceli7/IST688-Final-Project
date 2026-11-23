"""Allowed cuisine labels and the instructions for the one-time classifier call."""
import json

# Exact strings. Chroma filters match exactly, so the model must pick from this
# list and our code rejects anything else.
CUISINES = [
    "American", "Barbecue", "Breakfast & Brunch", "Cafe & Bakery",
    "Caribbean", "Chinese", "Indian", "Italian", "Japanese", "Korean",
    "Latin American", "Mediterranean / Middle Eastern", "Mexican",
    "Seafood", "Thai", "Vietnamese", "Other",
]

SYSTEM_PROMPT = f"""You label restaurants by cuisine for a recommendation database.

You will be given a restaurant's name and the text of its menu. Choose exactly
ONE primary cuisine from this list:
{json.dumps(CUISINES)}

Rules:
- Base your answer on the dishes in the menu text. Use the name only as a
  tie-breaker; names like "The York" or "Eden" say nothing about the food.
- Pick the cuisine that describes the largest share of the menu's dishes.
  A pub or grill with mixed dishes is "American".
- If no label on the list fits well, or the menu text is too short or unclear
  to judge, answer "Other". Never guess and never invent a label.
- Do not describe any dish's ingredients, allergens, or dietary suitability.

Respond with only this JSON object and nothing else:
{{"cuisine": "<one label from the list>", "evidence": "<up to 3 dish names from the menu that support it>"}}
"""


def parse_classification(raw):
    """Check the model's reply. Returns (cuisine, evidence), or ('Other', reason) on any problem."""
    try:
        data = json.loads(raw)
        cuisine = data["cuisine"]
        evidence = str(data.get("evidence", ""))
    except (json.JSONDecodeError, KeyError, TypeError):
        return "Other", "unparseable reply"
    if cuisine not in CUISINES:
        return "Other", f"model returned off-list label: {cuisine!r}"
    return cuisine, evidence
