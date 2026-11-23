"""Statements the Host shows verbatim with every set of recommendations.

The allergy statement reports only what the menu says and never describes a dish as free of
risk; tests/test_checker.py scans every Part C string for words that would.
"""
from datetime import datetime

ALLERGY_NOTE = ("Dietary notes come only from the restaurant's menu. Please confirm allergies "
                "and ingredients with the restaurant before ordering. Grubbz cannot confirm "
                "what is in any dish.")

SOURCE_NOTE = ("Hours and services come from Google Places, checked at {time}. They can be "
               "out of date, and they do not show when online ordering is paused.")

SOURCE_NOTE_UNCHECKED = ("Hours and services could not be checked with Google Places this time. "
                         "Please confirm hours with the restaurant by phone. Google hours can also "
                         "be out of date, and they do not show when online ordering is paused.")

INSPECTION_NOTE = ("Inspection details come from the Onondaga County Health Department via "
                   "health.data.ny.gov. An inspection is a snapshot of one visit. Read the "
                   "official record for details.")


def format_checked_time(checked_at):
    """ISO timestamp -> 'Nov 22, 2026 at 3:05 PM'."""
    dt = datetime.fromisoformat(checked_at) if isinstance(checked_at, str) else checked_at
    hour = dt.hour % 12 or 12
    return f"{dt:%b} {dt.day}, {dt.year} at {hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def source_note(checked_at, any_verified=True):
    if not any_verified:
        return SOURCE_NOTE_UNCHECKED
    return SOURCE_NOTE.format(time=format_checked_time(checked_at))
