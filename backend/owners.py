"""Hotel-owner accounts and the admin password.

Owners log in and update ONLY their own hotel's price, facilities, availability and contact. Saving marks the record
"Verified" today, so the planner and the assistant see the change at once. Passwords are stored as salted PBKDF2
hashes, never as text. The admin password comes from ADMIN_PASSWORD in .env; without it the admin tools stay locked.
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import date
from .database import get_connection, initialize_database
from .llm import load_env

ITERATIONS = 200_000
MIN_PASSWORD_LENGTH = 8
MAX_PRICE = 500_000
FIRST_APP_HOTEL_ID = 1000          # hotels created in the app get ids from here, so they never clash with CSV ids
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")


# ---------------------------------------------------------------- admin password
def admin_password_configured(env=None):
    load_env()
    return bool((os.environ if env is None else env).get("ADMIN_PASSWORD"))


def check_admin_password(candidate, env=None):
    """True only when ADMIN_PASSWORD is set and matches. With no admin password configured nobody gets in."""
    load_env()
    expected = (os.environ if env is None else env).get("ADMIN_PASSWORD")
    if not expected or not candidate:
        return False
    return hmac.compare_digest(str(candidate).encode(), expected.encode())


# ---------------------------------------------------------------- helpers
def _hash(password, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), ITERATIONS).hex()


def _connect():
    initialize_database()
    conn = get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS owners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        hotel_id INTEGER NOT NULL UNIQUE,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    return conn


def _text(value, label, max_len=200, required=False):
    value = " ".join(str(value or "").split())
    if required and not value:
        raise ValueError(f"{label} is required.")
    if len(value) > max_len:
        raise ValueError(f"{label} is too long (at most {max_len} characters).")
    return value or None


def _price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        raise ValueError("Price per night must be a number.")
    if not 0 <= price <= MAX_PRICE:
        raise ValueError(f"Price per night must be between 0 and {MAX_PRICE:,}.")
    return price


# ---------------------------------------------------------------- accounts (admin actions)
def create_hotel(name, city, area, price_per_night, facilities, contact, availability, today=None):
    """Add a hotel from inside the app. It starts as 'Unknown' until its owner confirms the details."""
    name, city = _text(name, "Hotel name", 80, True), _text(city, "City", 40, True).title()
    price = _price(price_per_night)
    conn = _connect()
    try:
        if conn.execute("SELECT 1 FROM hotels WHERE lower(name)=lower(?) AND lower(city)=lower(?)", (name, city)).fetchone():
            raise ValueError("A hotel with this name already exists in this city.")
        hotel_id = max(FIRST_APP_HOTEL_ID, (conn.execute("SELECT COALESCE(MAX(id), 0) FROM hotels").fetchone()[0]) + 1)
        conn.execute("""INSERT INTO hotels (id, name, city, area, price_per_night, facilities, contact, availability,
                        updated_at, confidence, last_verified, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (hotel_id, name, city, _text(area, "Area", 80), price, _text(facilities, "Facilities"),
                      _text(contact, "Contact", 80), _text(availability, "Availability", 120),
                      (today or date.today()).isoformat(), "Unknown", None, "admin"))
        conn.commit()
        return hotel_id
    finally:
        conn.close()


def create_owner(username, password, hotel_id):
    """Create a login for one hotel. Returns the owner id."""
    username = (username or "").strip()
    if not USERNAME.match(username):
        raise ValueError("Username must be 3 to 30 letters, digits, dots, dashes or underscores.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    conn = _connect()
    try:
        if not conn.execute("SELECT 1 FROM hotels WHERE id=?", (hotel_id,)).fetchone():
            raise ValueError("That hotel does not exist.")
        if conn.execute("SELECT 1 FROM owners WHERE hotel_id=?", (hotel_id,)).fetchone():
            raise ValueError("This hotel already has an owner login.")
        if conn.execute("SELECT 1 FROM owners WHERE username=?", (username,)).fetchone():
            raise ValueError("That username is taken.")
        salt = secrets.token_hex(16)
        cur = conn.execute("INSERT INTO owners (username, password_hash, salt, hotel_id) VALUES (?,?,?,?)",
                           (username, _hash(password, salt), salt, int(hotel_id)))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_owners():
    """Owner logins with their hotel names. Password data is never returned."""
    conn = _connect()
    rows = conn.execute("""SELECT o.id, o.username, o.hotel_id, h.name AS hotel, h.city, o.created_at
                           FROM owners o LEFT JOIN hotels h ON h.id = o.hotel_id ORDER BY o.id""").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_owner(owner_id):
    conn = _connect()
    conn.execute("DELETE FROM owners WHERE id=?", (int(owner_id),))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- owner actions
def verify_owner(username, password):
    """Return {"id", "username", "hotel_id"} when the login is right, otherwise None."""
    conn = _connect()
    row = conn.execute("SELECT * FROM owners WHERE username=?", ((username or "").strip(),)).fetchone()
    conn.close()
    salt = row["salt"] if row else "00" * 16                     # do the same work when the user does not exist
    attempt = _hash(password or "", salt)
    if row and hmac.compare_digest(attempt, row["password_hash"]):
        return {"id": row["id"], "username": row["username"], "hotel_id": row["hotel_id"]}
    return None


def get_owner_hotel(owner_id):
    conn = _connect()
    row = conn.execute("SELECT h.* FROM hotels h JOIN owners o ON o.hotel_id = h.id WHERE o.id=?",
                       (int(owner_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_own_hotel(owner_id, price_per_night, facilities, availability, contact, area=None, today=None):
    """Save an owner's changes to their own hotel and mark it Verified today. Returns the updated hotel."""
    price = _price(price_per_night)
    values = (price, _text(facilities, "Facilities"), _text(availability, "Availability", 120),
              _text(contact, "Contact", 80), _text(area, "Area", 80))
    conn = _connect()
    try:
        owner = conn.execute("SELECT hotel_id FROM owners WHERE id=?", (int(owner_id),)).fetchone()
        if owner is None:
            raise ValueError("Unknown owner account.")
        day = (today or date.today()).isoformat()
        conn.execute("""UPDATE hotels SET price_per_night=?, facilities=?, availability=?, contact=?,
                        area=COALESCE(?, area), updated_at=?, confidence='Verified', last_verified=?, source='owner'
                        WHERE id=?""", (*values, day, day, owner["hotel_id"]))
        conn.commit()
    finally:
        conn.close()
    return get_owner_hotel(owner_id)
