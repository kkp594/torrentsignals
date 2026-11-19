"""
Upstox Live Price Fetcher for Flask server
Uses Upstox Market Quote API (REST) for current prices.
Token is shared from analysis/data/upstox-token.json
"""
import json
import os
import time
import threading
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
TOKEN_FILE = Path(__file__).resolve().parent.parent / "data" / "upstox-token.json"

# ── Symbol → Upstox Instrument Key mapping ─────────────────────────────────────
# NSE Equity: NSE_EQ|<SYMBOL>
# NSE Index:  NSE_INDEX|<Name>
# MCX Commodity (continuous): MCX_FO|<INSTRUMENT_KEY>
# We use NSE_INDEX for indices (same as JS upstox-feed.js)
# For commodities we use MCX continuous symbols

NSE_INDEX_MAP = {
    "NIFTY50":     "NSE_INDEX|Nifty 50",
    "NIFTY":       "NSE_INDEX|Nifty 50",
    "BANKNIFTY":   "NSE_INDEX|Nifty Bank",
    "FINNIFTY":    "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY":  "NSE_INDEX|NIFTY MID SELECT",
    "NIFTYNXT50":  "NSE_INDEX|Nifty Next 50",
    "SENSEX":      "BSE_INDEX|SENSEX",
    "BANKEX":      "BSE_INDEX|BANKEX",
}

