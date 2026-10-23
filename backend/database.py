"""SQLite data-access layer for the tourism planner prototype."""
from pathlib import Path
import sqlite3
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = ROOT / "tourism_planner.db"

HOTEL_COLUMNS = ["id", "name", "city", "area", "price_per_night", "facilities", "contact", "availability", "updated_at"]
TRANSPORT_COLUMNS = ["id", "mode", "from_city", "to_city", "fare_per_person", "duration_hours", "notes", "updated_at"]


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------- validation
def _validate(df, *, required_cols, text_cols, city_cols, required_text, nonneg_cols, positive_cols, date_cols, dup_keys):
    """Clean and validate rows. Returns (clean_df, rejected) where rejected is a list of (csv_line, reason).

    A rejection with line 0 describes the whole file (for example a missing required column).
    """
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        return df.iloc[0:0].copy(), [(0, f"missing required column: {c}") for c in missing]
    df = df.copy().reset_index(drop=True)
    reason = pd.Series("", index=df.index, dtype=object)

    def reject(mask, text):
        mask = mask.fillna(False).astype(bool) & (reason == "")
        reason[mask] = text

    for col in text_cols:
        if col in df.columns:
            s = df[col].astype("string").str.strip()
            df[col] = s.where(s != "", pd.NA)           # whitespace-only text becomes missing
    for col in city_cols:
        df[col] = df[col].str.title()
    for col in required_text:
        reject(df[col].isna(), f"missing {col}")

    if "id" in df.columns:
        ids = pd.to_numeric(df["id"], errors="coerce")
        reject(ids.isna() | (ids % 1 != 0) | (ids <= 0), "invalid id")
        df["id"] = ids
        reject(df["id"].duplicated(keep="first"), "duplicate id")

    for col in nonneg_cols + positive_cols:
        if col not in df.columns:                       # optional column that is absent
            continue
        raw = df[col]
        num = pd.to_numeric(raw, errors="coerce")
        df[col] = num
        if col in nonneg_cols:
            reject(num.isna() | (num < 0), f"invalid {col} (must be a number, 0 or more)")
        else:                                           # optional, but must be positive when given
            reject(raw.notna() & (num.isna() | (num <= 0)), f"invalid {col} (must be a positive number)")

    for col in date_cols:
        raw = df[col].astype("string").str.strip() if col in df.columns else None
        if raw is None:
            continue
        parsed = pd.to_datetime(raw.where(raw != ""), format="%Y-%m-%d", errors="coerce")
        reject(raw.notna() & (raw != "") & parsed.isna(), f"malformed {col} (use YYYY-MM-DD)")
        df[col] = parsed.dt.strftime("%Y-%m-%d")

    reject(df.duplicated(subset=dup_keys, keep="first"), "duplicate record")

    rejected = [(int(i) + 2, reason[i]) for i in df.index if reason[i]]   # +2: header is line 1
    clean = df[reason == ""].copy()
    if "id" in clean.columns:
        clean["id"] = clean["id"].astype(int)
    return clean, rejected


def clean_hotels(df):
    return _validate(df, required_cols=["id", "name", "city", "price_per_night"],
                     text_cols=["name", "city", "area", "facilities", "contact", "availability"],
                     city_cols=["city"], required_text=["name", "city"], nonneg_cols=["price_per_night"],
                     positive_cols=[], date_cols=["updated_at"], dup_keys=["name", "city"])


def clean_transport(df):
    return _validate(df, required_cols=["id", "mode", "from_city", "to_city", "fare_per_person"],
                     text_cols=["mode", "from_city", "to_city", "notes"], city_cols=["from_city", "to_city"],
                     required_text=["mode", "from_city", "to_city"], nonneg_cols=["fare_per_person"],
                     positive_cols=["duration_hours"], date_cols=["updated_at"],
                     dup_keys=["mode", "from_city", "to_city"])


