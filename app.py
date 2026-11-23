"""Grubbz AI - Part D (Host).

Runs with: streamlit run app.py
The Host calls two tools: the Scout (find_restaurants, Part B) and the Checker
(check_candidates, Part C). Profiles are in a local JSON file for now.
Later: replace load_profile / save_profile / delete_profile with Supabase.
"""
import json
import os
import hashlib
import re
from datetime import datetime

import streamlit as st

from check_candidates import check_candidates
from timecheck import TZ

PROFILE_FILE = "profiles.json"

# ---------------------------------------------------------------
# Tools: the Scout (Part B) and the Checker (Part C)
# ---------------------------------------------------------------
def request_text(request: dict) -> str:
    """The stored request as one sentence for the Scout's search."""
    parts = [f"Party of {request['party']}" if request.get("party") else "",
             request.get("when", ""), request.get("mode", ""),
             f"about ${request['budget']} each" if request.get("budget") else "",
             request.get("cuisine", ""), request.get("diet", "")]
    return ", ".join(str(p) for p in parts if p)


def run_tools(request: dict):
    """Scout, then Checker. Returns (scout output, checker output)."""
    from find_restaurants import find_restaurants  # needs the OpenAI key and chroma_db/
    budget = request.get("budget")
    found = find_restaurants(request_text(request), max_price=float(budget) if budget else None)
    return found, check_candidates(found, request)


# ---------------------------------------------------------------
# Profile storage (local file now; Supabase later)
# ---------------------------------------------------------------
def _hash(passcode: str) -> str:
    return hashlib.sha256(passcode.encode()).hexdigest()


def _read_all() -> dict:
    if os.path.exists(PROFILE_FILE):
        with open(PROFILE_FILE) as f:
            return json.load(f)
    return {}