# NSE Equity ISIN map — Upstox v2 REST LTP requires NSE_EQ|ISIN format
# Source: NSE instrument master
NSE_ISIN_MAP = {
    "RELIANCE":    "NSE_EQ|INE002A01018",
    "TCS":         "NSE_EQ|INE467B01029",
    "HDFCBANK":    "NSE_EQ|INE040A01034",
    "INFY":        "NSE_EQ|INE009A01021",
    "ICICIBANK":   "NSE_EQ|INE090A01021",
    "HINDUNILVR":  "NSE_EQ|INE030A01027",
    "ITC":         "NSE_EQ|INE154A01025",
    "SBIN":        "NSE_EQ|INE062A01020",
    "BHARTIARTL":  "NSE_EQ|INE397D01024",
    "KOTAKBANK":   "NSE_EQ|INE237A01028",
    "LT":          "NSE_EQ|INE018A01030",
    "AXISBANK":    "NSE_EQ|INE238A01034",
    "ASIANPAINT":  "NSE_EQ|INE021A01026",
    "MARUTI":      "NSE_EQ|INE585B01010",
    "TITAN":       "NSE_EQ|INE280A01028",
    "SUNPHARMA":   "NSE_EQ|INE044A01036",
    "ULTRACEMCO":  "NSE_EQ|INE481G01011",
    "BAJFINANCE":  "NSE_EQ|INE296A01024",
    "WIPRO":       "NSE_EQ|INE075A01022",
    "NESTLEIND":   "NSE_EQ|INE239A01016",
    "POWERGRID":   "NSE_EQ|INE752E01010",
    "NTPC":        "NSE_EQ|INE733E01010",
    "TECHM":       "NSE_EQ|INE669C01036",
    "HCLTECH":     "NSE_EQ|INE860A01027",
    "ONGC":        "NSE_EQ|INE213A01029",
    "TATAMOTORS":  "NSE_EQ|INE155A01022",
    "TATASTEEL":   "NSE_EQ|INE081A01020",
    "JSWSTEEL":    "NSE_EQ|INE019A01038",
    "ADANIENT":    "NSE_EQ|INE423A01024",
    "ADANIPORTS":  "NSE_EQ|INE742F01042",
    "COALINDIA":   "NSE_EQ|INE522F01014",
    "DIVISLAB":    "NSE_EQ|INE361B01024",
    "DRREDDY":     "NSE_EQ|INE089A01023",
    "EICHERMOT":   "NSE_EQ|INE066A01021",
    "GRASIM":      "NSE_EQ|INE047A01021",
    "HEROMOTOCO":  "NSE_EQ|INE158A01026",
    "HINDALCO":    "NSE_EQ|INE038A01020",
    "INDUSINDBK":  "NSE_EQ|INE095A01012",
    "M&M":         "NSE_EQ|INE101A01026",
    "BAJAJFINSV":  "NSE_EQ|INE918I01026",
    "BAJAJ-AUTO":  "NSE_EQ|INE917I01010",
    "BPCL":        "NSE_EQ|INE029A01011",
    "BRITANNIA":   "NSE_EQ|INE216A01030",
    "CIPLA":       "NSE_EQ|INE059A01026",
    "HDFCLIFE":    "NSE_EQ|INE795G01014",
    "SBILIFE":     "NSE_EQ|INE123W01016",
    "TATACONSUM":  "NSE_EQ|INE192A01025",
    "APOLLOHOSP":  "NSE_EQ|INE437A01024",
    "VEDL":        "NSE_EQ|INE205A01025",
    "ZOMATO":      "NSE_EQ|INE758T01015",
    "TRENT":       "NSE_EQ|INE849A01020",
    "IRCTC":       "NSE_EQ|INE335Y01020",
    "ADANIGREEN":  "NSE_EQ|INE364U01010",
    "SIEMENS":     "NSE_EQ|INE003A01024",
    "ABB":         "NSE_EQ|INE117A01022",
    "HAVELLS":     "NSE_EQ|INE176B01034",
    "LTIM":        "NSE_EQ|INE214T01019",
    "PERSISTENT":  "NSE_EQ|INE262H01021",
    "COFORGE":     "NSE_EQ|INE591G01017",
    "BANKBARODA":  "NSE_EQ|INE028A01039",
    "CANBK":       "NSE_EQ|INE476A01014",
    "PNB":         "NSE_EQ|INE160A01022",
    "CHOLAFIN":    "NSE_EQ|INE121A01024",
    "MUTHOOTFIN":  "NSE_EQ|INE414G01012",
    "DLF":         "NSE_EQ|INE271C01023",
    "GODREJPROP":  "NSE_EQ|INE484J01027",
    "RECLTD":      "NSE_EQ|INE020B01018",
    "PFC":         "NSE_EQ|INE134E01011",
    "GAIL":        "NSE_EQ|INE129A01019",
    "IOC":         "NSE_EQ|INE242A01010",
    "NMDC":        "NSE_EQ|INE584A01023",
    "SAIL":        "NSE_EQ|INE114A01011",
    "TATAPOWER":   "NSE_EQ|INE245A01021",
    "OFSS":        "NSE_EQ|INE881D01027",
    "NAUKRI":      "NSE_EQ|INE663F01024",
    "PAGEIND":     "NSE_EQ|INE761H01022",
    "PIDILITIND":  "NSE_EQ|INE318A01026",
    "MARICO":      "NSE_EQ|INE196A01026",
    "DABUR":       "NSE_EQ|INE016A01026",
    "GODREJCP":    "NSE_EQ|INE102D01028",
    "COLPAL":      "NSE_EQ|INE259A01022",
    "LUPIN":       "NSE_EQ|INE326A01037",
    "AUROPHARMA":  "NSE_EQ|INE406A01037",
    "TORNTPHARM":  "NSE_EQ|INE685A01028",
    "BOSCHLTD":    "NSE_EQ|INE323A01026",
    "CONCOR":      "NSE_EQ|INE111A01025",
    "AMBUJACEM":   "NSE_EQ|INE079A01024",
    "SHREECEM":    "NSE_EQ|INE070A01015",
    "INDIGO":      "NSE_EQ|INE646L01027",
    "JUBLFOOD":    "NSE_EQ|INE797F01020",
    "TVSMOTOR":    "NSE_EQ|INE494B01023",
    "BERGEPAINT":  "NSE_EQ|INE463A01038",
}