# ---------------------------------------------------------------- schema + loading
def _create_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hotels (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            city TEXT NOT NULL,
            area TEXT,
            price_per_night REAL NOT NULL CHECK(price_per_night >= 0),
            facilities TEXT,
            contact TEXT,
            availability TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transport (
            id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL,
            from_city TEXT NOT NULL,
            to_city TEXT NOT NULL,
            fare_per_person REAL NOT NULL CHECK(fare_per_person >= 0),
            duration_hours REAL CHECK(duration_hours IS NULL OR duration_hours > 0),
            notes TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            destination TEXT NOT NULL,
            origin TEXT NOT NULL,
            budget REAL NOT NULL CHECK(budget >= 0),
            nights INTEGER NOT NULL CHECK(nights >= 1),
            group_size INTEGER NOT NULL CHECK(group_size >= 1),
            preferences TEXT,
            cost_breakdown TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _py(value):
    return None if pd.isna(value) else (value.item() if hasattr(value, "item") else value)


def _sync_table(conn, table, columns, df, prune):
    """Insert new rows and update changed rows (matched by id). Optionally remove rows missing from the CSV."""
    existing = {r["id"]: dict(r) for r in conn.execute(f"SELECT * FROM {table}")}
    added = updated = unchanged = 0
    placeholders = ", ".join("?" for _ in columns)
    assignments = ", ".join(f"{c}=excluded.{c}" for c in columns if c != "id")
    for _, row in df.iterrows():
        values = {c: _py(row[c]) if c in df.columns else None for c in columns}
        old = existing.get(values["id"])
        if old is None:
            added += 1
        elif any(old[c] != values[c] for c in columns):
            updated += 1
        else:
            unchanged += 1
            continue
        conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
                     f"ON CONFLICT(id) DO UPDATE SET {assignments}", [values[c] for c in columns])
    removed = 0
    if prune and len(df):
        keep = set(int(i) for i in df["id"])
        stale = [i for i in existing if i not in keep]
        conn.executemany(f"DELETE FROM {table} WHERE id=?", [(i,) for i in stale])
        removed = len(stale)
    return {"added": added, "updated": updated, "unchanged": unchanged, "removed": removed}


def _load_csv(conn, table, columns, csv_name, cleaner, prune):
    nothing = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0}
    try:
        raw = pd.read_csv(DATA_DIR / csv_name)
    except FileNotFoundError:
        return {**nothing, "rejected": [(0, f"file not found: {csv_name}")]}
    except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        return {**nothing, "rejected": [(0, f"cannot read {csv_name}: {exc}")]}
    clean, rejected = cleaner(raw)
    if rejected and rejected[0][0] == 0:                # file-level problem: change nothing, never prune
        return {**nothing, "rejected": rejected}
    report = _sync_table(conn, table, columns, clean, prune)
    report["rejected"] = rejected
    return report


def initialize_database(reset=False):
    """Create tables and seed them from the cleaned CSV files when they are empty. Safe to rerun."""
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    conn = get_connection()
    _create_tables(conn)
    report = {}
    if conn.execute("SELECT COUNT(*) FROM hotels").fetchone()[0] == 0:
        report["hotels"] = _load_csv(conn, "hotels", HOTEL_COLUMNS, "hotels_sample.csv", clean_hotels, False)
    if conn.execute("SELECT COUNT(*) FROM transport").fetchone()[0] == 0:
        report["transport"] = _load_csv(conn, "transport", TRANSPORT_COLUMNS, "transport_sample.csv", clean_transport, False)
    conn.commit()
    conn.close()
    return report


def refresh_data(prune=False):
    """Controlled data refresh: apply edits made in the CSV files to the existing database.

    New ids are added, changed rows are updated, invalid rows are rejected (and reported).
    With prune=True, database rows whose id is no longer in the CSV are removed.
    A broken file (missing column, unreadable) changes nothing. Saved trips are never touched.
    """
    initialize_database()
    conn = get_connection()
    report = {
        "hotels": _load_csv(conn, "hotels", HOTEL_COLUMNS, "hotels_sample.csv", clean_hotels, prune),
        "transport": _load_csv(conn, "transport", TRANSPORT_COLUMNS, "transport_sample.csv", clean_transport, prune),
    }
    conn.commit()
    conn.close()
    return report