def _write_all(data: dict) -> None:
    with open(PROFILE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def sign_in(name: str, passcode: str):
    """Returns (ok, message). Creates the user if new."""
    data = _read_all()
    key = name.strip().lower()
    if key in data:
        if data[key]["passcode"] != _hash(passcode):
            return False, "Wrong passcode."
        return True, "Welcome back."
    data[key] = {"passcode": _hash(passcode), "profile": {}}
    _write_all(data)
    return True, "New account created."


def load_profile(name: str) -> dict:
    return _read_all().get(name.strip().lower(), {}).get("profile", {})


def save_profile(name: str, profile: dict) -> None:
    data = _read_all()
    data[name.strip().lower()]["profile"] = profile
    _write_all(data)


def delete_profile(name: str) -> None:
    data = _read_all()
    data.pop(name.strip().lower(), None)
    _write_all(data)


# ---------------------------------------------------------------
# Short-term memory: recent turns + running summary of needs
# ---------------------------------------------------------------
def update_summary(summary: dict, new_info: dict) -> dict:
    merged = dict(summary)
    merged.update({k: v for k, v in new_info.items() if v not in (None, "", [])})
    return merged


ITEM_CODE = re.compile(r"^Item\s+\w+-\s*")


def _md(text) -> str:
    """Escape Markdown so menu text like '8-BIT BURGER*D,G' or '$7 ... $9' displays as written
    (Streamlit reads * and _ as emphasis and $...$ as math)."""
    return re.sub(r"([\\`*_\[\]#|~$<>])", r"\\\1", str(text))


def _hours_line(checks: dict, requested_time) -> str:
    if not checks["verified"]:
        return "Hours: not checked with Google Places this time; please call to confirm."
    lines = checks["hours_text"]
    if not lines:
        return "Hours: not listed on Google Places; please call to confirm."
    day = datetime.fromisoformat(requested_time) if requested_time else datetime.now(TZ)
    line = lines[day.weekday()] if len(lines) == 7 else "; ".join(lines)
    if checks["open_at_requested_time"] is True:
        line += " (open at your requested time)"
    return f"Hours (Google Places): {_md(line)}"


def format_reply(found: dict, checked: dict) -> str:
    """Up to 3 recommendations: dishes, prices, hours, data dates, inspection, one action link."""
    picks = checked["restaurants"][:3]
    if picks:
        lines = ["Here are my top picks:", f"_{_md(checked['when_note'])}_"]
    else:
        lines = ["I couldn't find a match that passed the live checks. "
                 "Try a different time, cuisine or budget."]
    for r in picks:
        c = r["checks"]
        lines.append(f"\n**{_md(r['name'])}** ({_md(r['cuisine'])})")
        for d in r["dishes"]:
            extra = f" · menu says: {_md(d['menu_says'])}" if d.get("menu_says") else ""
            if d.get("over_budget"):
                extra += " · over your budget"
            lines.append(f"- {_md(d['name'])}: {_md(d['price'])}{extra}")
        lines.append(f"- {_hours_line(c, checked.get('requested_time'))}")
        dates = f"Menu collected {r['menu_collected']}"
        if c.get("last_verified"):
            dates += f" · last verified {c['last_verified']}"
        lines.append(f"- {dates}")
        ins = c.get("inspection")
        if ins:
            lines.append(f"- Last county inspection: {ins['date']} "
                         f"([official record]({ins['record_url']}))")
            for v in ins["violations"]:
                lines.append("    - " + _md(ITEM_CODE.sub("", v)))  # county text, minus "Item 8A-"
            if not ins["violations"]:
                lines.append("    - No violations listed")
        link = c.get("action_link")
        if link:
            lines.append(f"- [{_md(link['label'])}]({link['url']})")
        for note in c["notes"]:
            # The time note is shown once above; "not checked" is already in the hours line.
            if note != checked["when_note"] and not note.startswith("Not checked with Google Places"):
                lines.append(f"- _{_md(note)}_")
    for x in checked["removed"]:
        lines.append(f"\nNot shown: **{_md(x['name'])}**. {_md(x['reason'])}")
    if found.get("relaxed"):
        lines.append(f"\n{_md(found['relaxed'])}")
    lines += ["", _md(checked["source_note"])]
    if any(r["checks"].get("inspection") for r in picks):
        lines.append(_md(checked["inspection_note"]))
    lines.append(_md(checked["allergy_note"]))
    return "\n".join(lines)


def build_reply(request: dict) -> str:
    try:
        found, checked = run_tools(request)
    except (Exception, SystemExit) as e:  # B raises SystemExit when the OpenAI key is missing
        print(f"build_reply: search failed: {e!r}")
        return ("Sorry, the restaurant search isn't available right now. "
                "Please try again in a moment.")
    return format_reply(found, checked)


# ---------------------------------------------------------------
# App
# ---------------------------------------------------------------
st.set_page_config(page_title="Grubbz AI", page_icon="🍽️")
st.title("🍽️ Grubbz AI")
st.caption("Downtown Syracuse restaurant picks for your request.")

ss = st.session_state
ss.setdefault("user", None)
ss.setdefault("messages", [])          # short-term buffer
ss.setdefault("summary", {})           # running summary of needs

# ---- Sidebar: sign-in, consent, profile ----
with st.sidebar:
    st.header("Account")
    if ss.user is None:
        name = st.text_input("Name")
        passcode = st.text_input("Passcode", type="password")
        consent = st.checkbox("I agree to save my preferences (you can view "
                              "and delete them anytime).")
        if st.button("Sign in / Create"):
            if not name or not passcode:
                st.warning("Enter a name and passcode.")
            elif not consent:
                st.warning("Please tick the consent box to save a profile.")
            else:
                ok, msg = sign_in(name, passcode)
                if ok:
                    ss.user = name
                    ss.summary = load_profile(name)
                    st.rerun()
                else:
                    st.error(msg)
        st.info("Or just use the app without signing in; nothing is saved.")
    else:
        st.success(f"Signed in as {ss.user}")
        st.subheader("My saved profile")
        st.json(load_profile(ss.user) or {"info": "Nothing saved yet."})
        if st.button("Delete my profile"):
            delete_profile(ss.user)
            ss.user, ss.summary, ss.messages = None, {}, []
            st.rerun()
        if st.button("Sign out"):
            ss.user, ss.summary, ss.messages = None, {}, []
            st.rerun()

# ---- Intake form ----
with st.form("intake"):
    st.subheader("Tell me what you need")
    c1, c2 = st.columns(2)
    party = c1.number_input("Party size", 1, 30, int(ss.summary.get("party", 2)))
    when = c2.text_input("Day and time", ss.summary.get("when", ""),
                         placeholder="Friday 7 PM")
    mode = c1.selectbox("Mode", ["Dine in", "Pickup", "Delivery"])
    budget = c2.number_input("Budget per person ($)", 0, 200,
                             int(ss.summary.get("budget", 20)))
    cuisine = st.text_input("Cuisine", ss.summary.get("cuisine", ""),
                            placeholder="Thai or Vietnamese")
    diet = st.text_input("Diet or limits", ss.summary.get("diet", ""),
                         placeholder="1 vegetarian")
    go = st.form_submit_button("Find restaurants")

if go:
    info = {"party": party, "when": when, "mode": mode, "budget": budget,
            "cuisine": cuisine, "diet": diet}
    ss.summary = update_summary(ss.summary, info)
    text = (f"Party of {party}, {when}, {mode}, about ${budget} each, "
            f"{cuisine}, {diet}")
    ss.messages.append({"role": "user", "content": text})
    ss.messages.append({"role": "assistant", "content": build_reply(ss.summary)})
    if ss.user:
        save_profile(ss.user, ss.summary)

# ---- Chat (follow-ups reuse the stored request) ----
for m in ss.messages[-10:]:            # keep a short buffer on screen
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

if prompt := st.chat_input("Refine it: cheaper, vegetarian, pickup..."):
    ss.messages.append({"role": "user", "content": prompt})
    lower = prompt.lower()
    if "cheaper" in lower:
        ss.summary["budget"] = max(5, int(ss.summary.get("budget", 20)) - 5)
    if "pickup" in lower:
        ss.summary["mode"] = "Pickup"
    # TODO: replace the keyword rules above with an LLM (OpenAI/LangChain)
    ss.messages.append({"role": "assistant", "content": build_reply(ss.summary)})
    if ss.user:
        save_profile(ss.user, ss.summary)
    st.rerun()

with st.expander("What the Host remembers right now"):
    st.json(ss.summary)