# MCX commodity instrument keys for Upstox v2
# These numeric keys EXPIRE every month when contracts roll over.
# We fetch fresh keys dynamically from Upstox instrument master.
# Fallback keys below are approximate — may need update if expired.
MCX_SYMBOL_MAP = {
    "GOLD":        "MCX_FO|427496",
    "GOLDM":       "MCX_FO|427499",
    "SILVER":      "MCX_FO|427512",
    "SILVERM":     "MCX_FO|427515",
    "CRUDEOIL":    "MCX_FO|427450",
    "CRUDEOILM":   "MCX_FO|427453",
    "NATURALGAS":  "MCX_FO|427464",
    "NATGASMINI":  "MCX_FO|427467",
    "COPPER":      "MCX_FO|427480",
    "ZINC":        "MCX_FO|427530",
    "ZINCMINI":    "MCX_FO|427533",
    "ALUMINIUM":   "MCX_FO|427443",
    "LEAD":        "MCX_FO|427473",
    "NICKEL":      "MCX_FO|427476",
}

# Dynamic MCX key cache — fetched from Upstox instrument master
_mcx_keys_cache: dict = {}
_mcx_keys_ts: float = 0.0
_mcx_fail_ts: float = 0.0        # timestamp of last failure
_MCX_FAIL_BACKOFF = 3600         # wait 1 hour after failure before retrying


def _fetch_mcx_keys() -> dict:
    """
    Fetch current MCX near-month contract instrument keys from Upstox.
    Cached for 4 hours. Backs off for 1 hour after any network failure.
    """
    global _mcx_keys_cache, _mcx_keys_ts, _mcx_fail_ts
    now = time.time()

    # Fresh cache — return immediately
    if _mcx_keys_cache and now - _mcx_keys_ts < 14400:
        return _mcx_keys_cache

    # Still in backoff period after a failure — return fallback silently
    if _mcx_fail_ts and now - _mcx_fail_ts < _MCX_FAIL_BACKOFF:
        return MCX_SYMBOL_MAP

    import urllib.request, gzip as _gz
    target_syms = {s.upper() for s in MCX_SYMBOL_MAP}

    try:
        url = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        import json as _json
        data = _json.loads(_gz.decompress(raw).decode("utf-8"))

        from collections import defaultdict
        by_sym: dict = defaultdict(list)
        for inst in data:
            name = (inst.get("tradingsymbol") or inst.get("name") or "").upper()
            base = ''.join(c for c in name if c.isalpha())
            if base in target_syms:
                by_sym[base].append(inst)

        new_map = {}
        for base, insts in by_sym.items():
            insts_sorted = sorted(insts, key=lambda x: x.get("expiry", "9999"))
            nearest = insts_sorted[0]
            ikey = nearest.get("instrument_key") or nearest.get("key")
            if ikey:
                new_map[base] = ikey
                if base == "GOLD":       new_map["GOLDM"]      = ikey
                if base == "SILVER":     new_map["SILVERM"]    = ikey
                if base == "CRUDEOIL":   new_map["CRUDEOILM"]  = ikey
                if base == "NATURALGAS": new_map["NATGASMINI"] = ikey
                if base == "ZINC":       new_map["ZINCMINI"]   = ikey

        if new_map:
            _mcx_keys_cache = new_map
            _mcx_keys_ts    = now
            _mcx_fail_ts    = 0.0   # clear failure timestamp on success
            print(f"[upstox_live] Refreshed MCX keys: {list(new_map.keys())}")
            return new_map

    except Exception as e:
        _mcx_fail_ts = now   # record failure — suppress retries for 1 hour
        print(f"[upstox_live] MCX key refresh failed: {e} — using fallback, retry in 1h")

    return MCX_SYMBOL_MAP