def describe_rejection(line, why):
    return f"CSV file: {why}" if line == 0 else f"CSV line {line}: {why}"


def format_refresh_report(report):
    lines = []
    for table, r in report.items():
        lines.append(f"{table}: {r['added']} added, {r['updated']} updated, {r['unchanged']} unchanged, "
                     f"{r['removed']} removed, {len(r['rejected'])} rejected")
        lines += [f"  - {describe_rejection(line, why)}" for line, why in r["rejected"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- queries
def list_cities():
    """Cities that have at least one hotel record."""
    initialize_database()
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT city FROM hotels ORDER BY city").fetchall()
    conn.close()
    return [r[0] for r in rows]


def list_origin_cities():
    """Cities a trip can start from: every city that has a transport route or a hotel record."""
    initialize_database()
    conn = get_connection()
    rows = conn.execute("""SELECT from_city AS c FROM transport UNION SELECT city FROM hotels
                           ORDER BY c""").fetchall()
    conn.close()
    return [r[0] for r in rows]


def search_hotels(city, max_nightly_price=None):
    initialize_database()
    conn = get_connection()
    query = "SELECT * FROM hotels WHERE lower(city)=lower(?)"
    params = [city]
    if max_nightly_price is not None:
        query += " AND price_per_night <= ?"
        params.append(float(max_nightly_price))
    query += " ORDER BY price_per_night ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_transport_options(origin, destination):
    initialize_database()
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM transport WHERE lower(from_city)=lower(?)
           AND lower(to_city)=lower(?) ORDER BY fare_per_person ASC""",
        (origin, destination)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_connecting_options(origin, destination):
    """One-stop routes built from two existing legs (origin -> via -> destination), cheapest first.

    Fares and durations are summed. Waiting time between legs is not modeled.
    """
    initialize_database()
    conn = get_connection()
    rows = conn.execute(
        """SELECT a.to_city AS via, a.mode AS first_mode, b.mode AS second_mode,
                  a.fare_per_person + b.fare_per_person AS fare_per_person,
                  CASE WHEN a.duration_hours IS NULL OR b.duration_hours IS NULL THEN NULL
                       ELSE a.duration_hours + b.duration_hours END AS duration_hours
           FROM transport a JOIN transport b ON lower(a.to_city) = lower(b.from_city)
           WHERE lower(a.from_city)=lower(?) AND lower(b.to_city)=lower(?)
             AND lower(a.to_city) NOT IN (lower(?), lower(?))
           ORDER BY fare_per_person ASC""",
        (origin, destination, origin, destination)
    ).fetchall()
    conn.close()
    options = []
    for r in rows:
        option = dict(r)
        option["mode"] = f"Connecting route via {option['via']} ({option['first_mode']} + {option['second_mode']})"
        option["connecting"] = True
        options.append(option)
    return options


def save_trip(destination, origin, budget, nights, group_size, preferences, cost_breakdown):
    initialize_database()
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO trips(destination, origin, budget, nights, group_size,
           preferences, cost_breakdown) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (destination, origin, float(budget), int(nights), int(group_size),
         preferences, cost_breakdown)
    )
    conn.commit()
    trip_id = cur.lastrowid
    conn.close()
    return trip_id


def list_trips():
    """Saved trips, newest first."""
    initialize_database()
    conn = get_connection()
    rows = conn.execute("SELECT * FROM trips ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_trip(trip_id):
    conn = get_connection()
    conn.execute("DELETE FROM trips WHERE id=?", (int(trip_id),))
    conn.commit()
    conn.close()
