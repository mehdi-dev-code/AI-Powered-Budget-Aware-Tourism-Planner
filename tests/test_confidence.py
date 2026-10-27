"""Tests for data confidence labels, the database upgrade and owner-managed rows."""
import shutil
import sqlite3
from datetime import date
import pandas as pd
import pytest
from backend import database
from backend.database import (initialize_database, effective_confidence, weakest_confidence, clean_hotels,
                              search_hotels, get_connection, refresh_data, get_connecting_options, STALE_DAYS)
from backend.planner import build_budget_plan
from backend.tools import find_hotels, find_transport


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(database.DATA_DIR, data)
    monkeypatch.setattr(database, "DATA_DIR", data)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    initialize_database(reset=True)
    return data


def _set(table, column, value, where="1=1"):
    conn = get_connection()
    conn.execute(f"UPDATE {table} SET {column}=? WHERE {where}", (value,))
    conn.commit()
    conn.close()


# ---- labels
def test_missing_or_unknown_label_is_unknown():
    assert effective_confidence(None) == "Unknown"
    assert effective_confidence("nonsense") == "Unknown"
    assert effective_confidence("Estimated") == "Estimated"


def test_verified_needs_a_recent_date():
    today = date(2026, 11, 1)
    assert effective_confidence("Verified", "2026-10-30", today) == "Verified"
    assert effective_confidence("Verified", "2026-10-30") in ("Verified", "Needs reconfirmation")
    assert effective_confidence("Verified", None, today) == "Needs reconfirmation"
    assert effective_confidence("Verified", "not a date", today) == "Needs reconfirmation"
    assert effective_confidence("Verified", "2026-08-01", today) == "Needs reconfirmation"   # older than STALE_DAYS
    assert STALE_DAYS == 60


def test_weakest_label_wins():
    assert weakest_confidence(["Verified", "Estimated"]) == "Estimated"
    assert weakest_confidence(["Verified", "Needs reconfirmation"]) == "Needs reconfirmation"
    assert weakest_confidence(["Estimated", "Unknown"]) == "Unknown"
    assert weakest_confidence([]) == "Unknown"


# ---- validation
def _hotel_row(**extra):
    row = {"id": 1, "name": "A Inn", "city": "Hunza", "price_per_night": 3000}
    row.update(extra)
    return pd.DataFrame([row])


def test_confidence_is_case_normalised_and_invalid_values_rejected():
    clean, rejected = clean_hotels(_hotel_row(confidence="verified", last_verified="2026-10-01"))
    assert list(clean["confidence"]) == ["Verified"] and rejected == []
    clean, rejected = clean_hotels(_hotel_row(confidence="probably"))
    assert len(clean) == 0 and "invalid confidence" in rejected[0][1]


def test_bad_last_verified_date_is_rejected():
    _, rejected = clean_hotels(_hotel_row(last_verified="26/10/2026"))
    assert "malformed last_verified" in rejected[0][1]


def test_new_columns_are_optional_in_the_csv():
    clean, rejected = clean_hotels(_hotel_row())
    assert len(clean) == 1 and rejected == []


# ---- upgrading an existing database
def test_old_database_gets_the_new_columns(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE hotels (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, area TEXT, "
                 "price_per_night REAL NOT NULL, facilities TEXT, contact TEXT, availability TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO hotels VALUES (1, 'Old Inn', 'Hunza', 'x', 3000, 'Wi-Fi', 'c', 'a', '2026-10-01')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(database, "DB_PATH", old)
    initialize_database()
    row = search_hotels("Hunza")[0]
    assert row["name"] == "Old Inn" and row["confidence"] is None and row["source"] is None


# ---- plans and tools
def test_sample_data_gives_a_low_confidence_plan_with_a_warning():
    plan = build_budget_plan("Islamabad", "Hunza", 60000, 2, 2)
    assert plan["data_confidence"]["hotel"] == "Unknown"
    assert plan["data_confidence"]["transport"] == "Estimated"
    assert plan["data_confidence"]["overall"] == "Unknown"
    assert any("Data confidence" in w for w in plan["warnings"])


def test_fully_verified_data_gives_a_verified_plan():
    today = date.today().isoformat()
    for table in ("hotels", "transport"):
        _set(table, "confidence", "Verified")
        _set(table, "last_verified", today)
    plan = build_budget_plan("Islamabad", "Hunza", 60000, 2, 2)
    assert plan["data_confidence"]["overall"] == "Verified"
    assert not any("Data confidence" in w for w in plan["warnings"])


def test_old_verification_is_demoted():
    _set("hotels", "confidence", "Verified")
    _set("hotels", "last_verified", "2024-01-01")
    assert search_hotels("Hunza") and find_hotels("Hunza")["hotels"][0]["confidence"] == "Needs reconfirmation"


def test_connecting_route_is_as_weak_as_its_weakest_leg():
    today = date.today().isoformat()
    _set("transport", "confidence", "Verified")
    _set("transport", "last_verified", today)
    _set("transport", "confidence", "Estimated", "from_city='Hunza' AND to_city='Gilgit'")
    option = get_connecting_options("Hunza", "Skardu")[0]
    assert option["confidence"] == "Estimated"


def test_tools_show_the_confidence_label():
    assert find_hotels("Hunza")["hotels"][0]["confidence"] == "Unknown"
    assert find_transport("Islamabad", "Hunza")["options"][0]["confidence"] == "Estimated"


# ---- owner-managed rows
def test_csv_refresh_never_overwrites_or_prunes_owner_rows(temp_env):
    _set("hotels", "source", "owner", "name='Karimabad Budget Stay'")
    _set("hotels", "price_per_night", 9999, "name='Karimabad Budget Stay'")
    csv_path = temp_env / "hotels_sample.csv"
    df = pd.read_csv(csv_path)
    df.loc[df["name"] == "Karimabad Budget Stay", "price_per_night"] = 1000      # CSV tries to change it
    df = df[df["name"] != "Hunza Valley Guest House"]                               # and drops another row
    df.to_csv(csv_path, index=False)
    report = refresh_data(prune=True)["hotels"]
    assert report["protected"] == 1
    prices = {h["name"]: h["price_per_night"] for h in search_hotels("Hunza")}
    assert prices["Karimabad Budget Stay"] == 9999
    assert "Hunza Valley Guest House" not in prices                                  # normal rows are still pruned
