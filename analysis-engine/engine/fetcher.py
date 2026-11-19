"""
Data fetcher — yfinance for NSE/BSE/global stocks, indices, commodities.
Caches to disk to avoid repeated API calls.
"""
import json, time, os, csv, math, threading
from pathlib import Path

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

try:
    import pandas as pd
    HAS_PD = True
except ImportError:
    HAS_PD = False

# ── Paths ─────────────────────────────────────────────────────────────────────
_THIS_DIR  = Path(__file__).resolve().parent
BASE_DIR   = _THIS_DIR.parent          # .../analysis
CACHE_DIR  = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# ── Symbol → yfinance ticker ──────────────────────────────────────────────────
_INDEX_MAP = {
    "NIFTY":       "^NSEI",
    "NIFTY50":     "^NSEI",
    "BANKNIFTY":   "^NSEBANK",
    "FINNIFTY":    "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY":  "NIFTY_MIDSELECT.NS",
    "NIFTYNXT50":  "^NSMIDCP100",
    "SENSEX":      "^BSESN",
    "BANKEX":      "^BSEBANKEX",
}

# MCX commodities — yfinance global futures (USD prices, converted to INR)
# ZNC=F (LME Zinc), LL=F (Lead), NI=F (Nickel) are delisted on yfinance.
# For dead tickers we derive prices from copper HG=F using LME ratios.
_COMMODITY_MAP = {
    "GOLD":       "GC=F",   # $/troy oz  -> MCX Rs/10g
    "GOLDM":      "GC=F",
    "SILVER":     "SI=F",   # $/troy oz  -> MCX Rs/kg
    "SILVERM":    "SI=F",
    "CRUDEOIL":   "CL=F",   # $/barrel   -> MCX Rs/barrel
    "CRUDEOILM":  "CL=F",
    "NATURALGAS": "NG=F",   # $/MMBtu    -> MCX Rs/MMBtu
    "NATGASMINI": "NG=F",
    "COPPER":     "HG=F",   # $/lb       -> MCX Rs/kg
    "ZINC":       "HG=F",   # ZNC=F dead/stale -> derive from copper ratio 0.2603
    "ZINCMINI":   "HG=F",
    "LEAD":       "HG=F",   # LL=F dead  -> derive from copper ratio 0.1462
    "LEADMINI":   "HG=F",
    "NICKEL":     "HG=F",   # NI=F dead  -> derive from copper ratio 1.3513
    "ALUMINIUM":  "ALI=F",  # $/short ton -> MCX Rs/kg
    "ALUMINI":    "ALI=F",
}

def _to_yf_ticker(symbol: str) -> str:
    """
    Convert bare NSE/MCX/BSE symbol to yfinance ticker.

    Priority:
    1. Explicit override in _SYMBOL_ALIAS_MAP (handles typos, renamed stocks, BSE-only)
    2. Index map → ^NSEI etc.
    3. Commodity map → GC=F etc.
    4. CSV exchange hint → .NS or .BO
    5. Default: .NS
    """
    sym = symbol.upper().replace(".NS", "").replace(".BO", "").strip()

    # 1. Explicit alias (typos, alternate names, BSE-only stocks)
    if sym in _SYMBOL_ALIAS_MAP:
        return _SYMBOL_ALIAS_MAP[sym]

    # 2. Index
    if sym in _INDEX_MAP:
        return _INDEX_MAP[sym]

    # 3. Commodity
    if sym in _COMMODITY_MAP:
        return _COMMODITY_MAP[sym]

    # 4. CSV exchange hint
    exchange = _SYMBOL_EXCHANGE_MAP.get(sym, "NSE").upper()
    if exchange in ("BSE", "BO"):
        return sym + ".BO"

    # 5. Default NSE
    return sym + ".NS"