MCX_YF_MAP = {
    "GOLD":       "GC=F",
    "GOLDM":      "GC=F",
    "SILVER":     "SI=F",
    "SILVERM":    "SI=F",
    "CRUDEOIL":   "CL=F",
    "CRUDEOILM":  "CL=F",
    "NATURALGAS": "NG=F",
    "NATGASMINI": "NG=F",
    "COPPER":     "HG=F",
    "ZINC":       "ZNC=F",
    "ZINCMINI":   "ZNC=F",
}

# Approximate conversion factors to INR per MCX unit
# Gold: $/troy oz → ₹/10g  (1 troy oz = 31.1g, so /10g = price * USD_INR * 10 / 31.1)
# Silver: $/troy oz → ₹/kg (1 troy oz = 0.0311 kg, so /kg = price * USD_INR / 0.0311)
# Crude: $/barrel → ₹/barrel (direct * USD_INR)
# NatGas: $/MMBtu → ₹/MMBtu (direct * USD_INR)
# Copper: $/lb → ₹/kg (* USD_INR * 2.20462)

def _get_usd_inr() -> float:
    """Return live USD/INR rate — delegates to fetcher which has the correct rate/range check."""
    from .fetcher import _get_usd_inr as _f_get_usd_inr
    return _f_get_usd_inr()

# Keep module-level alias for backward compatibility
USD_INR = 83.5


def get_access_token():
    """Load Upstox access token from shared token file."""
    try:
        if TOKEN_FILE.exists():
            data = json.loads(TOKEN_FILE.read_text())
            return data.get("access_token")
    except Exception:
        pass
    return os.environ.get("UPSTOX_ACCESS_TOKEN")


# ── Upstox API headers — must look like a real browser to pass Cloudflare ─────
def _upstox_headers(token: str) -> dict:
    return {
        "Authorization":  f"Bearer {token}",
        "Accept":         "application/json, text/plain, */*",
        "Accept-Language":"en-US,en;q=0.9",
        "Accept-Encoding":"gzip, deflate, br",
        "Origin":         "https://pro.upstox.com",
        "Referer":        "https://pro.upstox.com/",
        "User-Agent":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "sec-ch-ua":      '"Chromium";v="124", "Google Chrome";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "Connection":     "keep-alive",
    }


# ── Transport: curl_cffi (real Chrome TLS fingerprint) when available ─────────
# Upstox sits behind Cloudflare, which blocks python-urllib's fingerprint
# (HTTP 403 / error 1010). curl_cffi impersonates Chrome and passes.
# pip install curl-cffi — optional; the urllib fallback below stays.
try:
    from curl_cffi import requests as _creq
    _HAS_CURL_CFFI = True
except Exception:
    _creq = None
    _HAS_CURL_CFFI = False


def _upstox_get(url: str, token: str, timeout: int = 6):
    """Make a GET request to Upstox API with a real-browser fingerprint."""
    headers = {k: v for k, v in _upstox_headers(token).items()
               if k.lower() != "accept-encoding"}
    headers["Accept-Encoding"] = "identity"   # ask for plain JSON, not compressed
    if _HAS_CURL_CFFI:
        try:
            r = _creq.get(url, headers=headers, timeout=timeout, impersonate="chrome124")
        except Exception as e:
            raise Exception(f"network error: {e}")
        if r.status_code == 401:
            raise Exception("401 Unauthorized — token expired, paste today's token")
        if r.status_code >= 400:
            raise Exception(f"HTTP {r.status_code}: {r.text[:400]}")
        raw = r.content
    else:
        import urllib.request
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except Exception as e:
            msg = str(e)
            if "401" in msg or "Unauthorized" in msg:
                raise Exception("401 Unauthorized — token expired, paste today's token")
            raise
    # Defensive decompression in case server ignores our preference
    if raw[:2] == b'\x1f\x8b':
        import gzip as _gzip
        raw = _gzip.decompress(raw)
    elif raw[:2] in (b'\x78\x9c', b'\x78\x01', b'\x78\xda'):
        import zlib
        raw = zlib.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def is_market_hours():
    """Check if NSE equity market is open."""
    from .market_session import is_market_hours as _imh
    return _imh()

