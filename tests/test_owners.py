"""Tests for hotel-owner logins, owner edits and the admin password."""
import shutil
import sqlite3
from datetime import date
import pandas as pd
import pytest
from backend import database
from backend.database import initialize_database, search_hotels, get_connection, refresh_data, effective_confidence
from backend.owners import (create_owner, verify_owner, update_own_hotel, get_owner_hotel, create_hotel, list_owners,
                            delete_owner, check_admin_password, admin_password_configured)
from backend.planner import build_budget_plan


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    shutil.copytree(database.DATA_DIR, data)
    monkeypatch.setattr(database, "DATA_DIR", data)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    initialize_database(reset=True)
    return data


def _hotel_id(name):
    return next(h["id"] for c in ("Hunza", "Skardu", "Gilgit") for h in search_hotels(c) if h["name"] == name)


def _raises(func, *args, text=""):
    try:
        func(*args)
    except ValueError as exc:
        assert text in str(exc), str(exc)
        return
    assert False, "expected ValueError"


# ---- admin password
def test_admin_is_locked_when_no_password_is_set():
    assert admin_password_configured(env={}) is False
    assert check_admin_password("anything", env={}) is False
    assert check_admin_password("", env={"ADMIN_PASSWORD": "secret-pass"}) is False


def test_admin_password_must_match_exactly():
    env = {"ADMIN_PASSWORD": "secret-pass"}
    assert check_admin_password("secret-pass", env=env) is True
    assert check_admin_password("secret-pas", env=env) is False


# ---- accounts
def test_owner_can_log_in_with_the_right_password_only():
    owner_id = create_owner("karim.owner", "correct-horse", _hotel_id("Karimabad Budget Stay"))
    ok = verify_owner("karim.owner", "correct-horse")
    assert ok["id"] == owner_id and ok["hotel_id"] == _hotel_id("Karimabad Budget Stay")
    assert verify_owner("karim.owner", "wrong-pass-1") is None
    assert verify_owner("nobody", "correct-horse") is None
    assert verify_owner("KARIM.OWNER", "correct-horse") is not None        # usernames ignore case


def test_password_is_not_stored_as_text(tmp_path):
    create_owner("karim.owner", "correct-horse", _hotel_id("Karimabad Budget Stay"))
    conn = sqlite3.connect(tmp_path / "test.db")
    stored = " ".join(str(v) for row in conn.execute("SELECT * FROM owners") for v in row)
    assert "correct-horse" not in stored
    assert len(conn.execute("SELECT password_hash FROM owners").fetchone()[0]) == 64
    assert all("password" not in k for o in list_owners() for k in o)       # listing never exposes hashes


def test_account_rules():
    hid = _hotel_id("Karimabad Budget Stay")
    _raises(create_owner, "ab", "correct-horse", hid, text="Username")
    _raises(create_owner, "karim.owner", "short", hid, text="at least 8")
    _raises(create_owner, "karim.owner", "correct-horse", 99999, text="does not exist")
    create_owner("karim.owner", "correct-horse", hid)
    _raises(create_owner, "other.owner", "correct-horse", hid, text="already has an owner")
    _raises(create_owner, "karim.owner", "correct-horse", _hotel_id("Hunza Valley Guest House"), text="taken")


def test_delete_owner_removes_the_login():
    oid = create_owner("karim.owner", "correct-horse", _hotel_id("Karimabad Budget Stay"))
    delete_owner(oid)
    assert verify_owner("karim.owner", "correct-horse") is None and list_owners() == []


# ---- owner edits
def test_owner_update_changes_only_their_own_hotel_and_marks_it_verified():
    mine, other = _hotel_id("Karimabad Budget Stay"), _hotel_id("Hunza Valley Guest House")
    oid = create_owner("karim.owner", "correct-horse", mine)
    before = {h["name"]: h["price_per_night"] for h in search_hotels("Hunza")}
    hotel = update_own_hotel(oid, 3500, "Wi-Fi; Hot water", "Open all year", "0300-1234567")
    assert hotel["price_per_night"] == 3500 and hotel["source"] == "owner"
    assert hotel["confidence"] == "Verified" and hotel["last_verified"] == date.today().isoformat()
    assert effective_confidence(hotel["confidence"], hotel["last_verified"]) == "Verified"
    after = {h["name"]: h["price_per_night"] for h in search_hotels("Hunza")}
    assert after["Hunza Valley Guest House"] == before["Hunza Valley Guest House"]      # the other hotel is untouched
    assert get_owner_hotel(oid)["id"] == mine and other != mine


def test_owner_update_validates_input():
    oid = create_owner("karim.owner", "correct-horse", _hotel_id("Karimabad Budget Stay"))
    _raises(update_own_hotel, oid, -5, "Wi-Fi", "Open", "c", text="between")
    _raises(update_own_hotel, oid, "abc", "Wi-Fi", "Open", "c", text="number")
    _raises(update_own_hotel, oid, 99999999, "Wi-Fi", "Open", "c", text="between")
    _raises(update_own_hotel, oid, 3000, "x" * 500, "Open", "c", text="too long")
    _raises(update_own_hotel, 424242, 3000, "Wi-Fi", "Open", "c", text="Unknown owner")


def test_planner_sees_the_owner_update_straight_away():
    oid = create_owner("karim.owner", "correct-horse", _hotel_id("Karimabad Budget Stay"))
    update_own_hotel(oid, 2000, "Wi-Fi; Hot water", "Open", "0300-1234567")
    plan = build_budget_plan("Islamabad", "Hunza", 60000, 2, 2)
    assert plan["hotel"]["name"] == "Karimabad Budget Stay" and plan["hotel"]["price_per_night"] == 2000
    assert plan["data_confidence"]["hotel"] == "Verified"


def test_csv_refresh_keeps_owner_edits(temp_env):
    oid = create_owner("karim.owner", "correct-horse", _hotel_id("Karimabad Budget Stay"))
    update_own_hotel(oid, 2000, "Wi-Fi", "Open", "c")
    report = refresh_data(prune=True)
    assert report["hotels"]["protected"] >= 1
    assert get_owner_hotel(oid)["price_per_night"] == 2000


# ---- hotels created in the app
def test_admin_created_hotel_starts_unknown_and_survives_refresh():
    hid = create_hotel("Altit View Hotel", "hunza", "Altit", 4100, "Wi-Fi", "0300-0000001", "Open")
    assert hid >= 1000
    hotel = next(h for h in search_hotels("Hunza") if h["id"] == hid)
    assert hotel["confidence"] == "Unknown" and hotel["source"] == "admin"
    refresh_data(prune=True)
    assert any(h["id"] == hid for h in search_hotels("Hunza"))


def test_duplicate_or_invalid_hotel_is_refused():
    _raises(create_hotel, "Karimabad Budget Stay", "Hunza", "x", 3000, "", "", "", text="already exists")
    _raises(create_hotel, "", "Hunza", "x", 3000, "", "", "", text="required")
    _raises(create_hotel, "New Inn", "Hunza", "x", -1, "", "", "", text="between")
