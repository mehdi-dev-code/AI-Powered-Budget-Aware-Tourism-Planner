"""Tools the AI agent is allowed to call.

They are thin wrappers over the existing data-access and planner functions, so the agent can only read
the local database and run the same cost calculation as the Trip planner tab. There is deliberately no
tool that books, pays or contacts a hotel.
"""
import json
from .database import (list_cities, list_origin_cities, search_hotels, get_transport_options,
                       get_connecting_options, effective_confidence)
from .planner import build_budget_plan

HOTEL_FIELDS = ("name", "city", "area", "price_per_night", "facilities", "availability", "updated_at",
                "last_verified", "source")


def _with_confidence(record):
    """Replace the stored label by the label to show (Verified / Needs reconfirmation / Estimated / Unknown)."""
    record["confidence"] = effective_confidence(record.get("confidence"), record.get("last_verified"))
    return record


def list_destinations():
    """Destinations with hotel data and cities a trip can start from."""
    return {"destinations": list_cities(), "starting_cities": list_origin_cities()}


def find_hotels(city, max_nightly_price=None):
    """Hotels in a city from the local database, cheapest first."""
    rows = search_hotels(city, max_nightly_price)
    if not rows:
        return {"city": city, "hotels": [], "note": f"No hotel data for {city}. Supported: {', '.join(list_cities())}."}
    return {"city": city, "hotels": [_with_confidence({k: h.get(k) for k in HOTEL_FIELDS + ("confidence",)}) for h in rows]}


def find_transport(origin, destination):
    """Direct transport options, or one-stop connections when no direct route is in the data."""
    options = get_transport_options(origin, destination)
    connecting = False
    if not options:
        options = get_connecting_options(origin, destination)
        connecting = bool(options)
    if not options:
        return {"origin": origin, "destination": destination, "options": [],
                "note": "No transport data for this route. Do not guess a fare."}
    keep = ("mode", "fare_per_person", "duration_hours", "via", "notes", "last_verified", "confidence")
    return {"origin": origin, "destination": destination, "connecting": connecting,
            "options": [_with_confidence({k: o.get(k) for k in keep if o.get(k) is not None}) for o in options]}


def plan_trip(origin, destination, budget, nights, group_size, preferences=""):
    """Itemized budget plan from the rule-based planner (all arithmetic is done in Python)."""
    try:
        budget, nights, group_size = float(budget), int(float(nights)), int(float(group_size))
    except (TypeError, ValueError):
        return {"error": "budget, nights and group_size must be numbers."}
    try:
        return build_budget_plan(origin, destination, budget, nights, group_size, preferences or "")
    except ValueError as exc:
        return {"error": str(exc)}


TOOL_FUNCS = {"list_destinations": list_destinations, "find_hotels": find_hotels,
              "find_transport": find_transport, "plan_trip": plan_trip}

TOOL_SPECS = [
    {"name": "list_destinations",
     "description": "List the destinations that have hotel data and the cities a trip can start from.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "find_hotels",
     "description": "Look up hotels in one destination city from the local database (prices are PKR per room per night).",
     "parameters": {"type": "object", "properties": {
         "city": {"type": "string", "description": "Destination city, e.g. Hunza, Skardu or Gilgit."},
         "max_nightly_price": {"type": "number", "description": "Optional maximum price per room per night in PKR."}},
         "required": ["city"]}},
    {"name": "find_transport",
     "description": "Look up transport options and fares per person (one way, PKR) between two cities.",
     "parameters": {"type": "object", "properties": {
         "origin": {"type": "string", "description": "Starting city."},
         "destination": {"type": "string", "description": "Destination city."}},
         "required": ["origin", "destination"]}},
    {"name": "plan_trip",
     "description": ("Build the itemized budget plan (hotel, return transport, food, activities) and say whether it fits the "
                     "budget. Call this once you know origin, destination, budget, nights and travellers."),
     "parameters": {"type": "object", "properties": {
         "origin": {"type": "string", "description": "Starting city."},
         "destination": {"type": "string", "description": "Destination city."},
         "budget": {"type": "number", "description": "Total trip budget in PKR."},
         "nights": {"type": "integer", "description": "Number of nights."},
         "group_size": {"type": "integer", "description": "Number of travellers."},
         "preferences": {"type": "string", "description": "Comma-separated preferences, e.g. 'Wi-Fi, family rooms'."}},
         "required": ["origin", "destination", "budget", "nights", "group_size"]}},
]


def call_tool(name, args):
    """Run one tool. Never raises: problems come back as {"error": ...} so the model can recover."""
    func = TOOL_FUNCS.get(name)
    if func is None:
        return {"error": f"Unknown tool '{name}'. Available: {', '.join(TOOL_FUNCS)}."}
    try:
        result = func(**(args or {}))
        return json.loads(json.dumps(result, default=str))      # plain JSON types only
    except TypeError as exc:
        return {"error": f"Bad arguments for {name}: {exc}"}
    except Exception as exc:                                    # keep the chat alive on any data problem
        return {"error": f"{name} failed: {exc}"}