def is_commodity_market_hours():
    """Check if MCX commodity market is open (9:00 AM - 11:30 PM IST)."""
    from .market_session import is_commodity_market_hours as _icmh
    return _icmh()


def resolve_instrument_key(symbol: str):
    """Return Upstox instrument key for a symbol."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    if sym in NSE_INDEX_MAP:
        return NSE_INDEX_MAP[sym]
    # MCX: try dynamic keys first, fall back to hardcoded
    if sym in MCX_SYMBOL_MAP:
        dynamic = _fetch_mcx_keys()
        return dynamic.get(sym, MCX_SYMBOL_MAP[sym])
    # NSE equity: use ISIN key (required by Upstox REST LTP endpoint)
    if sym in NSE_ISIN_MAP:
        return NSE_ISIN_MAP[sym]
    # Fallback: symbol format
    return f"NSE_EQ|{sym}"


# ── Bad-key quarantine: Upstox rejects an ENTIRE batch (HTTP 400 UDAPI1087)
# if any single instrument_key is invalid (expired MCX contract, delisted
# ISIN, renamed index...). Bad keys are isolated at runtime and persisted so
# one rotten key can never sink the live feed again. File auto-refreshes
# after 7 days (contracts rotate monthly).
_BADKEY_FILE = TOKEN_FILE.parent / "upstox-badkeys.json"
_BADKEYS = set()
_QLOCK = threading.Lock()


def _load_quarantine():
    global _BADKEYS
    if _BADKEYS:
        return
    try:
        if _BADKEY_FILE.exists():
            import time as _t
            d = json.loads(_BADKEY_FILE.read_text())
            if _t.time() - d.get("updated", 0) < 7 * 86400:
                _BADKEYS = set(d.get("keys", []))
                return
    except Exception:
        pass
    _BADKEYS = set()


def _quarantine_key(key, sym):
    import time as _t
    with _QLOCK:
        if key in _BADKEYS:
            return
        _BADKEYS.add(key)
        try:
            _BADKEY_FILE.write_text(json.dumps({"updated": _t.time(), "keys": sorted(_BADKEYS)}))
        except Exception:
            pass
        print(f"[upstox_live] quarantined invalid instrument key: {key} ({sym})")


def fetch_upstox_quotes(symbols: list) -> dict:
    """Full quotes — price, OHLC, volume, change_pct."""
    import urllib.parse
    token = get_access_token()
    if not token:
        return {}

    key_to_sym = {resolve_instrument_key(s): s for s in symbols}
    if not key_to_sym:
        return {}
    name_to_sym = {s.upper().strip(): s for s in symbols}

    # Drop quarantined keys (auto-isolated earlier as invalid).
    _load_quarantine()
    if _BADKEYS:
        key_to_sym = {k: v for k, v in key_to_sym.items() if k not in _BADKEYS}
    if not key_to_sym:
        return {}

    # Chunked batches: a ~200-symbol URL (~6KB query string) gets rejected
    # by servers/proxies — 50 keys per request stays safely under limits.
    # NOTE: Upstox rejects the WHOLE batch (HTTP 400 UDAPI1087) if even one
    # key is invalid, so _isolate() bisects failures to quarantine the bad
    # key while keeping the good data.
    all_keys = list(key_to_sym)
    batches = [all_keys[i:i + 50] for i in range(0, len(all_keys), 50)]

    def _request(sub):
        keys_param = ",".join(urllib.parse.quote(k, safe='') for k in sub)
        url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={keys_param}"
        raw = _upstox_get(url, token)
        return raw.get("data", {})

    def _isolate(sub):
        """Return (data, bad_syms); bisect 400s to quarantine bad keys."""
        try:
            return _request(sub), []
        except Exception as e:
            if "UDAPI1087" not in str(e):
                print(f"[upstox_live] Quote API error: {e}")
                return {}, []
        keys = list(sub)
        if len(keys) == 1:
            _quarantine_key(keys[0], key_to_sym[keys[0]])
            return {}, [key_to_sym[keys[0]]]
        mid = len(keys) // 2
        good, bad = {}, []
        for half in (keys[:mid], keys[mid:]):
            d, b = _isolate({k: sub[k] for k in half})
            good.update(d)
            bad.extend(b)
        return good, bad

    raw_combined, _bad = {}, []
    if len(batches) == 1:
        raw_combined, _bad = _isolate({k: key_to_sym[k] for k in batches[0]})
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(len(batches), 4)) as pool:
            futs = [pool.submit(_isolate, {k: key_to_sym[k] for k in b}) for b in batches]
            for fut in as_completed(futs):
                try:
                    d, b = fut.result()
                    raw_combined.update(d)
                    _bad.extend(b)
                except Exception as e:
                    print(f"[upstox_live] Quote API error: {e}")
    if _bad:
        print(f"[upstox_live] quarantined invalid keys for: {sorted(set(_bad))}")
    if not raw_combined:
        return {}

    result = {}
    for key_raw, q in raw_combined.items():
        key_norm = key_raw.replace("%7C", "|").replace(":", "|")
        sym = key_to_sym.get(key_norm)
        if not sym:
            suffix = key_norm.split("|")[-1].upper().strip()
            sym = next(
                (s for k, s in key_to_sym.items()
                 if k.split("|")[-1].upper().strip() == suffix), None)
        if not sym:
            # Upstox sometimes answers equities by SYMBOL (NSE_EQ|RELIANCE)
            # instead of the ISIN key requested — match the symbol as well.
            sym = name_to_sym.get(suffix)
        if not sym:
            continue
        ohlc = q.get("ohlc", {})
        ltp  = float(q.get("last_price", 0) or 0)
        cp   = float(ohlc.get("close", 0) or 0)
        chg  = ((ltp - cp) / cp * 100) if cp else 0
        result[sym] = {
            "symbol":     sym,
            "price":      round(ltp, 2),
            "prev_close": round(cp, 2),
            "day_high":   round(float(ohlc.get("high", 0) or 0), 2),
            "day_low":    round(float(ohlc.get("low",  0) or 0), 2),
            "volume":     int(q.get("volume", 0) or 0),
            "change_pct": round(chg, 2),
            "source":     "upstox_live",
        }
    return result


def fetch_upstox_ltp(symbols: list) -> dict:
    """
    Quotes with change_pct for watchlist.
    Uses /market-quote/quotes (full OHLC) instead of /ltp
    because Upstox LTP endpoint returns close_price=null (no change_pct possible).
    Falls back to LTP-only if quotes endpoint fails.
    """
    # Try full quotes first (has prev_close and change_pct)
    try:
        result = fetch_upstox_quotes(symbols)
        if result:
            return result
    except Exception:
        pass

    # Fallback: LTP-only with prev_close from yfinance cache
    import urllib.parse
    token = get_access_token()
    if not token:
        return {}

    result = {}

    def _fetch_batch(batch_syms):
        _load_quarantine()
        batch_syms = [s for s in batch_syms if resolve_instrument_key(s) not in _BADKEYS]
        if not batch_syms:
            return
        key_to_sym = {resolve_instrument_key(s): s for s in batch_syms}
        name_to_sym = {s.upper(): s for s in batch_syms}

        keys_param = ",".join(urllib.parse.quote(k, safe='') for k in key_to_sym)
        url = f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={keys_param}"
        try:
            raw = _upstox_get(url, token, timeout=6)
            for key_raw, q in raw.get("data", {}).items():
                key_norm = key_raw.replace("%7C", "|").replace(":", "|")
                sym = key_to_sym.get(key_norm)
                if not sym:
                    token_val = (q.get("instrument_token") or "").replace(":", "|")
                    sym = key_to_sym.get(token_val)
                if not sym:
                    parts = key_raw.replace("%7C", "|").split(":")
                    if len(parts) == 2:
                        sym = name_to_sym.get(parts[1].upper().strip())
                if not sym:
                    suffix = key_norm.split("|")[-1].upper().strip()
                    sym = next((s for k, s in key_to_sym.items()
                                if k.split("|")[-1].upper().strip() == suffix), None)
                if not sym:
                    continue

                ltp = float(q.get("last_price", 0) or 0)
                if ltp <= 0:
                    continue

                # Get prev_close from yfinance OHLCV cache (already downloaded)
                cp = _get_prev_close_from_cache(sym)
                chg = round((ltp - cp) / cp * 100, 2) if cp else 0

                result[sym] = {
                    "symbol":     sym,
                    "price":      round(ltp, 2),
                    "prev_close": round(cp, 2),
                    "change_pct": chg,
                    "source":     "upstox_ltp",
                }
        except Exception as e:
            print(f"[upstox_live] LTP batch error: {e}")

    nse_syms = [s for s in symbols if s not in MCX_SYMBOL_MAP]
    mcx_syms = [s for s in symbols if s in MCX_SYMBOL_MAP]
    for i in range(0, len(nse_syms), 50):
        _fetch_batch(nse_syms[i:i+50])
    if mcx_syms:
        _fetch_batch(mcx_syms)

    return result


def _get_prev_close_from_cache(symbol: str) -> float:
    """
    Get previous day's close from yfinance OHLCV cache.
    Used to compute change_pct when Upstox LTP returns close_price=null.
    Returns 0 if cache not found.
    """
    import json as _json, math as _math
    from pathlib import Path as _Path
    cache_dir = _Path(__file__).resolve().parent.parent / "cache"
    # Try common cache filenames
    for period in ("3mo", "6mo", "1mo", "1y"):
        f = cache_dir / f"{symbol.upper()}_{period}_1d.json"
        if f.exists():
            try:
                data = _json.loads(f.read_text())
                # Get last valid close (skip today's possibly-NaN bar)
                closes = [d["close"] for d in data
                          if d.get("close") and not _math.isnan(float(d["close"]))
                          and float(d["close"]) > 0]
                if len(closes) >= 2:
                    return float(closes[-1])   # last confirmed close = yesterday's
                elif closes:
                    return float(closes[-1])
            except Exception:
                pass
    return 0.0


def fetch_commodity_yf(symbol: str) -> dict:
    """
    Fetch commodity price via yfinance with correct MCX INR conversion.
    Delegates conversion to fetcher._commodity_usd_to_inr() which has the
    calibrated multipliers (June 11, 2026) — single source of truth.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    from .fetcher import _commodity_usd_to_inr, _get_usd_inr as _fetcher_usd_inr

    yf_ticker = MCX_YF_MAP.get(symbol.upper())
    if not yf_ticker:
        return {}

    try:
        t = yf.Ticker(yf_ticker)
        info = t.fast_info
        usd_price = float(info.last_price or 0)
        if not usd_price:
            return {}

        sym      = symbol.upper()
        usd_inr  = _fetcher_usd_inr()           # correct rate: ~83-86, rejects ~94
        inr      = _commodity_usd_to_inr(sym, usd_price)

        prev_usd = float(info.previous_close or usd_price)
        prev_inr = _commodity_usd_to_inr(sym, prev_usd)
        chg      = ((usd_price - prev_usd) / prev_usd * 100) if prev_usd else 0

        return {
            "symbol":     symbol,
            "price":      round(inr, 2),
            "prev_close": round(prev_inr, 2),
            "day_high":   round(_commodity_usd_to_inr(sym, float(info.day_high or usd_price)), 2),
            "day_low":    round(_commodity_usd_to_inr(sym, float(info.day_low  or usd_price)), 2),
            "change_pct": round(chg, 2),
            "usd_inr":    round(usd_inr, 2),
            "source":     "yfinance_inr",
        }
    except Exception as e:
        print(f"[upstox_live] Commodity yf error {symbol}: {e}")
        return {}


