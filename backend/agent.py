"""AI trip assistant: an LLM that calls the planner's tools, with checks on what it says.

The model never does the arithmetic and never supplies prices: it asks the tools, and the final plan
shown in the app is the planner's own result. verify_reply() then checks the wording for prices,
hotel names and booking claims that do not come from the tools.
"""
import re
from .database import list_cities, search_hotels
from .tools import TOOL_SPECS, call_tool

MAX_STEPS = 6

SYSTEM_PROMPT = """You are the assistant of a budget trip planner for Hunza, Skardu and Gilgit in northern Pakistan.
Answer in the language the traveller uses (English or Urdu).

Rules:
1. Hotels, fares and costs come ONLY from the tools. Never state a price, hotel or route that a tool did not return.
2. To get a cost estimate, call plan_trip. Never calculate totals yourself.
3. You need the starting city, destination, total budget (PKR), number of nights and number of travellers. If any is
   missing, ask for it in one short question instead of guessing. Preferences are optional.
4. If a tool returns no data or an error, say so plainly. Do not fill the gap with a guess.
5. Every record has a confidence label (Verified, Needs reconfirmation, Estimated or Unknown). Quote the plan's overall
   data confidence, and when it is not Verified say that prices must be confirmed with the hotel before travel.
6. You cannot book, pay or contact hotels. Never say or imply that something is booked or confirmed.
7. If the plan is over budget, say so and suggest one change (fewer nights, fewer travellers, another destination)
   and offer to re-run it. Keep replies short."""

_PKR = re.compile(r"(?:PKR|Rs\.?|₨)\s*([\d][\d,]*(?:\.\d+)?)|([\d][\d,]*(?:\.\d+)?)\s*(?:PKR|rupees)", re.I)
_HOTEL = re.compile(r"((?:[A-Z][\w'’-]*\s+){1,4}(?:Hotel|Inn|Lodge|Resort|Guest House|Stay|Motel|Hostel|Residency|Rooms))")
_BOOKED = re.compile(r"\b(?:i(?:'ve| have)?\s+booked|successfully booked|(?:booking|reservation)\s+(?:is\s+)?confirmed|"
                     r"has been booked|have been booked)\b", re.I)


def _numbers(value, found):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        found.add(round(float(value), 2))
    elif isinstance(value, dict):
        for v in value.values():
            _numbers(v, found)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers(v, found)
    elif isinstance(value, str):
        for n in re.findall(r"\d[\d,]*(?:\.\d+)?", value):
            try:
                found.add(round(float(n.replace(",", "")), 2))
            except ValueError:
                pass


def verify_reply(reply, steps, user_texts=()):
    """Return a list of warnings about things in `reply` that the tools did not provide."""
    warnings = []
    allowed = set()
    for s in steps:
        _numbers(s["result"], allowed)
    for text in user_texts:
        _numbers(text, allowed)
    unknown = []
    for m in _PKR.finditer(reply):
        raw = (m.group(1) or m.group(2)).replace(",", "").rstrip(".")
        try:
            amount = round(float(raw), 2)
        except ValueError:
            continue
        if amount not in allowed:
            unknown.append(f"{amount:,.0f}")
    if unknown:
        warnings.append("These amounts in the reply were not returned by any tool, so do not rely on them: "
                        + ", ".join(sorted(set(unknown))) + ".")
    known = [h["name"].lower() for city in list_cities() for h in search_hotels(city)]
    invented = sorted({m.group(1).strip() for m in _HOTEL.finditer(reply)
                       if not any(m.group(1).strip().lower() in k for k in known)})
    if invented:
        warnings.append("Hotel name(s) not found in the database: " + ", ".join(invented) + ".")
    if _BOOKED.search(reply):
        warnings.append("The reply talks as if something is booked. This app cannot book or confirm anything.")
    return warnings


def run_agent(message, history=None, llm=None, max_steps=MAX_STEPS):
    """Handle one user message. Returns {"reply", "plan", "steps", "warnings", "history"}.

    `history` is the neutral message list returned by the previous call (keeps the conversation going).
    """
    if llm is None:
        from .llm import GeminiLLM
        llm = GeminiLLM()
    messages = list(history or []) + [{"role": "user", "text": message}]
    steps, plan, reply = [], None, None
    for _ in range(max_steps):
        out = llm.step(SYSTEM_PROMPT, messages, TOOL_SPECS)
        messages.append({"role": "model", "text": out["text"], "calls": out["calls"], "raw": out.get("raw")})
        if not out["calls"]:
            reply = out["text"]
            break
        for call in out["calls"]:
            result = call_tool(call["name"], call["args"])
            steps.append({"tool": call["name"], "args": call["args"], "result": result})
            messages.append({"role": "tool", "name": call["name"], "result": result})
            if call["name"] == "plan_trip" and "costs" in result:
                plan = result
    if not reply:
        reply = ("Here is the plan from the planner." if plan else
                 "I could not finish that request. Please try rephrasing it or give the trip details again.")
    users = [m["text"] for m in messages if m["role"] == "user"]
    return {"reply": reply, "plan": plan, "steps": steps, "warnings": verify_reply(reply, steps, users),
            "history": messages}
