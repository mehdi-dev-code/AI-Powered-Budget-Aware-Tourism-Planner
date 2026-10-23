"""Rule-based planning baseline; AI agents are planned for Week 5."""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from .database import (search_hotels, get_transport_options, get_connecting_options, save_trip,
                       list_cities)

# Cost assumptions (PKR). They are illustrative and stated on screen so the estimate stays transparent.
PEOPLE_PER_ROOM = 2
FOOD_PER_PERSON_PER_DAY = 1200      # charged for (nights + 1) days
ACTIVITIES_PER_PERSON = 800


def _normalize(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def _split_preferences(preferences):
    return [p.strip() for p in re.split(r"[,;]", preferences or "") if p.strip()]


def _preference_match(hotel, preferences):
    """Return (matched, unmet) lists for the comma-separated preferences against the hotel's facilities."""
    facilities = _normalize(hotel.get("facilities") or "")
    matched, unmet = [], []
    for pref in _split_preferences(preferences):
        (matched if _normalize(pref) and _normalize(pref) in facilities else unmet).append(pref)
    return matched, unmet


def build_budget_plan(origin, destination, budget, nights, group_size, preferences=""):
    """Build an itemized estimate. Nothing is saved; call save_plan() to keep a plan."""
    if budget <= 0:
        raise ValueError("Budget must be greater than zero.")
    if nights < 1:
        raise ValueError("Trip duration must be at least one night.")
    if group_size < 1:
        raise ValueError("Group size must be at least one person.")
    if origin.strip().lower() == destination.strip().lower():
        raise ValueError("Starting city and destination must be different.")

    hotels = search_hotels(destination)
    if not hotels:
        supported = ", ".join(list_cities())
        return {"error": f"No hotel data for {destination}. Supported destinations: {supported}."}

    # Transport: a direct route, else a one-stop connection, else unknown (partial estimate).
    transport = get_transport_options(origin, destination)
    connecting = False
    if not transport:
        transport = get_connecting_options(origin, destination)
        connecting = bool(transport)
    transport_option = transport[0] if transport else None

    warnings = []
    if transport_option:
        transport_total = transport_option["fare_per_person"] * group_size * 2
        if connecting:
            transport_label = f"Transport (connecting route via {transport_option['via']}, return estimate)"
            warnings.append(f"No direct route in the data from {origin} to {destination}. The estimate combines two "
                            f"sample legs via {transport_option['via']} and does not include waiting time. "
                            "Confirm the connection and fares locally.")
        else:
            transport_label = "Transport (return estimate)"
    else:
        transport_total = 0
        transport_label = "Transport (no data - NOT included)"
        warnings.append(f"No transport data for {origin} to {destination}. "
                        "Transport cost is not included, so the real total will be higher.")

    food_days = nights + 1
    food_estimate = FOOD_PER_PERSON_PER_DAY * group_size * food_days
    activity_estimate = ACTIVITIES_PER_PERSON * group_size
    rooms = math.ceil(group_size / PEOPLE_PER_ROOM)
    other_costs = transport_total + food_estimate + activity_estimate

    def hotel_cost(h):
        return h["price_per_night"] * nights * rooms

    # Hotel choice: budget first, then best preference match, then lowest price.
    def rank(h):
        return (-len(_preference_match(h, preferences)[0]), h["price_per_night"])

    affordable = [h for h in hotels if hotel_cost(h) + other_costs <= budget]
    hotel = min(affordable, key=rank) if affordable else min(hotels, key=lambda h: h["price_per_night"])
    best_match = min(hotels, key=rank)
    if affordable:
        if len(_preference_match(best_match, preferences)[0]) > len(_preference_match(hotel, preferences)[0]):
            warnings.append(f"{best_match['name']} matches more of your preferences but would exceed your budget, "
                            f"so {hotel['name']} was selected instead.")
    else:
        warnings.append(f"No hotel in {destination} fits your budget with these costs. The cheapest hotel is shown.")
    _, unmet = _preference_match(hotel, preferences)
    if unmet:
        warnings.append("The selected hotel does not match these preferences: " + ", ".join(unmet) + ".")

    hotel_total = hotel_cost(hotel)
    total = hotel_total + other_costs

    # Budget check. A missing cost can only raise the total, so "over budget" stays conclusive,
    # but "within budget" is NOT conclusive while transport is unknown.
    partial = transport_option is None
    if total > budget:
        budget_status = "over"
    elif partial:
        budget_status = "inconclusive"
    else:
        budget_status = "within"

    return {
        "destination": destination,
        "origin": origin,
        "budget": float(budget),
        "nights": int(nights),
        "group_size": int(group_size),
        "rooms": rooms,
        "hotel": hotel,
        "transport": transport_option,
        "costs": {
            f"Accommodation ({rooms} room{'s' if rooms > 1 else ''} x {nights} night{'s' if nights > 1 else ''})": round(hotel_total),
            transport_label: round(transport_total),
            f"Food estimate ({food_days} days)": round(food_estimate),
            "Activities estimate": round(activity_estimate),
        },
        "total_estimate": round(total),
        "remaining": round(float(budget) - total),
        "partial": partial,
        "budget_status": budget_status,
        "within_budget": {"within": True, "over": False}.get(budget_status),   # None = cannot be confirmed
        "preferences": preferences,
        "unmet_preferences": unmet,
        "warnings": warnings,
        "assumptions": (f"{PEOPLE_PER_ROOM} people per room; food PKR {FOOD_PER_PERSON_PER_DAY:,} per person per day "
                        f"for nights + 1 days ({food_days} days here); activities PKR {ACTIVITIES_PER_PERSON:,} "
                        "per person; transport is a return trip per person."),
        "notice": "Demo data and cost assumptions only. Confirm all prices, routes, hotel contacts and availability before travel.",
    }


def save_plan(result):
    """Store a plan the user chose to keep. Returns the saved trip id."""
    payload = {"hotel": result["hotel"]["name"], "total_estimate": result["total_estimate"],
               "budget_status": result["budget_status"], "partial": result["partial"], "costs": result["costs"]}
    return save_trip(result["destination"], result["origin"], result["budget"], result["nights"],
                     result["group_size"], result["preferences"], json.dumps(payload))


PKT = timezone(timedelta(hours=5), "PKT")      # Pakistan Standard Time, UTC+5, no daylight saving


def to_pakistan_time(utc_text):
    """SQLite stores CURRENT_TIMESTAMP in UTC; convert it to Pakistan time for display."""
    try:
        utc = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return utc_text or ""
    return utc.astimezone(PKT).strftime("%Y-%m-%d %H:%M")


def summarize_trip(row):
    """Turn a saved trip row into display fields (also reads older rows that stored only the costs)."""
    try:
        data = json.loads(row.get("cost_breakdown") or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    costs = data.get("costs", data)
    total = data["total_estimate"] if "total_estimate" in data else \
        sum(v for v in costs.values() if isinstance(v, (int, float)))
    return {"Trip #": row["id"], "Saved at (PKT)": to_pakistan_time(row["created_at"]),
            "From": row["origin"], "To": row["destination"],
            "Nights": row["nights"], "Travelers": row["group_size"], "Budget (PKR)": round(row["budget"]),
            "Estimated total (PKR)": round(total), "Hotel": data.get("hotel", ""),
            "Budget check": data.get("budget_status", ""), "Preferences": row["preferences"] or ""}