def smart_quote(symbol: str) -> dict:
    """
    Main entry point — returns quote using best available source.
    Equity/Index: Upstox during NSE hours (9:15-15:30), else yfinance
    Commodity:    Upstox during MCX hours (9:00-23:30), else yfinance INR conversion
    """
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    is_commodity = sym in MCX_SYMBOL_MAP

    # Route to correct market session check
    market_live = is_commodity_market_hours() if is_commodity else is_market_hours()

    if market_live:
        quotes = fetch_upstox_ltp([sym])
        if quotes.get(sym) and quotes[sym].get("price"):
            upstox_q = quotes[sym]
            # Upstox LTP often lacks day_high/low/52w/mkt_cap/volume
            # Fill gaps from yfinance (already cached — instant)
            missing = not upstox_q.get("day_high") or not upstox_q.get("52w_high")
            if missing and not is_commodity:
                try:
                    from .fetcher import fetch_quote as _yf
                    yf_q = _yf(sym)
                    merged = {**yf_q, **{k: v for k, v in upstox_q.items() if v}}
                    # Keep Upstox price as authoritative (more real-time)
                    merged["price"]      = upstox_q["price"]
                    merged["change_pct"] = upstox_q.get("change_pct", yf_q.get("change_pct", 0))
                    merged["source"]     = upstox_q.get("source", "upstox_ltp")
                    return merged
                except Exception:
                    pass
            return upstox_q

    # Off-market or Upstox failed
    if is_commodity:
        q = fetch_commodity_yf(sym)
        if q:
            return q
        return {"symbol": sym, "price": 0, "prev_close": 0, "change_pct": 0, "source": "unavailable"}

    # Stock/index via yfinance
    try:
        from .fetcher import fetch_quote as yf_quote
        return yf_quote(sym)
    except Exception:
        return {"symbol": sym, "price": 0}


