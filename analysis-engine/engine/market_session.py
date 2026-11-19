"""
Market session tracker — NSE + MCX
========================================
NSE Equity / Derivatives / Currency:
  Mon–Fri  09:15 – 15:30 IST  (excl. NSE trading holidays)

MCX Commodity:
  Mon–Fri  09:00 – 23:30 IST  (excl. MCX holidays)
  On partial holidays (Holi, Eid etc.): morning closed but evening open 17:00–23:30
  On full holidays (Good Friday, Christmas etc.): entire day closed

Holiday data source: NSE + MCX official sites
"""
import os
import csv
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Set, Optional, Tuple
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# ── Session timings ───────────────────────────────────────────────────────────
NSE_OPEN  = time(9, 15)
NSE_CLOSE = time(15, 30)

MCX_OPEN  = time(9, 0)
MCX_CLOSE = time(23, 30)

BASE_DIR = Path(__file__).resolve().parent.parent


# ── Holiday loader ────────────────────────────────────────────────────────────
def _load_holidays_csv() -> Tuple[Set[str], Set[str], Set[str]]:
    """Returns (nse_closed, mcx_full_closed, mcx_morning_only_closed) sets."""
    nse_closed    : Set[str] = set()
    mcx_am_closed : Set[str] = set()
    mcx_pm_closed : Set[str] = set()

    path = BASE_DIR / "assets" / "market_holidays.csv"
    if not path.exists():
        print(f"[market_session] holidays file not found: {path}")
        return nse_closed, mcx_am_closed, mcx_pm_closed

    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw = (row.get('date') or '').strip()
                if not raw:
                    continue
                date_str = None
                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                    try:
                        date_str = datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
                if not date_str:
                    continue

                nse      = (row.get('nse', '')         or '').strip().lower()
                mcx_morn = (row.get('mcx_morning', '') or '').strip().lower()
                mcx_eve  = (row.get('mcx_evening', '') or '').strip().lower()

                if nse == 'closed':
                    nse_closed.add(date_str)
                if mcx_morn == 'closed':
                    mcx_am_closed.add(date_str)
                if mcx_eve == 'closed':
                    mcx_pm_closed.add(date_str)
    except Exception as e:
        print(f"[market_session] Error loading holidays: {e}")

    return nse_closed, mcx_am_closed, mcx_pm_closed


# ── Holiday caches ────────────────────────────────────────────────────────────
_nse_holidays    : Optional[Set[str]] = None
_mcx_am_holidays : Optional[Set[str]] = None
_mcx_pm_holidays : Optional[Set[str]] = None


def _ensure_loaded():
    global _nse_holidays, _mcx_am_holidays, _mcx_pm_holidays
    if _nse_holidays is None:
        _nse_holidays, _mcx_am_holidays, _mcx_pm_holidays = _load_holidays_csv()


def reload_holidays():
    global _nse_holidays, _mcx_am_holidays, _mcx_pm_holidays
    _nse_holidays = None
    _ensure_loaded()


# ── NSE session ───────────────────────────────────────────────────────────────
def is_nse_holiday(dt: Optional[datetime] = None) -> bool:
    if dt is None:
        dt = datetime.now(IST)
    if dt.weekday() >= 5:
        return True
    _ensure_loaded()
    return dt.strftime("%Y-%m-%d") in _nse_holidays


def is_market_hours(dt: Optional[datetime] = None) -> bool:
    """NSE equity: Mon–Fri 09:15–15:30 IST, excl. holidays."""
    if dt is None:
        dt = datetime.now(IST)
    if is_nse_holiday(dt):
        return False
    return NSE_OPEN <= dt.time() <= NSE_CLOSE


# ── MCX session ───────────────────────────────────────────────────────────────
def is_commodity_market_hours(dt: Optional[datetime] = None) -> bool:
    """
    MCX commodity: Mon–Fri 09:00–23:30 IST.
    Partial holidays (Holi, Eid etc.): morning closed, evening 17:00–23:30 open.
    Full holidays (Good Friday, Christmas etc.): entire day closed.
    """
    if dt is None:
        dt = datetime.now(IST)

    if dt.weekday() >= 5:
        return False

    _ensure_loaded()
    date_str = dt.strftime("%Y-%m-%d")
    t = dt.time()

    am_closed = date_str in _mcx_am_holidays
    pm_closed = date_str in _mcx_pm_holidays

    # Full closure (both sessions closed)
    if am_closed and pm_closed:
        return False

    # Morning-only holiday — only evening session open 17:00–23:30
    if am_closed and not pm_closed:
        return time(17, 0) <= t <= MCX_CLOSE

    # Evening-only holiday — only morning session open 09:00–17:00
    if pm_closed and not am_closed:
        return MCX_OPEN <= t < time(17, 0)

    # Normal day — full 09:00–23:30
    return MCX_OPEN <= t <= MCX_CLOSE


