"""
Price / RSI / Volume Alerts — SQLite storage, checked on every tick/poll.
Zero external dependencies beyond Python stdlib.

Schema
------
alerts(
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    type        TEXT NOT NULL,       -- 'price_above' | 'price_below' | 'rsi_above' | 'rsi_below' | 'vol_spike'
    value       REAL NOT NULL,       -- threshold value
    note        TEXT,                -- user note / label
    enabled     INTEGER DEFAULT 1,   -- 0 = disabled
    triggered   INTEGER DEFAULT 0,   -- 1 = already fired
    created_at  TEXT,
    triggered_at TEXT
)
"""
import sqlite3
import time
import os
from pathlib import Path
from datetime import datetime

# DB lives in analysis/data/
_ANALYSIS_DIR = Path(__file__).resolve().parent.parent  # .../analysis
_DB_PATH      = _ANALYSIS_DIR / "data" / "alerts.db"

# Ensure data dir exists
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _conn():
    con = sqlite3.connect(str(_DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def init_db():
    """Create tables if they don't exist."""
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT NOT NULL,
                type         TEXT NOT NULL,
                value        REAL NOT NULL,
                note         TEXT DEFAULT '',
                enabled      INTEGER DEFAULT 1,
                triggered    INTEGER DEFAULT 0,
                created_at   TEXT,
                triggered_at TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS alert_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id    INTEGER,
                symbol      TEXT,
                type        TEXT,
                value       REAL,
                price_at    REAL,
                fired_at    TEXT
            )
        """)
        con.commit()


def _row_to_dict(row) -> dict:
    return {
        "id":           row["id"],
        "symbol":       row["symbol"],
        "type":         row["type"],
        "value":        row["value"],
        "note":         row["note"] or "",
        "enabled":      bool(row["enabled"]),
        "triggered":    bool(row["triggered"]),
        "created_at":   row["created_at"],
        "triggered_at": row["triggered_at"],
    }


# ── CRUD ─────────────────────────────────────────────────────────────────────

def add_alert(symbol: str, alert_type: str, value: float, note: str = "") -> dict:
    """
    alert_type: 'price_above' | 'price_below' | 'rsi_above' | 'rsi_below' | 'vol_spike'
    """
    VALID_TYPES = {"price_above", "price_below", "rsi_above", "rsi_below", "vol_spike"}
    if alert_type not in VALID_TYPES:
        raise ValueError(f"Invalid alert type: {alert_type}. Must be one of {VALID_TYPES}")
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO alerts (symbol, type, value, note, enabled, triggered, created_at) VALUES (?,?,?,?,1,0,?)",
            (symbol.upper(), alert_type, float(value), note, now)
        )
        con.commit()
        row = con.execute("SELECT * FROM alerts WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def get_alerts(symbol: str = None, active_only: bool = False) -> list:
    sql = "SELECT * FROM alerts"
    params = []
    conditions = []
    if symbol:
        conditions.append("symbol=?")
        params.append(symbol.upper())
    if active_only:
        conditions.append("enabled=1 AND triggered=0")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY id DESC"
    with _conn() as con:
        rows = con.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_alert(alert_id: int) -> bool:
    with _conn() as con:
        n = con.execute("DELETE FROM alerts WHERE id=?", (alert_id,)).rowcount
        con.commit()
    return n > 0


def toggle_alert(alert_id: int, enabled: bool) -> bool:
    with _conn() as con:
        n = con.execute("UPDATE alerts SET enabled=? WHERE id=?", (1 if enabled else 0, alert_id)).rowcount
        con.commit()
    return n > 0


def reset_alert(alert_id: int) -> bool:
    """Re-arm a triggered alert so it can fire again."""
    with _conn() as con:
        n = con.execute("UPDATE alerts SET triggered=0, triggered_at=NULL WHERE id=?", (alert_id,)).rowcount
        con.commit()
    return n > 0


def get_alert_history(limit: int = 50) -> list:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM alert_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{
        "id":       r["id"],
        "alert_id": r["alert_id"],
        "symbol":   r["symbol"],
        "type":     r["type"],
        "value":    r["value"],
        "price_at": r["price_at"],
        "fired_at": r["fired_at"],
    } for r in rows]


# ── Checker — called every tick or on poll ────────────────────────────────────

def check_alerts(price_map: dict, rsi_map: dict = None, vol_map: dict = None) -> list:
    """
    Check all active alerts against current market data.

    Parameters
    ----------
    price_map : {SYMBOL: price_float}
    rsi_map   : {SYMBOL: rsi_float}  (optional — computed separately)
    vol_map   : {SYMBOL: vol_ratio}  (optional — vol / 20d_avg)

    Returns list of fired alerts [{alert_dict, price_at}, ...]
    """
    if rsi_map  is None: rsi_map  = {}
    if vol_map  is None: vol_map  = {}

    active = get_alerts(active_only=True)
    fired  = []
    now    = datetime.utcnow().isoformat()

    for alert in active:
        sym   = alert["symbol"]
        atype = alert["type"]
        val   = alert["value"]
        hit   = False
        price_at = price_map.get(sym, 0)

        if atype == "price_above" and sym in price_map:
            hit = price_map[sym] >= val
        elif atype == "price_below" and sym in price_map:
            hit = price_map[sym] <= val
        elif atype == "rsi_above" and sym in rsi_map:
            hit = rsi_map[sym] >= val
        elif atype == "rsi_below" and sym in rsi_map:
            hit = rsi_map[sym] <= val
        elif atype == "vol_spike" and sym in vol_map:
            hit = vol_map[sym] >= val

        if hit:
            fired.append({**alert, "price_at": price_at, "fired_at": now})
            with _conn() as con:
                con.execute(
                    "UPDATE alerts SET triggered=1, triggered_at=? WHERE id=?",
                    (now, alert["id"])
                )
                con.execute(
                    "INSERT INTO alert_history (alert_id, symbol, type, value, price_at, fired_at) VALUES (?,?,?,?,?,?)",
                    (alert["id"], sym, atype, val, price_at, now)
                )
                con.commit()

    return fired


# Initialise DB on import
init_db()