# ── Symbol alias map — override yfinance ticker for specific symbols ───────────
# Format: "SYMBOL_IN_CSV": "YFINANCE_TICKER"
# Use for: typos in CSV, renamed stocks, BSE-only stocks, delisted+relisted tickers
_SYMBOL_ALIAS_MAP: dict = {
    # Typo corrections
    "ANATRAJ":      "ANANTRAJ.NS",    # typo in CSV — correct symbol is ANANTRAJ

    # Common BSE-only / dual-listed with different codes
    "MTARTECH":     "MTARTECH.NS",    # try NSE; if no data, may be delisted

    # Add your custom mappings here:
    # "YOURSYMBOL":  "YOURSYMBOL.BO",
}

# ── Symbol exchange map — loaded from CSV EXCHANGE column ─────────────────────
# Format: {"SYMBOL": "NSE"} or {"SYMBOL": "BSE"}
_SYMBOL_EXCHANGE_MAP: dict = {}

def _cache_path(symbol: str, period: str) -> Path:
    return CACHE_DIR / f"{symbol}_{period}.json"

def _is_fresh(path: Path, max_age_min: int = 15) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) < max_age_min * 60

# ── Per-symbol write locks (prevents parallel threads writing to wrong files) ─
_cache_locks: dict = {}
_cache_locks_meta = threading.Lock()

def _get_lock(key: str) -> threading.Lock:
    """Get or create a per-cache-key lock."""
    with _cache_locks_meta:
        if key not in _cache_locks:
            _cache_locks[key] = threading.Lock()
        return _cache_locks[key]

