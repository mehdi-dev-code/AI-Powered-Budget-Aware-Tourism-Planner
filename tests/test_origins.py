"""Tests for the starting-city list: big cities are always offered, with honest handling of missing fares."""
import shutil
import pytest
from backend import database
from backend.database import (initialize_database, list_origin_cities, route_kind, MAJOR_CITIES, get_connection)
from backend.planner import build_budget_plan
from backend.tools import call_tool


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(database.DATA_DIR, data)
    monkeypatch.setattr(database, "DATA_DIR", data)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    initialize_database(reset=True)
    return data


def _add_route(route_id, origin, destination, fare):
    conn = get_connection()
    conn.execute("INSERT INTO transport (id, mode, from_city, to_city, fare_per_person) VALUES (?,?,?,?,?)",
                 (route_id, "Test bus", origin, destination, fare))
    conn.commit()
    conn.close()


def test_big_cities_are_always_offered_as_starting_cities():
    origins = list_origin_cities()
    for city in ("Lahore", "Karachi", "Peshawar", "Quetta", "Islamabad", "Hunza"):
        assert city in origins
    assert origins == sorted(set(origins)) and set(MAJOR_CITIES) <= set(origins)


def test_route_kind_reports_what_the_data_has():
    assert route_kind("Islamabad", "Hunza") == "direct"
    assert route_kind("Hunza", "Skardu") == "connecting"        # via Gilgit
    assert route_kind("Lahore", "Hunza") is None


def test_city_without_fares_gives_an_honest_partial_estimate():
    plan = build_budget_plan("Lahore", "Hunza", 60000, 2, 2)
    assert plan["partial"] is True and plan["budget_status"] in ("inconclusive", "over")
    assert any(w.startswith("No transport data") for w in plan["warnings"])


def test_adding_one_route_to_islamabad_completes_the_plan_through_a_connection():
    _add_route(50, "Lahore", "Islamabad", 3000)
    assert route_kind("Lahore", "Hunza") == "connecting"
    plan = build_budget_plan("Lahore", "Hunza", 60000, 2, 2)
    assert plan["partial"] is False
    assert plan["transport"]["fare_per_person"] == 3000 + 6000
    assert plan["transport"]["via"] == "Islamabad"


def test_the_assistant_is_told_about_missing_fares():
    result = call_tool("list_destinations", {})
    assert "Lahore" in result["starting_cities"] and "partial" in result["note"]