# ── Batch quote cache (for watchlist — avoid 255 individual calls) ─────────────
_batch_cache: dict = {}  # {cache_key: {result_dict, timestamp}}
_batch_cache_ttl: int = 10  # seconds
_batch_lock = threading.Lock()


def _get_cache_key(symbols: list) -> str:
    """Create a cache key from sorted symbols list."""
    return ",".join(sorted(symbols))


def batch_quote_all(symbols: list) -> dict:
    """
    Batch quote for all watchlist symbols.
    Cached 10s per unique symbol set. Uses Upstox during active market hours.
    Commodity symbols checked against MCX hours separately.
    """
    global _batch_cache

    cache_key = _get_cache_key(symbols)
    now = time.time()

    with _batch_lock:
        # Check if we have a fresh cache for this specific symbol set
        if cache_key in _batch_cache:
            cached = _batch_cache[cache_key]
            if now - cached["timestamp"] < _batch_cache_ttl:
                return cached["result"]

        # Split by session type
        equity_syms  = [s for s in symbols if s not in MCX_SYMBOL_MAP]
        commodity_syms = [s for s in symbols if s in MCX_SYMBOL_MAP]

        result = {}

        # Equity via Upstox if NSE open
        if equity_syms and is_market_hours():
            result.update(fetch_upstox_ltp(equity_syms))

        # Commodity via Upstox if MCX open
        if commodity_syms and is_commodity_market_hours():
            result.update(fetch_upstox_ltp(commodity_syms))

        # Cache the result for this specific symbol set
        _batch_cache[cache_key] = {"result": result, "timestamp": now}

        # Clean up old cache entries (keep last 10 to prevent memory bloat)
        if len(_batch_cache) > 10:
            oldest_keys = sorted(_batch_cache.keys(),
                                key=lambda k: _batch_cache[k]["timestamp"])[:len(_batch_cache) - 10]
            for k in oldest_keys:
                del _batch_cache[k]

        return result