# ── CSV loaders ───────────────────────────────────────────────────────────────
def _load_csv_symbols(filename: str, col: str = None) -> list:
    """
    Load symbols from a CSV in sApp/assets/ or analysis/.
    Also reads EXCHANGE column (NSE/BSE) if present and populates _SYMBOL_EXCHANGE_MAP.
    Supports columns: SYMBOL, symbol, Symbol  +  EXCHANGE, exchange, Exchange
    """
    global _SYMBOL_EXCHANGE_MAP
    for base in [BASE_DIR / "assets", BASE_DIR]:
        p = base / filename
        if p.exists():
            try:
                with open(p, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    cols = reader.fieldnames or []

                    # Find symbol column
                    sym_key = col or next(
                        (c for c in cols if c.strip().upper() == "SYMBOL"), None
                    ) or (cols[0] if cols else None)
                    if not sym_key:
                        continue

                    # Find exchange column (optional)
                    exch_key = next(
                        (c for c in cols if c.strip().upper() == "EXCHANGE"), None
                    )

                    symbols = []
                    for r in reader:
                        raw = r.get(sym_key, "").strip().upper()
                        if not raw:
                            continue
                        symbols.append(raw)

                        # Store exchange hint
                        if exch_key:
                            exch = r.get(exch_key, "").strip().upper()
                            if exch in ("BSE", "BO", "NSE", "NS"):
                                _SYMBOL_EXCHANGE_MAP[raw] = "BSE" if exch in ("BSE","BO") else "NSE"

                    return symbols
            except Exception as e:
                print(f"[fetcher] Warning loading {p}: {e}")
    return []

def load_nifty50_from_csv():
    syms = _load_csv_symbols("stocks.csv", "SYMBOL") or _load_csv_symbols("stocks.csv", "symbol")
    return syms or [
        "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC","SBIN",
        "BHARTIARTL","KOTAKBANK","LT","AXISBANK","ASIANPAINT","MARUTI","TITAN",
        "SUNPHARMA","ULTRACEMCO","BAJFINANCE","WIPRO","NESTLEIND","POWERGRID",
        "NTPC","TECHM","HCLTECH","ONGC","TATAMOTORS","TATASTEEL","JSWSTEEL",
        "ADANIENT","ADANIPORTS","COALINDIA","DIVISLAB","DRREDDY","EICHERMOT",
        "GRASIM","HEROMOTOCO","HINDALCO","INDUSINDBK","M&M","BAJAJFINSV",
        "BAJAJ-AUTO","BPCL","BRITANNIA","CIPLA","HDFCLIFE","SBILIFE","TATACONSUM",
        "UPL","VEDL","APOLLOHOSP",
    ]

def load_nifty_next50_from_csv():
    syms = _load_csv_symbols("nifty_next50.csv")
    return syms or [
        "ABB","ADANIGREEN","ADANITRANS","AMBUJACEM","AUROPHARMA","BANDHANBNK",
        "BERGEPAINT","BIOCON","BOSCHLTD","CANBK","CHOLAFIN","COLPAL","CONCOR",
        "DABUR","DLF","GAIL","GODREJCP","GODREJPROP","HAVELLS","ICICIGI",
        "ICICIPRULI","INDIGO","IOC","IRCTC","JUBLFOOD","LICI","LUPIN","MARICO",
        "MCDOWELL-N","MUTHOOTFIN","NAUKRI","NMDC","OFSS","PAGEIND","PIDILITIND",
        "PIIND","PNB","RECLTD","SAIL","SHREECEM","SIEMENS","SRF","TORNTPHARM",
        "TRENT","TVSMOTOR","UBL","VOLTAS","WHIRLPOOL","ZYDUSLIFE","ZOMATO",
    ]

def load_commodities_from_csv():
    syms = _load_csv_symbols("commodities.csv")
    return syms or list(_COMMODITY_MAP.keys())

def load_indices_from_csv():
    syms = _load_csv_symbols("indices.csv")
    return syms or list(_INDEX_MAP.keys())

# ── Symbol lists ──────────────────────────────────────────────────────────────
NIFTY50      = load_nifty50_from_csv()
NIFTY_NEXT50 = load_nifty_next50_from_csv()
COMMODITIES  = load_commodities_from_csv()
INDICES      = load_indices_from_csv()
ALL_SYMBOLS  = list(dict.fromkeys(NIFTY50 + NIFTY_NEXT50 + COMMODITIES + INDICES))

# ── Commodity INR conversion (yfinance returns USD) ───────────────────────────
_usd_inr_cache: dict = {"rate": 83.5, "ts": 0.0}

def _get_usd_inr() -> float:
    """Return live USD/INR rate, cached for 10 minutes."""
    now = time.time()
    if now - _usd_inr_cache["ts"] < 600:
        return _usd_inr_cache["rate"]
    try:
        t = yf.Ticker("USDINR=X")
        rate = float(t.fast_info.last_price or 0)
        if 70 < rate < 115:   # allow wider range — INR can be 83-110
            _usd_inr_cache["rate"] = rate
            _usd_inr_cache["ts"]   = now
            return rate
    except Exception:
        pass
    return _usd_inr_cache["rate"]

def _commodity_usd_to_inr(sym: str, usd: float) -> float:
    """
    Convert yfinance USD futures price to Indian MCX price in INR.

    MCX prices include import duties + GST on top of international prices.
    The duty premiums below are pure percentage premiums (rate-independent).

    Duty premiums (customs + GST + freight):
      Gold/Silver: ~18% premium (12.5% customs + 3% GST + ~2.5% insurance/freight)
      Base metals: ~5-9% premium (5% customs + GST)
      Crude/Gas:   no import duty (formula = usd * INR directly)

    Formulas per commodity:
      GOLD/GOLDM   : GC=F $/troy_oz -> Rs/10g
                     Rs/10g = usd * INR * (10/31.1035) * 1.18
      SILVER/SILVERM: SI=F $/troy_oz -> Rs/kg
                     Rs/kg = usd * INR * 32.1507 * 1.21
      CRUDEOIL     : CL=F $/barrel -> Rs/barrel  (no duty)
      NATURALGAS   : NG=F $/MMBtu -> Rs/MMBtu  (no duty)
      COPPER       : HG=F $/lb -> Rs/kg  (5% duty)
                     Rs/kg = usd * INR * 2.20462 * 1.055
      ZINC/ZINCMINI: derived from copper HG=F using LME Zinc/Copper ratio
      NICKEL       : derived from copper HG=F using LME Nickel/Copper ratio
    """
    import math
    if not usd or usd <= 0 or math.isnan(usd):
        return 0.0
    s = sym.upper()
    r = _get_usd_inr()

    # PRECIOUS METALS — import duty 12.5% + GST 3% + insurance/freight ~2.5%
    if s in ("GOLD", "GOLDM", "GOLDTEN", "GOLDGUINEA", "GOLDPETAL"):
        # GC=F: $/troy oz -> MCX Rs/10g, ~14.7% effective premium
        return round(usd * r * (10.0 / 31.1035) * 1.147, 2)

    if s in ("SILVER", "SILVERM"):
        # SI=F: $/troy oz -> MCX Rs/kg, ~15.9% effective premium
        return round(usd * r * 32.1507 * 1.159, 2)

    # ENERGY — small transportation/handling premium
    if s in ("CRUDEOIL", "CRUDEOILM"):
        # CL=F: $/barrel -> MCX Rs/barrel, ~2% premium
        return round(usd * r * 1.021, 2)

    if s in ("NATURALGAS", "NATGASMINI"):
        # NG=F: $/MMBtu -> MCX Rs/MMBtu, near-parity
        return round(usd * r * 0.993, 2)

    # BASE METALS — ~2.5% effective combined duties
    if s in ("COPPER",):
        # HG=F: $/lb -> Rs/kg, ~2.5% premium
        return round(usd * r * 2.20462 * 1.025, 2)

    if s in ("ZINC", "ZINCMINI"):
        # ZNC=F returns stale/wrong price — derive from copper HG=F using LME Zinc/Copper ratio
        copper_mt = usd * 2204.62
        zinc_usd_per_mt = copper_mt * 0.2603
        return round(zinc_usd_per_mt * r / 1000.0 * 1.082, 2)

    if s in ("LEAD", "LEADMINI"):
        # LL=F dead on yfinance — derive from copper HG=F using LME Lead/Copper ratio 0.1462
        copper_mt = usd * 2204.62
        lead_usd_per_mt = copper_mt * 0.1462
        return round(lead_usd_per_mt * r / 1000.0 * 1.092, 2)

    if s in ("ALUMINIUM", "ALUMINI"):
        # ALI=F: $/short_ton (907.185 kg) -> Rs/kg with 7.0% duty
        return round(usd * r / 907.185 * 1.070, 2)

    if s in ("NICKEL",):
        # NI=F dead — derive from copper HG=F using LME Nickel/Copper ratio 1.232
        copper_mt = usd * 2204.62
        nickel_usd_per_mt = copper_mt * 1.232
        return round(nickel_usd_per_mt * r / 1000.0 * 1.050, 2)

    # Unknown — raw conversion
    return round(usd * r, 2)

# ── OHLCV fetcher ─────────────────────────────────────────────────────────────
def fetch_ohlcv(symbol: str, period: str = "6mo", interval: str = "1d",
                force: bool = False) -> list:
    """
    Returns list of {date, open, high, low, close, volume}.
    Works for NSE stocks, indices (NIFTY50, BANKNIFTY), and MCX commodities.
    Commodities: prices converted to INR.
    Caches for 15 min.
    """
    if not HAS_YF or not HAS_PD:
        return []

    sym  = symbol.upper().replace(".NS","").replace(".BO","")
    is_commodity = sym in _COMMODITY_MAP

    # For commodity with long period, cap at 2y (futures don't have full history)
    if is_commodity and period in ("5y", "max"):
        period = "2y"

    cache_key = f"{sym}_{period}_{interval}"
    cache     = _cache_path(sym, f"{period}_{interval}")

    # Fast path: return fresh cache without acquiring lock
    if not force and _is_fresh(cache):
        try:
            with open(cache) as f:
                return json.load(f)
        except Exception:
            pass

    ticker = _to_yf_ticker(sym)

    # ── Global yfinance download lock ─────────────────────────────────────────
    # yfinance 1.2+ is NOT thread-safe — concurrent yf.download() calls
    # corrupt each other's DataFrames. We serialise ALL downloads through one
    # global lock. Cache reads above are still fully parallel and fast.
    with _get_lock("__yfinance_global__"):
        # Re-check cache inside lock (another thread may have just downloaded)
        if not force and _is_fresh(cache):
            try:
                with open(cache) as f:
                    return json.load(f)
            except Exception:
                pass

        # Try the primary ticker; if no data and it's .NS, also try .BO
        tickers_to_try = [ticker]
        if ticker.endswith(".NS") and sym not in _COMMODITY_MAP and sym not in _INDEX_MAP:
            tickers_to_try.append(sym + ".BO")

        records = []
        used_ticker = ticker

        for try_ticker in tickers_to_try:
            try:
                df = yf.download(try_ticker, period=period, interval=interval,
                                 progress=False, auto_adjust=True)
                if df.empty:
                    print(f"[fetcher] No data for {sym} ({try_ticker})")
                    continue

                # ── yfinance 1.2+ returns MultiIndex(PriceType, Ticker)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                df.columns = [str(c).title() for c in df.columns]

                if 'Close' not in df.columns:
                    print(f"[fetcher] No Close column for {sym}: {list(df.columns)}")
                    continue

                df.index = df.index.astype(str)
                for date_idx in df.index:
                    date_str = str(date_idx)[:10]
                    row = df.loc[date_idx]

                    def _sf(val) -> float:
                        try:
                            f = float(val)
                            return 0.0 if (math.isnan(f) or math.isinf(f)) else f
                        except (TypeError, ValueError):
                            return 0.0

                    close_raw = _sf(row.get('Close', 0))
                    open_raw  = _sf(row.get('Open',  0))
                    high_raw  = _sf(row.get('High',  0))
                    low_raw   = _sf(row.get('Low',   0))
                    vol_raw   = _sf(row.get('Volume', 0))

                    if close_raw == 0 and open_raw == 0:
                        continue

                    close = _commodity_usd_to_inr(sym, close_raw) if is_commodity else close_raw
                    open_ = _commodity_usd_to_inr(sym, open_raw)  if is_commodity else open_raw
                    high_ = _commodity_usd_to_inr(sym, high_raw)  if is_commodity else high_raw
                    low_  = _commodity_usd_to_inr(sym, low_raw)   if is_commodity else low_raw

                    records.append({
                        "date":   date_str,
                        "open":   round(open_,  2),
                        "high":   round(high_,  2),
                        "low":    round(low_,   2),
                        "close":  round(close,  2),
                        "volume": int(vol_raw),
                    })

                if records:
                    used_ticker = try_ticker
                    # If we found data on .BO, remember it for future calls
                    if try_ticker.endswith(".BO") and try_ticker != ticker:
                        _SYMBOL_ALIAS_MAP[sym] = try_ticker
                        print(f"[fetcher] Auto-detected {sym} on BSE ({try_ticker})")
                    break  # success — stop trying

            except Exception as e:
                print(f"[fetcher] {sym} ({try_ticker}): {e}")
                continue

        if records:
            tmp = str(cache) + ".tmp"
            with open(tmp, "w") as fw:
                json.dump(records, fw)
            os.replace(tmp, str(cache))

        return records

# ── Quote fetcher ─────────────────────────────────────────────────────────────
def fetch_quote(symbol: str) -> dict:
    """Latest price. Works for stocks (NSE+BSE), indices, commodities.
    Auto-detects BSE if NSE ticker returns no data."""
    if not HAS_YF:
        return {"symbol": symbol, "price": 0}

    sym  = symbol.upper().replace(".NS","").replace(".BO","")
    is_commodity = sym in _COMMODITY_MAP
    ticker = _to_yf_ticker(sym)

    # Tickers to try in order
    tickers_to_try = [ticker]
    if ticker.endswith(".NS") and not is_commodity and sym not in _INDEX_MAP:
        tickers_to_try.append(sym + ".BO")

    for try_ticker in tickers_to_try:
        try:
            t    = yf.Ticker(try_ticker)
            info = t.fast_info
            raw_price = float(info.last_price or info.previous_close or 0)
            if not raw_price:
                continue   # try next ticker

            raw_prev  = float(info.previous_close or raw_price)
            price = _commodity_usd_to_inr(sym, raw_price) if is_commodity else raw_price
            prev  = _commodity_usd_to_inr(sym, raw_prev)  if is_commodity else raw_prev

            # If found on .BO but .NS was default, remember for future calls
            if try_ticker.endswith(".BO") and try_ticker != ticker:
                _SYMBOL_ALIAS_MAP[sym] = try_ticker
                print(f"[fetcher] Auto-detected {sym} quote on BSE ({try_ticker})")

            return {
                "symbol":     sym,
                "price":      round(price, 2),
                "prev_close": round(prev, 2),
                "day_high":   round(_commodity_usd_to_inr(sym, float(info.day_high or raw_price))  if is_commodity else float(info.day_high  or price), 2),
                "day_low":    round(_commodity_usd_to_inr(sym, float(info.day_low  or raw_price))  if is_commodity else float(info.day_low   or price), 2),
                "volume":     int(info.three_month_average_volume or 0),
                "market_cap": int(info.market_cap or 0),
                "52w_high":   round(_commodity_usd_to_inr(sym, float(info.year_high or 0)) if is_commodity else float(info.year_high or 0), 2),
                "52w_low":    round(_commodity_usd_to_inr(sym, float(info.year_low  or 0)) if is_commodity else float(info.year_low  or 0), 2),
                "source":     "yfinance",
                "exchange":   "BSE" if try_ticker.endswith(".BO") else "NSE",
            }
        except Exception as e:
            continue   # try next ticker

    return {"symbol": sym, "error": "No data from NSE or BSE", "price": 0}

# ── Fundamentals ──────────────────────────────────────────────────────────────
def fetch_fundamentals(symbol: str) -> dict:
    """P/E, P/B, ROE, EPS etc. Indices/commodities return minimal data."""
    if not HAS_YF:
        return {}

    sym    = symbol.upper().replace(".NS","").replace(".BO","")
    is_idx = sym in _INDEX_MAP
    is_com = sym in _COMMODITY_MAP
    ticker = _to_yf_ticker(sym)

    if is_idx:
        return {"name": sym, "sector": "Index", "description": f"{sym} — NSE/BSE Index"}
    if is_com:
        return {"name": sym, "sector": "Commodity", "description": f"{sym} — MCX Commodity"}

    try:
        info = yf.Ticker(ticker).info
        return {
            "pe":           info.get("trailingPE"),
            "pb":           info.get("priceToBook"),
            "roe":          info.get("returnOnEquity"),
            "eps":          info.get("trailingEps"),
            "debt_equity":  info.get("debtToEquity"),
            "revenue":      info.get("totalRevenue"),
            "profit_margin":info.get("profitMargins"),
            "dividend":     info.get("dividendYield"),
            "sector":       info.get("sector", ""),
            "industry":     info.get("industry", ""),
            "name":         info.get("longName", sym),
            "description":  (info.get("longBusinessSummary") or "")[:400],
        }
    except Exception as e:
        return {"error": str(e)}

# ── Live-speed TTL cache ──────────────────────────────────────────────────────
# yf.Ticker().fast_info costs 1-2 HTTP calls per symbol and the dashboard
# re-polls every few seconds. Cache quotes briefly so the UI stays instant.
import time as _qtime
_QUOTE_CACHE = {}
_QUOTE_TTL = 20
_fetch_quote_raw = fetch_quote
def fetch_quote(symbol: str) -> dict:
    key = (symbol or "").upper()
    now = _qtime.time()
    hit = _QUOTE_CACHE.get(key)
    if hit and now - hit[0] < _QUOTE_TTL and hit[1].get("price"):
        return hit[1]
    q = _fetch_quote_raw(symbol)
    if q and q.get("price"):
        _QUOTE_CACHE[key] = (now, q)
        if len(_QUOTE_CACHE) > 600:
            _QUOTE_CACHE.pop(next(iter(_QUOTE_CACHE)))
    return q