# ── Human-readable status ─────────────────────────────────────────────────────
def get_market_status(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now(IST)
    if dt.weekday() >= 5:
        return "weekend"
    _ensure_loaded()
    date_str = dt.strftime("%Y-%m-%d")
    t = dt.time()
    if date_str in _nse_holidays:
        return "holiday"
    if t < NSE_OPEN:
        return "pre-market"
    if t <= NSE_CLOSE:
        return "open"
    return "post-market"


def get_full_status(dt: Optional[datetime] = None) -> dict:
    if dt is None:
        dt = datetime.now(IST)
    _ensure_loaded()
    date_str = dt.strftime("%Y-%m-%d")
    return {
        "datetime_ist": dt.strftime("%Y-%m-%d %H:%M:%S IST"),
        "weekday":      dt.strftime("%A"),
        "nse_open":     is_market_hours(dt),
        "mcx_open":     is_commodity_market_hours(dt),
        "nse_status":   get_market_status(dt),
        "nse_holiday":  date_str in _nse_holidays,
    }


# ── Next open helpers ─────────────────────────────────────────────────────────
def get_next_market_open(dt: Optional[datetime] = None) -> datetime:
    if dt is None:
        dt = datetime.now(IST)
    candidate = dt.replace(hour=9, minute=15, second=0, microsecond=0)
    if candidate <= dt:
        candidate += timedelta(days=1)
    while is_nse_holiday(candidate):
        candidate += timedelta(days=1)
    return candidate


def get_next_market_close(dt: Optional[datetime] = None) -> datetime:
    if dt is None:
        dt = datetime.now(IST)
    candidate = dt.replace(hour=15, minute=30, second=0, microsecond=0)
    if candidate <= dt:
        candidate += timedelta(days=1)
    while is_nse_holiday(candidate):
        candidate += timedelta(days=1)
    return candidate


# ── Backward compat aliases ───────────────────────────────────────────────────
def is_holiday(dt: Optional[datetime] = None) -> bool:
    return is_nse_holiday(dt)

def is_mcx_morning_session(dt: Optional[datetime] = None) -> bool:
    if dt is None: dt = datetime.now(IST)
    return is_commodity_market_hours(dt) and dt.time() < time(17, 0)

def is_mcx_evening_session(dt: Optional[datetime] = None) -> bool:
    if dt is None: dt = datetime.now(IST)
    return is_commodity_market_hours(dt) and dt.time() >= time(17, 0)


# ── Quick self-test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    def _ist(y, m, d, h=10, mi=0):
        return datetime(y, m, d, h, mi, tzinfo=IST)

    tests = [
        (_ist(2026, 1, 15, 10), False, False, "Election holiday 10am"),
        (_ist(2026, 3, 3, 10),  False, False, "Holi 10am — MCX morning closed"),
        (_ist(2026, 3, 3, 18),  False, True,  "Holi 18pm — MCX evening open"),
        (_ist(2026, 4, 3, 11),  False, False, "Good Friday 11am — full closure"),
        (_ist(2026, 4, 3, 19),  False, False, "Good Friday 19pm — full closure"),
        (_ist(2026, 6, 8, 10),  True,  True,  "Normal Mon 10am — both open"),
        (_ist(2026, 6, 8, 16),  False, True,  "Normal Mon 16pm — MCX open"),
        (_ist(2026, 6, 8, 22),  False, True,  "Normal Mon 22pm — MCX evening open"),
        (_ist(2026, 6, 7, 11),  False, False, "Sunday — all closed"),
        (_ist(2026, 12, 25, 11),False, False, "Christmas — full closure"),
    ]

    print(f"{'Label':<45} {'NSE':>5} {'MCX':>5} {'OK':>4}")
    print("-" * 62)
    all_ok = True
    for dt, exp_nse, exp_mcx, label in tests:
        got_nse = is_market_hours(dt)
        got_mcx = is_commodity_market_hours(dt)
        ok = got_nse == exp_nse and got_mcx == exp_mcx
        all_ok = all_ok and ok
        print(f"{label:<45} {str(exp_nse):>5} {str(exp_mcx):>5} {'✓' if ok else f'✗ got NSE:{got_nse} MCX:{got_mcx}':>4}")

    import json
    print("\nCurrent:", json.dumps(get_full_status(), indent=2))
    print("\nAll tests passed!" if all_ok else "\n⚠ Some tests FAILED")
