"""
Stock Analysis API Server — Flask on port 5001
"""
import sys, os, json, time, queue
from pathlib import Path as _ServerPath
sys.path.insert(0, os.path.dirname(__file__))

# ── App-local data directory (self-contained, no root dependency) ─────────────
_DATA_DIR = _ServerPath(__file__).resolve().parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False

from engine.fetcher import fetch_ohlcv, fetch_quote, fetch_fundamentals
from engine.indicators import compute_all
from engine.signals import analyze
from engine.screener import run_screen, PRESET_SCREENS
from engine.market_session import is_market_hours, get_market_status
from engine.advisor import advise
from engine.alerts import (
    add_alert, get_alerts, delete_alert, toggle_alert,
    reset_alert, get_alert_history, check_alerts
)
# ── Shoonya integration ───────────────────────────────────────────────────────
try:
    from engine.shoonya_live import (
        is_enabled as shoonya_is_enabled,
        batch_quote as shoonya_batch,
        fetch_intraday as shoonya_intraday,
        get_status as shoonya_status,
        login as shoonya_login,
        save_config as shoonya_save_config,
        get_config as shoonya_get_config,
    )
    HAS_SHOONYA = True
except Exception as _e:
    print(f"[server] shoonya_live not available: {_e}")
    HAS_SHOONYA = False

from engine.market_intel import get_market_intelligence

# Upstox live module (graceful fallback if unavailable)
try:
    from engine.upstox_live import (
        smart_quote, batch_quote_all, fetch_upstox_quotes,
        NSE_INDEX_MAP, MCX_SYMBOL_MAP, is_market_hours as upstox_market_hours
    )
    HAS_UPSTOX_LIVE = True
except Exception as _e:
    print(f"[server] upstox_live not available: {_e}")
    HAS_UPSTOX_LIVE = False

# Upstox WebSocket background feed
try:
    import engine.upstox_ws as upstox_ws
    HAS_UPSTOX_WS = True
except Exception as _e:
    print(f"[server] upstox_ws not available: {_e}")
    HAS_UPSTOX_WS = False

app = Flask(__name__, static_folder="ui", static_url_path="")
CORS(app)
UI_DIR = os.path.join(os.path.dirname(__file__), "ui")

# ── Custom JSON encoder — converts NaN/Infinity to null (valid JSON) ──────────
import math as _math

class _SafeJSONProvider(app.json_provider_class):
    def dumps(self, obj, **kwargs):
        import json as _json
        class _NaNEncoder(_json.JSONEncoder):
            def iterencode(self, o, _one_shot=False):
                # Walk the output and replace NaN/Infinity tokens with null
                chunks = super().iterencode(o, _one_shot)
                for chunk in chunks:
                    chunk = chunk.replace('NaN', 'null').replace('Infinity', 'null').replace('-Infinity', 'null')
                    yield chunk
        return _json.dumps(obj, cls=_NaNEncoder, **kwargs)

    def loads(self, s, **kwargs):
        import json as _json
        return _json.loads(s, **kwargs)

app.json_provider_class = _SafeJSONProvider
app.json = _SafeJSONProvider(app)

# ── Symbol helpers ────────────────────────────────────────────────────────────
def get_all_symbols():
    try:
        import importlib, engine.fetcher
        importlib.reload(engine.fetcher)
        return engine.fetcher.ALL_SYMBOLS
    except Exception as e:
        print(f"[server] Error reloading symbols: {e}")
        from engine.fetcher import NIFTY50, NIFTY_NEXT50
        return list(dict.fromkeys(NIFTY50 + NIFTY_NEXT50))

def get_nifty50_symbols():
    try:
        import importlib, engine.fetcher
        importlib.reload(engine.fetcher)
        return engine.fetcher.NIFTY50
    except Exception:
        from engine.fetcher import NIFTY50
        return NIFTY50

# ── Static UI ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(UI_DIR, "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(UI_DIR, filename)

# ── Symbols ───────────────────────────────────────────────────────────────────
@app.route("/api/symbols")
def api_symbols():
    return jsonify(get_all_symbols())

@app.route("/api/nifty50")
def api_nifty50():
    return jsonify(get_nifty50_symbols())

@app.route("/api/reload")
def api_reload():
    try:
        import importlib, engine.fetcher
        importlib.reload(engine.fetcher)
        return jsonify({"success": True, "symbols_count": len(engine.fetcher.ALL_SYMBOLS)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/symbols/validate/<symbol>")
def api_validate_symbol(symbol):
    """
    Check if a symbol is valid on NSE or BSE.
    Returns {symbol, valid, exchange, ticker, price, name}
    Useful before adding to stocks.csv.
    """
    sym = symbol.upper().strip()
    tickers = [sym + ".NS", sym + ".BO"]

    for ticker in tickers:
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            p = float(t.fast_info.last_price or 0)
            if p > 0:
                name = ""
                try: name = t.info.get("longName","") or t.info.get("shortName","")
                except Exception: pass
                exchange = "BSE" if ticker.endswith(".BO") else "NSE"
                return jsonify({
                    "symbol":   sym,
                    "valid":    True,
                    "exchange": exchange,
                    "ticker":   ticker,
                    "price":    round(p, 2),
                    "name":     name,
                    "suggestion": f"Add '{sym}' to stocks.csv" + (f" with EXCHANGE=BSE" if exchange == "BSE" else ""),
                })
        except Exception:
            continue

    # Check if it's an alias
    from engine.fetcher import _SYMBOL_ALIAS_MAP
    if sym in _SYMBOL_ALIAS_MAP:
        mapped = _SYMBOL_ALIAS_MAP[sym]
        return jsonify({
            "symbol":   sym,
            "valid":    False,
            "alias_of": mapped,
            "suggestion": f"'{sym}' is mapped to '{mapped}' in alias map. Use '{mapped.replace('.NS','').replace('.BO','')}' in CSV instead.",
        })

    return jsonify({
        "symbol":     sym,
        "valid":      False,
        "suggestion": f"'{sym}' not found on NSE or BSE. Check the correct symbol at nseindia.com or bseindia.com",
    })


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    """Clear all yfinance OHLCV cache files to force fresh downloads."""
    import glob as _glob
    cache_dir = os.path.join(os.path.dirname(__file__), "cache")
    files = _glob.glob(os.path.join(cache_dir, "*.json"))
    deleted = 0
    errors  = 0
    for f in files:
        try:
            os.remove(f)
            deleted += 1
        except Exception:
            errors += 1
    return jsonify({"success": True, "files_deleted": deleted, "errors": errors})


# ── Market status ─────────────────────────────────────────────────────────────
@app.route("/api/market-status")
def api_market_status():
    from engine.market_session import (
        is_commodity_market_hours, get_full_status,
        is_mcx_morning_session, is_mcx_evening_session
    )
    status = get_full_status()
    return jsonify({
        "is_open":       status["nse_open"],
        "status":        status["nse_status"],
        "mcx_open":      status["mcx_open"],
        "mcx_morning":   is_mcx_morning_session(),
        "mcx_evening":   is_mcx_evening_session(),
        "symbols_count": len(get_all_symbols()),
        "upstox_live":   HAS_UPSTOX_LIVE,
        "datetime_ist":  status["datetime_ist"],
        "weekday":       status["weekday"],
    })

# ── Quote — smart: Upstox live during market, yfinance off-market ─────────────
@app.route("/api/quote/<symbol>")
def api_quote(symbol):
    sym = symbol.upper()
    # Commodities always need MCX conversion — never raw yfinance
    if HAS_UPSTOX_LIVE:
        from engine.upstox_live import MCX_SYMBOL_MAP, fetch_commodity_yf, is_commodity_market_hours
        if sym in MCX_SYMBOL_MAP:
            if is_commodity_market_hours():
                q = smart_quote(sym)
                if q and q.get("price"):
                    return jsonify(q)
            # Off-hours: still use MCX-converted yfinance price
            q = fetch_commodity_yf(sym)
            if q and q.get("price"):
                return jsonify(q)
    # Equity/Index
    if HAS_UPSTOX_LIVE and is_market_hours():
        q = smart_quote(sym)
        if q and q.get("price"):
            return jsonify(q)
    return jsonify(fetch_quote(sym))

# ── Batch LTP — for watchlist (all symbols in one shot) ──────────────────────
_LTP_YF_CACHE = {}  # {symbol: (ts, quote)} — 30s TTL on slow yfinance fallback
_LTP_YF_TTL = 15
@app.route("/api/ltp", methods=["POST"])
def api_ltp_batch():
    """
    POST {symbols: [...]}
    Returns {SYMBOL: {price, prev_close, change_pct, source}, ...}
    Priority: WS tick cache → Shoonya live → Upstox REST → yfinance fallback
    """
    body    = request.get_json(force=True) or {}
    symbols = [s.upper() for s in body.get("symbols", [])]
    if not symbols:
        return jsonify({}), 400

    result = {}

    # ── 1. WS tick cache (instant, sub-second fresh) ──────────────────────────
    if HAS_UPSTOX_WS:
        try:
            latest = upstox_ws.get_latest()
            for s in symbols:
                if s in latest and latest[s].get("price"):
                    result[s] = latest[s]
        except Exception:
            pass

    # ── 2. Shoonya live for anything not in WS cache ──────────────────────────
    if HAS_SHOONYA and shoonya_is_enabled() and is_market_hours():
        missing_sh = [s for s in symbols if s not in result or not result[s].get("price")]
        if missing_sh:
            try:
                sh_result = shoonya_batch(missing_sh)
                result.update(sh_result)
            except Exception:
                pass

    # ── 3. Upstox REST for anything still missing ─────────────────────────────
    missing = [s for s in symbols if s not in result or not result[s].get("price")]
    if missing and HAS_UPSTOX_LIVE:
        from engine.upstox_live import is_commodity_market_hours, MCX_SYMBOL_MAP
        in_nse = is_market_hours()
        in_mcx = is_commodity_market_hours()
        to_fetch = [s for s in missing if
                    (s not in MCX_SYMBOL_MAP and in_nse) or
                    (s in MCX_SYMBOL_MAP and in_mcx)]
        if to_fetch:
            try:
                # LTP helper: full quotes first, then LTP-only for symbols
                # Upstox answers by symbol instead of ISIN (silent drops).
                from engine.upstox_live import fetch_upstox_ltp
                upstox_result = fetch_upstox_ltp(to_fetch)
                result.update(upstox_result)
            except Exception:
                pass

    # ── 4. yfinance fallback for anything still missing ───────────────────────
    # (TTL-cached: dashboard re-polls every few seconds; yfinance is slow)
    missing = [s for s in symbols if s not in result or not result[s].get("price")]
    if missing:
        _now = time.time()
        _still = []
        for s in missing:
            _hit = _LTP_YF_CACHE.get(s)
            if _hit and _now - _hit[0] < _LTP_YF_TTL and _hit[1].get("price"):
                result[s] = _hit[1]
            else:
                _still.append(s)
        missing = _still
    if missing:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        try:
            from engine.upstox_live import MCX_SYMBOL_MAP
        except Exception:
            MCX_SYMBOL_MAP = {}

        def _yf_quote(sym):
            try:
                if sym in MCX_SYMBOL_MAP and HAS_UPSTOX_LIVE:
                    from engine.upstox_live import fetch_commodity_yf
                    q = fetch_commodity_yf(sym)
                    if q and q.get("price"):
                        return sym, q
                q = fetch_quote(sym)
                p = q.get("price") or 0
                if not p:
                    return sym, None
                prev = q.get("prev_close") or p
                chg  = round((p - prev) / max(prev, 0.01) * 100, 2) if prev else 0
                return sym, {"symbol": sym, "price": p, "prev_close": prev,
                             "change_pct": chg, "source": "yfinance"}
            except Exception:
                return sym, None

        with ThreadPoolExecutor(max_workers=min(len(missing), 20)) as pool:
            futs = {pool.submit(_yf_quote, s): s for s in missing}
            for fut in as_completed(futs):
                sym, data = fut.result()
                if data:
                    result[sym] = data
                    _LTP_YF_CACHE[sym] = (time.time(), data)

    return jsonify(result)

# ── OHLCV ─────────────────────────────────────────────────────────────────────
@app.route("/api/ohlcv/<symbol>")
def api_ohlcv(symbol):
    period   = request.args.get("period", "6mo")
    interval = request.args.get("interval", "1d")  # always default to daily
    data = fetch_ohlcv(symbol.upper(), period=period, interval=interval)
    return jsonify(data)

@app.route("/api/candle/latest/<symbol>")
def api_candle_latest(symbol):
    """Return just the latest candle + current price for live chart update."""
    sym = symbol.upper()
    quote = {}
    if HAS_UPSTOX_LIVE and is_market_hours():
        from engine.upstox_live import smart_quote
        quote = smart_quote(sym)
    if not quote.get("price"):
        quote = fetch_quote(sym)
    return jsonify({
        "symbol":    sym,
        "price":     quote.get("price", 0),
        "day_high":  quote.get("day_high", 0),
        "day_low":   quote.get("day_low", 0),
        "prev_close":quote.get("prev_close", 0),
        "change_pct":quote.get("change_pct", 0),
        "is_market": is_market_hours(),
    })

# ── Indicators ────────────────────────────────────────────────────────────────
@app.route("/api/indicators/<symbol>")
def api_indicators(symbol):
    period = request.args.get("period", "6mo")
    data = fetch_ohlcv(symbol.upper(), period=period)
    if not data:
        return jsonify({"error": "No data"}), 404
    # ── Warmup seeding ─────────────────────────────────────────────────────
    # Short windows (1mo ≈ 22 bars) cannot seed EMA50/100/200, BB20 or RSI14 —
    # the lines then start mid-chart ("less than half"). So we compute on a
    # longer history and slice the seeded values back to the visible window.
    MIN_SEED = 260
    full = data
    if len(data) < MIN_SEED and period not in ("2y", "5y", "max"):
        try:
            seed = fetch_ohlcv(symbol.upper(), period="2y")
            if seed and len(seed) > len(data):
                seen = {d["date"] for d in data}
                prefix = [d for d in seed if d["date"] not in seen]
                if prefix:
                    merged = prefix + data
                    merged.sort(key=lambda d: d["date"])
                    full = merged[-600:]
        except Exception:
            full = data
    ind = compute_all(full)
    # Slice seeded indicators back to the visible window
    n_vis = len(data)
    offset = len(full) - n_vis
    if offset < 0:
        offset = 0
    result = {}
    for k, v in ind.items():
        if isinstance(v, tuple):
            result[k] = [list(x[offset:]) if isinstance(x, list) else x for x in v]
        elif isinstance(v, list):
            result[k] = v[offset:]
        else:
            result[k] = v
    # VWAP is anchored to the visible window (not to warmup history)
    try:
        from engine.indicators import vwap as _vwap_fn
        result["vwap"] = _vwap_fn(data)
    except Exception:
        pass
    result["dates"] = [d["date"] for d in data]
    result["ohlcv"]  = data
    return jsonify(result)

# ── Signals ───────────────────────────────────────────────────────────────────
@app.route("/api/signals/<symbol>")
def api_signals(symbol):
    period = request.args.get("period", "1y")
    data = fetch_ohlcv(symbol.upper(), period=period)
    if not data:
        return jsonify({"error": "No data"}), 404
    return jsonify(analyze(data, symbol.upper()))

# ── Fundamentals ──────────────────────────────────────────────────────────────
@app.route("/api/fundamentals/<symbol>")
def api_fundamentals(symbol):
    return jsonify(fetch_fundamentals(symbol.upper()))

# ── Corporate actions ─────────────────────────────────────────────────────────
@app.route("/api/corporate/<symbol>")
def api_corporate(symbol):
    from engine.corporate import get_corporate_actions
    try:
        return jsonify(get_corporate_actions(symbol.upper()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Presets ───────────────────────────────────────────────────────────────────
@app.route("/api/presets")
def api_presets():
    return jsonify(PRESET_SCREENS)

# ── Daily Briefing ────────────────────────────────────────────────────────────
def _inject_live_price(data: list, symbols: list) -> dict:
    """
    Fetch live prices from Upstox and patch the last bar of each symbol's data.
    Returns {SYMBOL: live_price} dict.
    """
    live = {}
    if not HAS_UPSTOX_LIVE or not is_market_hours():
        return live
    try:
        from engine.upstox_live import batch_quote_all
        quotes = batch_quote_all(symbols)
        for sym, q in quotes.items():
            p = q.get("price", 0)
            if p and p > 0:
                live[sym] = p
    except Exception as e:
        print(f"[briefing] live price inject error: {e}")
    return live

@app.route("/api/briefing/<symbol>")
def api_briefing(symbol):
    """Generate a trader-style daily briefing for a symbol."""
    from engine.briefing import generate_briefing
    sym    = symbol.upper()
    period = request.args.get("period", "3mo")
    data   = fetch_ohlcv(sym, period=period)
    live = _inject_live_price(data, [sym])
    if live.get(sym) and data:
        data[-1]["close"] = live[sym]
    result = generate_briefing(sym, data)
    return jsonify(result)

@app.route("/api/briefing/batch", methods=["POST"])
def api_briefing_batch():
    """Generate briefings for multiple symbols."""
    from engine.briefing import generate_batch
    from engine.fetcher  import fetch_ohlcv as _fetch
    body    = request.get_json(force=True) or {}
    symbols = body.get("symbols", [])
    if not symbols:
        symbols = ["NIFTY50","BANKNIFTY","GOLD","SILVER","CRUDEOIL"]
    symbols = [s.upper() for s in symbols[:20]]

    # Fetch all OHLCV first, patch live prices, then run briefings
    import concurrent.futures
    data_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_fetch, sym, "3mo"): sym for sym in symbols}
        for fut in concurrent.futures.as_completed(futs):
            sym = futs[fut]
            try:
                data_map[sym] = fut.result() or []
            except Exception:
                data_map[sym] = []

    # Patch live prices in one batch call
    live = _inject_live_price([], symbols)
    for sym, price in live.items():
        if data_map.get(sym):
            data_map[sym][-1]["close"] = price

    # Generate briefings with patched data
    from engine.briefing import generate_briefing
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(generate_briefing, sym, data_map.get(sym)): sym
                for sym in symbols}
        for fut in concurrent.futures.as_completed(futs):
            sym = futs[fut]
            try:
                r = fut.result()
                results.append(r)
            except Exception as e:
                results.append({"symbol": sym, "error": str(e)})

    results.sort(key=lambda x: x.get("symbol",""))
    return jsonify({"count": len(results), "results": results})

# ── Trade Tips ────────────────────────────────────────────────────────────────
@app.route("/api/tips/<symbol>")
def api_tips_single(symbol):
    """Generate all tip types (Intraday/BTST/STBT/Positional/Options) for one symbol."""
    from engine.tips import generate_tips
    sym  = symbol.upper()
    data = fetch_ohlcv(sym, period="3mo")
    live = _inject_live_price(data, [sym])
    lp   = live.get(sym)
    if lp and data:
        data[-1]["close"] = lp
    return jsonify(generate_tips(sym, data, live_price=lp))

@app.route("/api/tips/batch", methods=["POST"])
def api_tips_batch():
    """Generate tips for multiple symbols in parallel. Live prices injected."""
    from engine.tips import generate_tips
    import concurrent.futures
    from engine.fetcher import fetch_ohlcv as _fetch

    body    = request.get_json(force=True) or {}
    symbols = [s.upper() for s in body.get("symbols", [])[:25]]
    if not symbols:
        symbols = ["NIFTY50","BANKNIFTY","RELIANCE","TCS","GOLD","CRUDEOIL"]

    # Fetch OHLCV in parallel
    data_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_fetch, s, "3mo"): s for s in symbols}
        for fut in concurrent.futures.as_completed(futs):
            s = futs[fut]
            try: data_map[s] = fut.result() or []
            except: data_map[s] = []

    # Inject live prices
    live = _inject_live_price([], symbols)
    for s, p in live.items():
        if data_map.get(s): data_map[s][-1]["close"] = p

    # Generate tips in parallel
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(generate_tips, s, data_map.get(s), live.get(s)): s
                for s in symbols}
        for fut in concurrent.futures.as_completed(futs):
            s = futs[fut]
            try: results.append(fut.result())
            except Exception as e: results.append({"symbol": s, "error": str(e)})

    results.sort(key=lambda x: x.get("symbol",""))
    return jsonify({"count": len(results), "results": results, "generated": __import__("datetime").datetime.now().strftime("%d %b %Y %H:%M")})
@app.route("/api/upstox/status")
def api_upstox_status():
    from pathlib import Path
    import json
    token_file = _DATA_DIR / "upstox-token.json"
    config_file = _DATA_DIR / "upstox-config.json"
    has_token = False
    client_id = ""
    source = ""
    try:
        if token_file.exists():
            d = json.loads(token_file.read_text())
            has_token = bool(d.get("access_token"))
            source = "token file"
    except Exception: pass
    try:
        if config_file.exists():
            c = json.loads(config_file.read_text())
            client_id = c.get("clientId","")
    except Exception: pass
    return jsonify({"has_token":has_token,"client_id":client_id,"source":source,"upstox_live":HAS_UPSTOX_LIVE})

@app.route("/api/upstox/credentials", methods=["POST"])
def api_upstox_credentials():
    from pathlib import Path
    import json
    body = request.get_json(force=True) or {}
    cid = body.get("client_id","").strip()
    sec = body.get("client_secret","").strip()
    if not cid:
        return jsonify({"success":False,"error":"client_id required"}), 400
    config_file = _DATA_DIR / "upstox-config.json"
    try:
        cfg = {}
        if config_file.exists():
            cfg = json.loads(config_file.read_text())
        cfg["clientId"] = cid
        if sec: cfg["clientSecret"] = sec
        cfg.setdefault("redirectUri","http://localhost:8765/callback")
        config_file.write_text(json.dumps(cfg, indent=2))
        return jsonify({"success":True})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}), 500

@app.route("/api/upstox/token", methods=["POST","DELETE"])
def api_upstox_token():
    from pathlib import Path
    import json
    token_file = _DATA_DIR / "upstox-token.json"
    if request.method == "DELETE":
        try:
            if token_file.exists(): token_file.unlink()
            return jsonify({"success":True})
        except Exception as e:
            return jsonify({"success":False,"error":str(e)}), 500
    body = request.get_json(force=True) or {}
    tok = body.get("access_token","").strip()
    if not tok:
        return jsonify({"success":False,"error":"access_token required"}), 400
    try:
        import time
        token_file.write_text(json.dumps({"access_token":tok,"savedAt":int(time.time()*1000)},indent=2))
        # Invalidate upstox_live token cache
        if HAS_UPSTOX_LIVE:
            from engine import upstox_live
            upstox_live._batch_cache = {}
            upstox_live._batch_cache_ts = 0
        return jsonify({"success":True})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}), 500

@app.route("/api/upstox/test")
def api_upstox_test():
    if not HAS_UPSTOX_LIVE:
        return jsonify({"success":False,"error":"upstox_live module not loaded"})
    try:
        from engine.upstox_live import fetch_upstox_ltp
        result = fetch_upstox_ltp(["NIFTY50"])
        if result.get("NIFTY50"):
            q = result["NIFTY50"]
            return jsonify({"success":True,"price":q.get("price"),"source":q.get("source")})
        return jsonify({"success":False,"error":"No data returned — token may be expired or invalid"})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)})


@app.route("/api/screen", methods=["POST"])
def api_screen():
    body    = request.get_json(force=True) or {}
    preset  = body.get("preset")
    filters = body.get("filters", [])
    symbols = body.get("symbols", get_all_symbols()[:50])
    if preset and preset in PRESET_SCREENS:
        filters = PRESET_SCREENS[preset]["filters"]
    if not filters:
        return jsonify({"error": "No filters provided"}), 400
    results = run_screen(symbols, filters)
    return jsonify({"count": len(results), "results": results})

# ── Screener (streaming SSE) ──────────────────────────────────────────────────
@app.route("/api/screen/stream", methods=["POST"])
def api_screen_stream():
    body    = request.get_json(force=True) or {}
    preset  = body.get("preset")
    filters = body.get("filters", [])
    symbols = body.get("symbols", get_all_symbols())
    if preset and preset in PRESET_SCREENS:
        filters = PRESET_SCREENS[preset]["filters"]
    if not filters:
        def _err():
            yield "data: " + json.dumps({"error": "No filters"}) + "\n\n"
        return app.response_class(_err(), mimetype="text/event-stream")

    def generate():
        from concurrent.futures import ThreadPoolExecutor, as_completed
        total = len(symbols)
        matched = []
        completed = 0

        # Pre-fetch all OHLCV in parallel, stream progress as results come in
        max_workers = min(20, total)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_sym = {pool.submit(fetch_ohlcv, sym, "1y"): sym for sym in symbols}
            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                completed += 1
                yield "data: " + json.dumps({
                    "type": "progress", "current": completed,
                    "total": total, "symbol": sym
                }) + "\n\n"
                try:
                    data = future.result()
                    if not data:
                        continue
                    from engine.screener import _build_row, _match
                    row = _build_row(sym, data)
                    if row and _match(row, filters):
                        matched.append(row)
                        yield "data: " + json.dumps({"type": "match", "row": row}) + "\n\n"
                except Exception:
                    pass

        matched.sort(key=lambda r: r["score"], reverse=True)
        yield "data: " + json.dumps({"type": "done", "count": len(matched)}) + "\n\n"

    return app.response_class(generate(), mimetype="text/event-stream",
                               headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── F&O eligible stocks (NSE as of 2025-2026) ────────────────────────────────
# Source: NSE F&O segment — stocks with derivatives contracts
FNO_STOCKS = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC","SBIN",
    "BHARTIARTL","KOTAKBANK","LT","AXISBANK","ASIANPAINT","MARUTI","TITAN",
    "SUNPHARMA","ULTRACEMCO","BAJFINANCE","WIPRO","NESTLEIND","POWERGRID",
    "NTPC","TECHM","HCLTECH","ONGC","TATAMOTORS","TATASTEEL","JSWSTEEL",
    "ADANIENT","ADANIPORTS","COALINDIA","DIVISLAB","DRREDDY","EICHERMOT",
    "GRASIM","HEROMOTOCO","HINDALCO","INDUSINDBK","M&M","BAJAJFINSV",
    "BAJAJ-AUTO","BPCL","BRITANNIA","CIPLA","HDFCLIFE","SBILIFE","TATACONSUM",
    "UPL","APOLLOHOSP","VEDL","ZOMATO","IRCTC","TRENT","DMART","PAYTM",
    "NAUKRI","LTIM","PERSISTENT","COFORGE","MPHASIS","OFSS","ABCAPITAL",
    "BANKBARODA","CANBK","PNB","FEDERALBNK","IDFCFIRSTB","BANDHANBNK",
    "CHOLAFIN","MUTHOOTFIN","BAJAJHLDNG","MAXFINSERV","PIIND","UBL","MARICO",
    "DABUR","GODREJCP","COLPAL","EMAMILTD","TATACOMM","IDEA","INDIACEM",
    "OBEROIRLTY","DLF","PRESTIGE","GODREJPROP","SOBHA","PHOENIXLTD",
    "SIEMENS","ABB","HAVELLS","VOLTAS","WHIRLPOOL","CUMMINSIND","BOSCHLTD",
    "ESCORTS","MOTHERSON","BALKRISIND","EXIDEIND","AMBUJACEM","SHREECEM",
    "ULTRACEMCO","ACC","RAMCOCEM","STAR","PVRINOX","ZYDUSLIFE","BIOCON",
    "AUROPHARMA","LUPIN","TORNTPHARM","ALKEM","IPCALAB","NATCOPHARM",
    "RECLTD","PFC","IRFC","NHPC","HUDCO","SJVN","CONCOR","GMRINFRA",
    "ADANIGREEN","ADANITRANS","TATAPOWER","TORNTPOWER","IGL","MGL","GAIL",
    "PETRONET","IOC","BPCL","HPCL","CASTROLIND","DEEPAKNTR","AARTIIND",
    "GNFC","COROMANDEL","RALLIS","FACT","CHAMBLFERT","GSFC",
    "SAIL","NMDC","NATIONALUM","HINDCOPPER","RATNAMANI","WELSPUN","HSCL",
    "SYNGENE","LAURAS","GRANULES","DISHTV","SUNTV","ZEEL",
]

# ── Options chain ─────────────────────────────────────────────────────────────
@app.route("/api/fno-symbols")
def api_fno_symbols():
    """Return list of F&O eligible symbols for the options page."""
    indices = ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","NIFTYNXT50","SENSEX"]
    return jsonify({"indices": indices, "stocks": FNO_STOCKS, "all": indices + FNO_STOCKS})


@app.route("/api/options/<symbol>")
def api_options(symbol):
    """
    Basic option chain via Upstox v2 (same backend as /api/options-greeks, without Greeks enrichment).
    Kept for backward compatibility.
    """
    sym          = symbol.upper()
    expiry_param = request.args.get("expiry")

    UPSTOX_KEY_MAP = {
        "NIFTY": "NSE_INDEX|Nifty 50", "NIFTY50": "NSE_INDEX|Nifty 50",
        "BANKNIFTY": "NSE_INDEX|Nifty Bank", "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
        "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT", "SENSEX": "BSE_INDEX|SENSEX",
    }
    try:
        from engine.upstox_live import get_access_token, _upstox_get, NSE_ISIN_MAP
        import urllib.parse
        token = get_access_token()
        if not token:
            return jsonify({"error": "No Upstox token — add in Settings"}), 503

        inst_key = UPSTOX_KEY_MAP.get(sym, NSE_ISIN_MAP.get(sym, f"NSE_EQ|{sym}"))
        key_enc  = urllib.parse.quote(inst_key, safe='')

        contract_data = _upstox_get(
            f"https://api.upstox.com/v2/option/contract?instrument_key={key_enc}", token)
        from datetime import date as _date
        today = _date.today().isoformat()
        expiries = sorted(set(
            x.get("expiry","") for x in contract_data.get("data",[]) if x.get("expiry","") >= today
        ))
        if not expiries:
            return jsonify({"error": f"No expiries for {sym}"}), 404

        chosen = expiry_param if (expiry_param and expiry_param in expiries) else expiries[0]
        chain_data = _upstox_get(
            f"https://api.upstox.com/v2/option/chain?instrument_key={key_enc}&expiry_date={chosen}", token)
        rows = chain_data.get("data", [])
        if not rows:
            return jsonify({"error": f"Empty chain for {sym}"}), 404

        spot = float(rows[0].get("underlying_spot_price", 0))
        strikes_out = []
        for row in rows:
            k   = float(row.get("strike_price", 0))
            cmd = row.get("call_options", {}).get("market_data", {})
            pmd = row.get("put_options",  {}).get("market_data", {})
            cg  = row.get("call_options", {}).get("option_greeks", {})
            pg  = row.get("put_options",  {}).get("option_greeks", {})
            c_ltp = float(cmd.get("ltp", 0) or 0)
            p_ltp = float(pmd.get("ltp", 0) or 0)
            strikes_out.append({
                "strike":       k,
                "call_ltp":     c_ltp,
                "call_ltp_chng": round(c_ltp - float(cmd.get("close_price",0) or 0), 2),
                "call_oi":      int(cmd.get("oi", 0) or 0),
                "call_oi_chng": int((cmd.get("oi",0) or 0) - (cmd.get("prev_oi",0) or 0)),
                "call_iv":      float(cg.get("iv", 0) or 0),
                "call_volume":  int(cmd.get("volume", 0) or 0),
                "put_ltp":      p_ltp,
                "put_ltp_chng": round(p_ltp - float(pmd.get("close_price",0) or 0), 2),
                "put_oi":       int(pmd.get("oi", 0) or 0),
                "put_oi_chng":  int((pmd.get("oi",0) or 0) - (pmd.get("prev_oi",0) or 0)),
                "put_iv":       float(pg.get("iv", 0) or 0),
                "put_volume":   int(pmd.get("volume", 0) or 0),
            })
        strikes_out.sort(key=lambda x: x["strike"])
        return jsonify({"symbol": sym, "spot": spot, "expiries": expiries[:8],
                        "expiry": chosen, "strikes": strikes_out, "source": "upstox"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500



# ── Live tick stream (SSE) — replaces setInterval polling on frontend ─────────
@app.route("/api/stream/ticks")
def api_stream_ticks():
    """
    SSE endpoint — pushes Upstox live ticks to the browser in real time.
    Frontend connects once with EventSource('/api/stream/ticks') and receives
    {symbol, price, change_pct, source} events as they arrive.
    Falls back to latest cached values when WS is not available.
    """
    def generate():
        if not HAS_UPSTOX_WS:
            # No WS module — send empty keep-alive
            while True:
                yield ": keep-alive\n\n"
                time.sleep(15)

        client_q = upstox_ws.subscribe_queue()
        try:
            # Send all latest known prices immediately on connect
            latest = upstox_ws.get_latest()
            if latest:
                yield "data: " + json.dumps({"type": "snapshot", "ticks": latest}) + "\n\n"

            while True:
                try:
                    tick = client_q.get(timeout=20)
                    yield "data: " + json.dumps({"type": "tick", **tick}) + "\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"   # prevent proxy timeout
        finally:
            upstox_ws.unsubscribe_queue(client_q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":      "keep-alive",
        }
    )


@app.route("/api/stream/status")
def api_stream_status():
    """WebSocket feed health — used by frontend status dot."""
    if not HAS_UPSTOX_WS:
        return jsonify({"running": False, "connected": False})
    s = upstox_ws.get_status()
    stale = (time.time() - s.get("last_update", 0)) > 10
    try:
        _td = json.loads((_DATA_DIR / "upstox-token.json").read_text())
        _has_token = bool((_td.get("access_token") or "").strip())
    except Exception:
        _has_token = False
    return jsonify({
        "running":     True,
        "connected":   s.get("connected", False),
        "subscribers": s.get("subscribers", 0),
        "stale":       stale,
        "error":       s.get("error", ""),
        "has_token":   _has_token,
    })


@app.route("/api/stream/restart", methods=["POST"])
def api_stream_restart():
    """Restart the WS feed — call after saving a new token."""
    if not HAS_UPSTOX_WS:
        return jsonify({"success": False, "error": "upstox_ws not loaded"})
    try:
        upstox_ws.stop()
        import threading; import time as _t; _t.sleep(1)
        all_syms = get_all_symbols()
        upstox_ws.start(all_syms)
        return jsonify({"success": True, "symbols": len(all_syms)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ── Options Greeks + OI Analysis ─────────────────────────────────────────────
@app.route("/api/options-greeks/<symbol>")
@app.route("/api/options/process", methods=["POST"])   # legacy compat — browser POSTs {symbol:...}
def api_options_greeks(symbol=None):
    """
    Options chain via Upstox v2 /option/chain API.
    Enriched with Black-Scholes Greeks + Max Pain.
    Requires a valid Upstox access token.
    """
    from engine.greeks import enrich_chain_with_greeks, max_pain as calc_max_pain
    import urllib.request, urllib.parse, gzip as _gzip

    # Support both GET /<symbol> and POST (legacy browser-fetch path)
    _body = request.get_json(force=True, silent=True) or {}
    if symbol is None:
        symbol = _body.get("symbol", "NIFTY")

    sym          = symbol.upper()
    expiry_param = request.args.get("expiry") or (
        _body.get("expiry_param") if request.method == "POST" else None
    )

    # Upstox instrument key mapping (same as NSE_INDEX_MAP in upstox_live.py)
    UPSTOX_KEY_MAP = {
        "NIFTY":      "NSE_INDEX|Nifty 50",
        "NIFTY50":    "NSE_INDEX|Nifty 50",
        "BANKNIFTY":  "NSE_INDEX|Nifty Bank",
        "FINNIFTY":   "NSE_INDEX|Nifty Fin Service",
        "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
        "SENSEX":     "BSE_INDEX|SENSEX",
    }
    # For F&O stocks use NSE_EQ instrument key (same as NSE_ISIN_MAP or fallback)
    if sym in UPSTOX_KEY_MAP:
        inst_key = UPSTOX_KEY_MAP[sym]
    else:
        # Try ISIN map from upstox_live
        try:
            from engine.upstox_live import NSE_ISIN_MAP
            inst_key = NSE_ISIN_MAP.get(sym, f"NSE_EQ|{sym}")
        except Exception:
            inst_key = f"NSE_EQ|{sym}"

    # Get Upstox token
    try:
        from engine.upstox_live import get_access_token, _upstox_get
        token = get_access_token()
    except Exception:
        token = None

    if not token:
        return jsonify({"error": "No Upstox token — paste your daily token in Settings → Upstox tab"}), 503

    key_enc = urllib.parse.quote(inst_key, safe='')

    try:
        # ── Step 1: get available expiry dates ────────────────────────────────
        contract_url = f"https://api.upstox.com/v2/option/contract?instrument_key={key_enc}"
        contract_data = _upstox_get(contract_url, token, timeout=10)
        from datetime import date as _date
        today_str = _date.today().isoformat()
        all_expiries = sorted(set(
            x.get("expiry", "") for x in contract_data.get("data", []) if x.get("expiry", "") >= today_str
        ))
        if not all_expiries:
            return jsonify({"error": f"No expiry dates found for {sym} via Upstox"}), 404

        chosen = expiry_param if (expiry_param and expiry_param in all_expiries) else all_expiries[0]

        # ── Step 2: fetch option chain for chosen expiry ──────────────────────
        chain_url = f"https://api.upstox.com/v2/option/chain?instrument_key={key_enc}&expiry_date={chosen}"
        chain_data = _upstox_get(chain_url, token, timeout=15)
        rows = chain_data.get("data", [])
        if not rows:
            return jsonify({"error": f"Empty chain for {sym} expiry {chosen}"}), 404

        spot = float(rows[0].get("underlying_spot_price", 0))

        # ── Step 3: normalise to our internal strike dict format ──────────────
        strikes_raw = []
        for row in rows:
            k    = float(row.get("strike_price", 0))
            ce   = row.get("call_options", {})
            pe   = row.get("put_options",  {})
            cmd  = ce.get("market_data", {})
            pmd  = pe.get("market_data", {})
            cg   = ce.get("option_greeks", {})
            pg   = pe.get("option_greeks", {})

            c_ltp  = float(cmd.get("ltp", 0) or 0)
            p_ltp  = float(pmd.get("ltp", 0) or 0)
            c_prev = float(cmd.get("close_price", 0) or 0)
            p_prev = float(pmd.get("close_price", 0) or 0)

            strikes_raw.append({
                "strike":        k,
                # Call
                "call_ltp":      c_ltp,
                "call_ltp_chng": round(c_ltp - c_prev, 2) if c_prev else 0,
                "call_oi":       int(cmd.get("oi", 0) or 0),
                "call_oi_chng":  int((cmd.get("oi", 0) or 0) - (cmd.get("prev_oi", 0) or 0)),
                "call_iv":       float(cg.get("iv", 0) or 0),
                "call_volume":   int(cmd.get("volume", 0) or 0),
                # Put
                "put_ltp":       p_ltp,
                "put_ltp_chng":  round(p_ltp - p_prev, 2) if p_prev else 0,
                "put_oi":        int(pmd.get("oi", 0) or 0),
                "put_oi_chng":   int((pmd.get("oi", 0) or 0) - (pmd.get("prev_oi", 0) or 0)),
                "put_iv":        float(pg.get("iv", 0) or 0),
                "put_volume":    int(pmd.get("volume", 0) or 0),
                # Upstox already provides Greeks — use them directly
                "call_delta":    float(cg.get("delta", 0) or 0),
                "call_gamma":    float(cg.get("gamma", 0) or 0),
                "call_theta":    float(cg.get("theta", 0) or 0),
                "call_vega":     float(cg.get("vega",  0) or 0),
                "put_delta":     float(pg.get("delta", 0) or 0),
                "put_gamma":     float(pg.get("gamma", 0) or 0),
                "put_theta":     float(pg.get("theta", 0) or 0),
                "put_vega":      float(pg.get("vega",  0) or 0),
            })

        # Sort by strike
        strikes_raw.sort(key=lambda x: x["strike"])

        # Fill in any missing Greeks via BS (for strikes with zero IV from Upstox)
        strikes_raw = enrich_chain_with_greeks(strikes_raw, spot, chosen)

        mp = calc_max_pain(strikes_raw)

        # ── IV surface: fetch 4 more expiries (best effort) ──────────────────
        iv_surface = {}
        for exp in all_expiries[1:5]:
            try:
                exp_url = f"https://api.upstox.com/v2/option/chain?instrument_key={key_enc}&expiry_date={exp}"
                exp_data = _upstox_get(exp_url, token, timeout=10)
                iv_surface[exp] = [
                    {
                        "strike":  float(r.get("strike_price", 0)),
                        "call_iv": float(r.get("call_options", {}).get("option_greeks", {}).get("iv", 0) or 0),
                        "put_iv":  float(r.get("put_options",  {}).get("option_greeks", {}).get("iv", 0) or 0),
                    }
                    for r in exp_data.get("data", [])
                ]
            except Exception:
                pass

        return jsonify({
            "symbol":     sym,
            "spot":       spot,
            "expiries":   all_expiries[:8],
            "expiry":     chosen,
            "strikes":    strikes_raw,
            "max_pain":   mp,
            "iv_surface": iv_surface,
            "source":     "upstox",
        })

    except Exception as e:
        import traceback
        print(f"[options-greeks] Error: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


# ── MCX Commodity Options Chain ───────────────────────────────────────────────

# MCX commodity name → Upstox `name` field in instrument master
MCX_OPTIONS_MAP = {
    "GOLD":       "GOLD",
    "GOLDM":      "GOLD",      # mini gold — same name field
    "GOLDPETAL":  "GOLD",
    "SILVER":     "SILVER",
    "SILVERM":    "SILVER",
    "SILVERMIC":  "SILVER",
    "CRUDEOIL":   "CRUDEOIL",
    "CRUDEOILM":  "CRUDEOIL",
    "NATURALGAS": "NATURALGAS",
    "NATGASMINI": "NATURALGAS",
    "COPPER":     "COPPER",
    "ZINC":       "ZINC",
    "ALUMINIUM":  "ALUMINIUM",
    "NICKEL":     "NICKEL",
    "LEAD":       "LEAD",
}

# Cached instrument master to avoid re-downloading every request
_mcx_instruments_cache = {}
_mcx_instruments_ts    = 0.0

def _get_mcx_instruments(token: str) -> list:
    """
    Download and cache Upstox MCX instrument master (refreshed every 4 hours).
    Returns list of MCX option instrument dicts.
    """
    global _mcx_instruments_cache, _mcx_instruments_ts
    import time as _time, gzip as _gz, io, csv

    now = _time.time()
    if _mcx_instruments_cache and (now - _mcx_instruments_ts) < 14400:
        return _mcx_instruments_cache

    try:
        import requests as _req
        r = _req.get(
            "https://assets.upstox.com/market-quote/instruments/exchange/MCX.csv.gz",
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=15
        )
        if r.status_code != 200:
            raise RuntimeError(f"Instrument CSV returned {r.status_code}")

        data = _gz.decompress(r.content).decode("utf-8")
        reader = csv.DictReader(io.StringIO(data))
        rows = [row for row in reader if row.get("instrument_type") in ("OPTFUT", "OPTIDX")]

        _mcx_instruments_cache = rows
        _mcx_instruments_ts    = now
        print(f"[mcx] Instrument master loaded: {len(rows)} option contracts")
        return rows
    except Exception as e:
        print(f"[mcx] Instrument master error: {e}")
        return _mcx_instruments_cache  # return stale cache if available


@app.route("/api/options-commodity/<symbol>")
def api_options_commodity(symbol):
    """
    MCX Commodity Option Chain via Upstox instrument master + batch LTP.
    Works for GOLD, SILVER, CRUDEOIL, NATURALGAS, COPPER, ZINC, ALUMINIUM, NICKEL, LEAD.
    """
    from engine.greeks import enrich_chain_with_greeks, max_pain as calc_max_pain
    from datetime import date as _date
    import time as _time
    import urllib.parse

    sym = symbol.upper()
    expiry_param = request.args.get("expiry")

    # Map user symbol to the `name` field in Upstox master
    # tradingsymbol starts with the user symbol, name is the commodity name
    sym_to_name = {
        "GOLD": "GOLD", "GOLDM": "GOLD", "GOLDPETAL": "GOLD", "GOLDTEN": "GOLD",
        "GOLDGUINEA": "GOLD",
        "SILVER": "SILVER", "SILVERM": "SILVER", "SILVERMIC": "SILVER",
        "CRUDEOIL": "CRUDE OIL", "CRUDEOILM": "CRUDE OIL",   # master has space
        "NATURALGAS": "NATURALGAS", "NATGASMINI": "NATURALGAS",
        "COPPER": "COPPER",
        "ZINC": "ZINC", "ZINCMINI": "ZINC",
        "ALUMINIUM": "ALUMINIUM", "ALUMINI": "ALUMINIUM",
        "NICKEL": "NICKEL",
        "LEAD": "LEAD", "LEADMINI": "LEAD",
    }

    if sym not in sym_to_name:
        return jsonify({
            "error": f"'{sym}' not supported. Available: {', '.join(sorted(sym_to_name))}",
            "available": sorted(sym_to_name.keys()),
        }), 400

    name_filter = sym_to_name[sym]

    # Get Upstox token
    try:
        from engine.upstox_live import get_access_token, _upstox_get
        token = get_access_token()
    except Exception:
        token = None
    if not token:
        return jsonify({"error": "No Upstox token — paste your daily token in Settings → Upstox tab"}), 503

    try:
        # ── Step 1: Get instrument master ─────────────────────────────────────
        instruments = _get_mcx_instruments(token)
        if not instruments:
            return jsonify({"error": "Failed to load MCX instrument master from Upstox"}), 503

        today_str = _date.today().isoformat()

        # Filter: name matches AND tradingsymbol starts with sym AND future expiry
        # e.g. sym=GOLD → tradingsymbol starts with "GOLD" but not "GOLDM" if sym=GOLD
        def matches(row):
            ts  = row.get("tradingsymbol", "")
            nm  = row.get("name", "").upper().replace(" ", "")
            exp = row.get("expiry", "")
            name_norm = name_filter.upper().replace(" ", "")
            if nm != name_norm:
                return False
            if sym in ("GOLD", "GOLDPETAL", "GOLDGUINEA", "GOLDTEN"):
                # differentiate by tradingsymbol prefix
                base = "".join(c for c in ts if c.isalpha()).upper()
                if sym == "GOLD" and not (base.startswith("GOLD") and len(base) == len("GOLD")):
                    return False
                elif sym != "GOLD" and not base.startswith(sym):
                    return False
            elif sym != name_filter.replace(" ", ""):
                if not ts.upper().startswith(sym):
                    return False
            return exp >= today_str

        filtered = [r for r in instruments if matches(r)]

        if not filtered:
            # Fallback: just filter by name and expiry (space/case insensitive)
            name_norm = name_filter.upper().replace(" ", "")
            filtered = [r for r in instruments
                        if r.get("name","").upper().replace(" ","") == name_norm
                        and r.get("expiry","") >= today_str]

        if not filtered:
            return jsonify({
                "error": f"No MCX option contracts found for {sym}. "
                         "MCX options may not be available on your Upstox subscription.",
                "instrument_key_sample": instruments[0].get("instrument_key") if instruments else None,
            }), 404

        # ── Step 2: Available expiries ────────────────────────────────────────
        all_expiries = sorted(set(r.get("expiry","") for r in filtered))
        chosen = expiry_param if (expiry_param and expiry_param in all_expiries) else all_expiries[0]

        # Filter to chosen expiry
        exp_rows = [r for r in filtered if r.get("expiry","") == chosen]

        # ── Step 3: Group by strike → CE / PE ────────────────────────────────
        strike_map = {}  # strike_float → {ce: row, pe: row}
        for row in exp_rows:
            try:
                strike = float(row.get("strike", 0))
                opt_type = row.get("option_type", "").upper()
                if strike not in strike_map:
                    strike_map[strike] = {}
                strike_map[strike][opt_type] = row
            except Exception:
                pass

        if not strike_map:
            return jsonify({"error": f"No strikes found for {sym} expiry {chosen}"}), 404

        # ── Step 4: Batch fetch LTP for all instrument keys ───────────────────
        all_keys = []
        for strike, sides in strike_map.items():
            if "CE" in sides:
                all_keys.append(sides["CE"]["instrument_key"])
            if "PE" in sides:
                all_keys.append(sides["PE"]["instrument_key"])

        # Upstox market-quote endpoint — batch up to 500 instruments
        ltp_map = {}
        BATCH = 200
        for i in range(0, len(all_keys), BATCH):
            batch = all_keys[i:i+BATCH]
            encoded = ",".join(urllib.parse.quote(k, safe="") for k in batch)
            try:
                qt = _upstox_get(
                    f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={encoded}",
                    token, timeout=15
                )
                for key, val in qt.get("data", {}).items():
                    # key format: "MCX_FO:570947"
                    # convert to instrument_key format: "MCX_FO|570947"
                    norm_key = key.replace(":", "|")
                    ltp_map[norm_key] = val.get("last_price", 0) or 0
            except Exception as e:
                print(f"[mcx] LTP batch error: {e}")

        # ── Step 5: Also fetch spot (underlying future LTP) ───────────────────
        fut_rows = [r for r in instruments
                    if r.get("name","").upper().replace(" ","") == name_filter.upper().replace(" ","")
                    and r.get("instrument_type","") in ("FUTCOM","FUTIDX")
                    and r.get("expiry","") >= today_str]
        fut_rows.sort(key=lambda r: r.get("expiry",""))
        spot = 0.0
        if fut_rows:
            fut_key = fut_rows[0].get("instrument_key","")
            try:
                enc = urllib.parse.quote(fut_key, safe="")
                qt = _upstox_get(
                    f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={enc}",
                    token, timeout=8
                )
                for val in qt.get("data", {}).values():
                    spot = float(val.get("last_price", 0) or 0)
                    break
            except Exception:
                pass

        # Use last_price from instruments as fallback spot
        if not spot:
            for row in fut_rows[:1]:
                try:
                    spot = float(row.get("last_price", 0) or 0)
                except Exception:
                    pass

        # ── Step 6: Build strike dicts ────────────────────────────────────────
        import math
        strikes_raw = []
        for strike in sorted(strike_map.keys()):
            sides = strike_map[strike]
            ce_row = sides.get("CE", {})
            pe_row = sides.get("PE", {})

            ce_key = ce_row.get("instrument_key","")
            pe_key = pe_row.get("instrument_key","")

            c_ltp = float(ltp_map.get(ce_key, 0) or ce_row.get("last_price", 0) or 0)
            p_ltp = float(ltp_map.get(pe_key, 0) or pe_row.get("last_price", 0) or 0)

            # Calculate simple BS IV if spot is known
            c_iv = 0.0
            p_iv = 0.0
            if spot and strike and c_ltp:
                try:
                    from engine.greeks import black_scholes_iv
                    dte = (_date.fromisoformat(chosen) - _date.today()).days
                    c_iv = black_scholes_iv(spot, strike, dte/365, 0.065, c_ltp, "C") * 100
                except Exception:
                    pass
            if spot and strike and p_ltp:
                try:
                    from engine.greeks import black_scholes_iv
                    dte = (_date.fromisoformat(chosen) - _date.today()).days
                    p_iv = black_scholes_iv(spot, strike, dte/365, 0.065, p_ltp, "P") * 100
                except Exception:
                    pass

            strikes_raw.append({
                "strike":        strike,
                "call_ltp":      round(c_ltp, 2),
                "call_ltp_chng": 0,
                "call_oi":       0,
                "call_oi_chng":  0,
                "call_iv":       round(c_iv, 1),
                "call_volume":   0,
                "call_delta":    0,
                "call_gamma":    0,
                "call_theta":    0,
                "call_vega":     0,
                "put_ltp":       round(p_ltp, 2),
                "put_ltp_chng":  0,
                "put_oi":        0,
                "put_oi_chng":   0,
                "put_iv":        round(p_iv, 1),
                "put_volume":    0,
                "put_delta":     0,
                "put_gamma":     0,
                "put_theta":     0,
                "put_vega":      0,
            })

        # Enrich Greeks via BS
        strikes_raw = enrich_chain_with_greeks(strikes_raw, spot, chosen)
        mp = calc_max_pain(strikes_raw)

        return jsonify({
            "symbol":    sym,
            "exchange":  "MCX",
            "spot":      spot,
            "expiries":  all_expiries[:8],
            "expiry":    chosen,
            "strikes":   strikes_raw,
            "max_pain":  mp,
            "source":    "upstox_mcx",
            "total_strikes": len(strikes_raw),
        })

    except Exception as e:
        import traceback
        print(f"[options-commodity] {sym}: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/options-commodity/symbols")
def api_commodity_option_symbols():
    """List of MCX commodities with options support."""
    syms = ["GOLD","GOLDM","GOLDPETAL","SILVER","SILVERM","SILVERMIC",
            "CRUDEOIL","CRUDEOILM","NATURALGAS","NATGASMINI",
            "COPPER","ZINC","ALUMINIUM","NICKEL","LEAD","LEADMINI","ZINCMINI","ALUMINI"]
    return jsonify({"symbols": sorted(syms)})


# ── Data provider selector ────────────────────────────────────────────────────
# Stored in memory — toggled via /api/provider
# Options: "upstox" | "shoonya" | "auto"
_active_provider = "auto"   # auto = upstox if token present, else yfinance

@app.route("/api/provider", methods=["GET"])
def api_get_provider():
    return jsonify({
        "provider":     _active_provider,
        "upstox_ready": HAS_UPSTOX_LIVE and bool((_DATA_DIR / "upstox-token.json").exists()),
        "shoonya_ready": HAS_SHOONYA and bool(shoonya_status().get("has_token")) if HAS_SHOONYA else False,
    })

@app.route("/api/provider", methods=["POST"])
def api_set_provider():
    global _active_provider
    body = request.get_json(force=True) or {}
    p = body.get("provider", "auto")
    if p not in ("upstox", "shoonya", "auto"):
        return jsonify({"error": "Invalid provider"}), 400
    _active_provider = p
    return jsonify({"success": True, "provider": p})


# ── Shoonya settings ──────────────────────────────────────────────────────────
@app.route("/api/shoonya/status")
def api_shoonya_status():
    if not HAS_SHOONYA:
        return jsonify({"configured": False, "has_token": False, "sdk_loaded": False,
                        "error": "shoonya module not loaded"})
    return jsonify(shoonya_status())


@app.route("/api/shoonya/config/get")
def api_shoonya_config_get():
    """Return current config with sensitive fields partially masked — for the settings UI."""
    cfg = shoonya_get_config() if HAS_SHOONYA else {}
    api_key = cfg.get("api_key", "")
    factor2 = cfg.get("factor2", "")
    return jsonify({
        "uid":     cfg.get("uid", ""),
        "vc":      cfg.get("vc", ""),
        "imei":    cfg.get("imei", ""),
        "has_pwd": bool(cfg.get("pwd", "")),
        # Partially masked — enough to recognise without exposing the full value
        "api_key": api_key[:6] + ("•" * max(0, len(api_key) - 6)) if api_key else "",
        "factor2": factor2,   # shown in full so user can identify type (TOTP vs PIN)
    })


@app.route("/api/shoonya/config", methods=["POST"])
def api_shoonya_config():
    if not HAS_SHOONYA:
        return jsonify({"success": False, "error": "shoonya not loaded"}), 500
    body = request.get_json(force=True) or {}
    cfg  = shoonya_get_config()
    for key in ("uid", "pwd", "factor2", "vc", "api_key", "imei"):
        val = body.get(key, "").strip()
        if val:
            cfg[key] = val
    shoonya_save_config(cfg)
    return jsonify({"success": True})


@app.route("/api/shoonya/login", methods=["POST"])
def api_shoonya_login():
    if not HAS_SHOONYA:
        return jsonify({"success": False, "error": "shoonya not loaded"})
    tok, emsg = shoonya_login()
    if tok:
        return jsonify({"success": True, "message": "Logged in to Shoonya"})
    return jsonify({"success": False, "error": emsg or "Login failed — check credentials"})


# ── Intraday OHLCV (1m/5m/15m) ───────────────────────────────────────────────
@app.route("/api/intraday/<symbol>")
def api_intraday(symbol):
    """
    Intraday OHLCV bars.
    ?interval=1|5|15|30|60  (minutes)
    ?days=1|2|3
    Provider: Upstox → Shoonya → error
    """
    sym      = symbol.upper()
    interval = request.args.get("interval", "5")
    days     = int(request.args.get("days", 1))

    # Try Upstox intraday first
    if HAS_UPSTOX_LIVE:
        try:
            data = _fetch_upstox_intraday(sym, interval, days)
            if data:
                return jsonify({"data": data, "source": "upstox", "symbol": sym, "interval": interval})
        except Exception as e:
            print(f"[server] Upstox intraday error: {e}")

    # Try Shoonya
    if HAS_SHOONYA:
        try:
            data = shoonya_intraday(sym, interval, days)
            if data:
                return jsonify({"data": data, "source": "shoonya", "symbol": sym, "interval": interval})
        except Exception as e:
            print(f"[server] Shoonya intraday error: {e}")

    return jsonify({"error": f"No intraday data for {sym} — requires Upstox or Shoonya token"}), 404


def _fetch_upstox_intraday(symbol: str, interval: str, days: int) -> list:
    """Fetch intraday bars from Upstox historical intraday API."""
    import urllib.parse
    from datetime import datetime, timedelta
    from engine.upstox_live import resolve_instrument_key, _upstox_get, get_access_token

    token = get_access_token()
    if not token:
        return []

    # Upstox interval map: "1minute" | "30minute" | "day"
    interval_map = {
        "1": "1minute", "3": "3minute", "5": "5minute",
        "10": "10minute", "15": "15minute", "30": "30minute", "60": "60minute",
    }
    upstox_interval = interval_map.get(str(interval), "5minute")

    instr_key = resolve_instrument_key(symbol)
    end_dt    = datetime.now()
    start_dt  = end_dt - timedelta(days=days)
    from_date = start_dt.strftime("%Y-%m-%d")
    to_date   = end_dt.strftime("%Y-%m-%d")

    key_enc = urllib.parse.quote(instr_key, safe='')
    url = (f"https://api.upstox.com/v2/historical-candle/intraday/"
           f"{key_enc}/{upstox_interval}/{to_date}/{from_date}")

    try:
        raw = _upstox_get(url, token, timeout=10)
        candles = raw.get("data", {}).get("candles", [])
        records = []
        for c in candles:
            # [timestamp, open, high, low, close, volume, oi]
            if len(c) < 6:
                continue
            records.append({
                "date":   str(c[0])[:19].replace("T", " "),
                "open":   round(float(c[1]), 2),
                "high":   round(float(c[2]), 2),
                "low":    round(float(c[3]), 2),
                "close":  round(float(c[4]), 2),
                "volume": int(c[5]),
            })
        records.reverse()   # oldest first
        return records
    except Exception as e:
        print(f"[server] Upstox intraday {symbol}: {e}")
        return []


# ── Pre-market Gap Scanner ─────────────────────────────────────────────────────
@app.route("/api/gap-scanner")
def api_gap_scanner():
    """
    Pre-market / gap scanner.
    Finds stocks with significant gap from previous close.
    ?min_gap=2.0  — minimum absolute gap % (default 1.5)
    ?symbols=N50  — symbol universe: N50 | ALL (default N50)
    """
    import math
    from concurrent.futures import ThreadPoolExecutor, as_completed

    min_gap  = float(request.args.get("min_gap", 1.5))
    universe = request.args.get("symbols", "N50")

    if universe == "ALL":
        syms = get_all_symbols()[:100]
    else:
        syms = get_nifty50_symbols()

    results = []

    def _check(sym):
        try:
            # Get yesterday's close from cache
            from engine.fetcher import fetch_ohlcv
            data = fetch_ohlcv(sym, "5d")
            if not data or len(data) < 2:
                return None
            # Filter valid bars
            valid = [d for d in data if d.get("close") and not math.isnan(float(d["close"])) and float(d["close"]) > 0]
            if len(valid) < 2:
                return None
            prev_close = valid[-2]["close"]
            last_close = valid[-1]["close"]
            if not prev_close:
                return None

            # Get live LTP if market open
            ltp = last_close
            if HAS_UPSTOX_LIVE and is_market_hours():
                try:
                    from engine.upstox_live import fetch_upstox_quotes
                    q = fetch_upstox_quotes([sym])
                    if q.get(sym) and q[sym].get("price"):
                        ltp = q[sym]["price"]
                except Exception:
                    pass

            gap_pct = round((ltp - prev_close) / prev_close * 100, 2)
            if abs(gap_pct) >= min_gap:
                return {
                    "symbol":     sym,
                    "prev_close": round(prev_close, 2),
                    "ltp":        round(ltp, 2),
                    "gap_pct":    gap_pct,
                    "direction":  "UP" if gap_pct > 0 else "DOWN",
                    "abs_gap":    abs(gap_pct),
                }
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=min(20, len(syms))) as pool:
        futs = {pool.submit(_check, s): s for s in syms}
        for fut in as_completed(futs):
            try:
                row = fut.result()
                if row:
                    results.append(row)
            except Exception:
                pass

    results.sort(key=lambda x: x["abs_gap"], reverse=True)
    return jsonify({
        "count":    len(results),
        "results":  results,
        "min_gap":  min_gap,
        "universe": universe,
        "scanned":  len(syms),
    })


# ── Backtester ────────────────────────────────────────────────────────────────
@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    """
    Simple backtester — runs a screener preset on historical daily data
    and simulates: buy at next open, hold N days, sell at close.

    POST {
        preset:    "oversold_rsi",  # or custom filters list
        filters:   [...],
        hold_days: 5,
        universe:  "N50",          # N50 | ALL
        period:    "2y"            # lookback
    }
    """
    import math
    body      = request.get_json(force=True) or {}
    preset    = body.get("preset")
    filters   = body.get("filters", [])
    hold_days = int(body.get("hold_days", 5))
    universe  = body.get("universe", "N50")
    period    = body.get("period", "2y")

    if preset and preset in PRESET_SCREENS:
        filters = PRESET_SCREENS[preset]["filters"]
    if not filters:
        return jsonify({"error": "No filters provided"}), 400

    if universe == "ALL":
        syms = get_all_symbols()[:100]
    else:
        syms = get_nifty50_symbols()

    from engine.fetcher import fetch_ohlcv
    from engine.screener import _build_row, _match
    from engine.indicators import compute_all
    from concurrent.futures import ThreadPoolExecutor, as_completed

    trades     = []
    total_ret  = []
    wins = losses = 0

    def _run_symbol(sym):
        sym_trades = []
        try:
            data = fetch_ohlcv(sym, period)
            if not data or len(data) < 60:
                return sym_trades
            # Clean data
            data = [d for d in data if d.get("close") and not math.isnan(float(d["close"])) and float(d["close"]) > 0]

            closes  = [d["close"] for d in data]
            n       = len(data)

            # Slide a 60-bar window across history
            for i in range(59, n - hold_days - 1):
                window = data[i-59:i+1]
                row    = _build_row(sym, window)
                if row and _match(row, filters):
                    buy_price  = data[i+1]["open"]      # buy next open
                    sell_price = data[i+1+hold_days]["close"]  # sell after hold_days
                    if not buy_price or not sell_price:
                        continue
                    ret = round((sell_price - buy_price) / buy_price * 100, 2)
                    sym_trades.append({
                        "symbol":     sym,
                        "entry_date": data[i+1]["date"],
                        "exit_date":  data[i+1+hold_days]["date"],
                        "buy_price":  round(buy_price, 2),
                        "sell_price": round(sell_price, 2),
                        "return_pct": ret,
                        "result":     "WIN" if ret > 0 else "LOSS",
                    })
        except Exception:
            pass
        return sym_trades

    with ThreadPoolExecutor(max_workers=min(15, len(syms))) as pool:
        futs = {pool.submit(_run_symbol, s): s for s in syms}
        for fut in as_completed(futs):
            sym_trades = fut.result()
            trades.extend(sym_trades)
            for t in sym_trades:
                total_ret.append(t["return_pct"])
                if t["return_pct"] > 0:
                    wins += 1
                else:
                    losses += 1

    trades.sort(key=lambda x: x["entry_date"], reverse=True)
    n_trades = len(trades)
    avg_ret  = round(sum(total_ret) / n_trades, 2) if n_trades else 0
    win_rate = round(wins / n_trades * 100, 1)    if n_trades else 0
    max_win  = round(max(total_ret), 2)            if total_ret else 0
    max_loss = round(min(total_ret), 2)            if total_ret else 0

    # Best/worst symbols
    sym_stats = {}
    for t in trades:
        s = t["symbol"]
        if s not in sym_stats:
            sym_stats[s] = {"trades": 0, "total_ret": 0}
        sym_stats[s]["trades"]    += 1
        sym_stats[s]["total_ret"] += t["return_pct"]
    for s in sym_stats:
        sym_stats[s]["avg_ret"] = round(sym_stats[s]["total_ret"] / sym_stats[s]["trades"], 2)

    top_syms = sorted(sym_stats.items(), key=lambda x: x[1]["avg_ret"], reverse=True)[:5]
    bot_syms = sorted(sym_stats.items(), key=lambda x: x[1]["avg_ret"])[:5]

    return jsonify({
        "summary": {
            "total_trades": n_trades,
            "win_rate":     win_rate,
            "avg_return":   avg_ret,
            "max_win":      max_win,
            "max_loss":     max_loss,
            "wins":         wins,
            "losses":       losses,
            "hold_days":    hold_days,
            "universe":     universe,
            "period":       period,
        },
        "top_symbols": [{"symbol": s, **v} for s, v in top_syms],
        "bot_symbols": [{"symbol": s, **v} for s, v in bot_syms],
        "trades":      trades[:200],   # cap at 200 for response size
    })


# ── Market Intelligence — premium whole-market analysis ───────────────────────
@app.route("/api/market-intelligence")
def api_market_intelligence():
    """
    Full market intelligence: regime, breadth, sectors, leaders,
    reversal watchlist, breakout candidates, macro snapshot.
    Cached 20 min. Pass ?force=1 to refresh immediately.
    """
    force = request.args.get("force", "0") == "1"
    try:
        data = get_market_intelligence(force=force)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Advisor — smart buy/sell/hold recommendation ──────────────────────────────
@app.route("/api/advisor/<symbol>")
def api_advisor(symbol):
    """
    Multi-factor smart recommendation for a stock/index/commodity.
    Returns action (BUY/SELL/HOLD), confidence %, entry, SL, targets,
    factor breakdown, and plain-English summary.
    """
    period = request.args.get("period", "1y")
    data   = fetch_ohlcv(symbol.upper(), period=period)
    if not data:
        return jsonify({"error": f"No data for {symbol}"}), 404
    # Inject live price if market is open
    if HAS_UPSTOX_LIVE and is_market_hours():
        q = smart_quote(symbol.upper())
        if q and q.get("price") and data:
            data[-1]["close"] = q["price"]
    result = advise(data, symbol.upper())
    return jsonify(result)


# ── Alerts — CRUD + check ─────────────────────────────────────────────────────
@app.route("/api/alerts", methods=["GET"])
def api_alerts_list():
    symbol     = request.args.get("symbol")
    active_only = request.args.get("active_only", "false").lower() == "true"
    return jsonify(get_alerts(symbol=symbol, active_only=active_only))


@app.route("/api/alerts", methods=["POST"])
def api_alerts_add():
    body = request.get_json(force=True) or {}
    sym   = body.get("symbol", "").strip().upper()
    atype = body.get("type", "").strip()
    value = body.get("value")
    note  = body.get("note", "")
    if not sym or not atype or value is None:
        return jsonify({"error": "symbol, type, and value are required"}), 400
    try:
        alert = add_alert(sym, atype, float(value), note)
        return jsonify(alert), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
def api_alerts_delete(alert_id):
    ok = delete_alert(alert_id)
    return jsonify({"success": ok})


@app.route("/api/alerts/<int:alert_id>/toggle", methods=["POST"])
def api_alerts_toggle(alert_id):
    body    = request.get_json(force=True) or {}
    enabled = body.get("enabled", True)
    ok = toggle_alert(alert_id, bool(enabled))
    return jsonify({"success": ok})


@app.route("/api/alerts/<int:alert_id>/reset", methods=["POST"])
def api_alerts_reset(alert_id):
    ok = reset_alert(alert_id)
    return jsonify({"success": ok})


@app.route("/api/alerts/history")
def api_alerts_history():
    limit = int(request.args.get("limit", 50))
    return jsonify(get_alert_history(limit))


@app.route("/api/alerts/check", methods=["POST"])
def api_alerts_check():
    """
    Manual check — POST {prices: {SYM: price}, rsi: {SYM: rsi}, vol: {SYM: vol_ratio}}
    Returns list of fired alerts.
    """
    body      = request.get_json(force=True) or {}
    price_map = body.get("prices", {})
    rsi_map   = body.get("rsi", {})
    vol_map   = body.get("vol", {})
    fired = check_alerts(price_map, rsi_map, vol_map)
    return jsonify({"fired": fired, "count": len(fired)})


# ── Shoonya Positions / P&L ──────────────────────────────────────────────────

@app.route("/api/shoonya/positions")
def api_shoonya_positions():
    """
    Fetch today's positions from Shoonya with realised + unrealised P&L.
    Used by Profit Guard to track actual trading P&L in real time.
    Returns:
      { positions: [...], summary: {realised_pnl, unrealised_pnl, total_pnl}, error }
    """
    if not HAS_SHOONYA:
        return jsonify({"error": "Shoonya not configured", "positions": [],
                        "summary": {"realised_pnl": 0, "unrealised_pnl": 0, "total_pnl": 0}}), 503
    try:
        from engine.shoonya_live import get_positions
        return jsonify(get_positions())
    except Exception as e:
        return jsonify({"error": str(e), "positions": [],
                        "summary": {"realised_pnl": 0, "unrealised_pnl": 0, "total_pnl": 0}}), 500


@app.route("/api/shoonya/orders")
def api_shoonya_orders():
    """Fetch today's order book from Shoonya."""
    if not HAS_SHOONYA:
        return jsonify({"error": "Shoonya not configured", "orders": []}), 503
    try:
        from engine.shoonya_live import get_order_book
        orders = get_order_book()
        return jsonify({"orders": orders, "count": len(orders)})
    except Exception as e:
        return jsonify({"error": str(e), "orders": []}), 500


# ── Pro Tips (professional-grade) ────────────────────────────────────────────

@app.route("/api/pro-tips/<symbol>")
def api_pro_tips_single(symbol):
    """
    Generate a professional-grade tip for one symbol.
    Query params:
      ?vix=13.5          — current IndiaVIX (optional)
      ?with_bn=1         — include BankNifty cross-ref (default 1 for indices)
    """
    from engine.pro_tips import generate_pro_tip
    sym  = symbol.upper()
    vix  = None
    try:
        vix = float(request.args.get("vix", 0) or 0) or None
    except Exception:
        pass

    data = fetch_ohlcv(sym, period="3mo")
    bn_data = None
    if request.args.get("with_bn", "1") == "1":
        try:
            bn_data = fetch_ohlcv("BANKNIFTY", period="3mo")
        except Exception:
            pass

    # Inject live price
    try:
        if HAS_UPSTOX_LIVE and is_market_hours():
            from engine.upstox_live import batch_quote_all
            quotes = batch_quote_all([sym])
            lp = quotes.get(sym, {}).get("price")
            if lp and data:
                data[-1]["close"] = lp
    except Exception:
        pass

    return jsonify(generate_pro_tip(sym, data=data, vix=vix, bn_data=bn_data))


@app.route("/api/pro-tips/batch", methods=["POST"])
def api_pro_tips_batch():
    """
    Generate professional tips for multiple symbols.
    Body: {symbols: [...], vix: 13.5}
    """
    from engine.pro_tips import generate_pro_tips_batch
    body    = request.get_json(force=True) or {}
    symbols = [s.upper() for s in body.get("symbols", ["NIFTY", "BANKNIFTY"])]
    vix     = float(body.get("vix", 0) or 0) or None

    # Inject live prices
    live_prices = {}
    try:
        if HAS_UPSTOX_LIVE and is_market_hours():
            from engine.upstox_live import batch_quote_all
            quotes = batch_quote_all(symbols)
            live_prices = {s: q.get("price") for s, q in quotes.items() if q.get("price")}
    except Exception:
        pass

    results = generate_pro_tips_batch(symbols, live_prices=live_prices, vix=vix)
    return jsonify({"count": len(results), "results": results,
                    "generated": __import__("datetime").datetime.now().strftime("%d %b %Y %H:%M")})


# ── Tips Log — SQLite history of generated tips ───────────────────────────────
import sqlite3 as _sqlite3

_TIPS_LOG_DB = _DATA_DIR / "tips_log.db"

def _tips_log_conn():
    con = _sqlite3.connect(str(_TIPS_LOG_DB))
    con.row_factory = _sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS tips_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT    NOT NULL,
            mode        TEXT    NOT NULL DEFAULT 'standard',
            bias        TEXT,
            direction   TEXT,
            entry       TEXT,
            sl          TEXT,
            target1     TEXT,
            target2     TEXT,
            tip_text    TEXT,
            price       REAL,
            generated   TEXT,
            logged_at   TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)
    con.commit()
    return con

@app.route("/api/tips/log", methods=["POST"])
def api_tips_log_save():
    """Save one or more generated tips to the log."""
    body  = request.get_json(force=True) or {}
    tips  = body.get("tips", [])
    mode  = body.get("mode", "standard")
    if not tips:
        return jsonify({"success": False, "error": "no tips provided"}), 400
    try:
        con = _tips_log_conn()
        for t in tips:
            con.execute("""
                INSERT INTO tips_log
                    (symbol, mode, bias, direction, entry, sl, target1, target2, tip_text, price, generated)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                t.get("symbol",""),
                mode,
                t.get("bias",""),
                t.get("direction",""),
                str(t.get("entry","")),
                str(t.get("sl","")),
                str(t.get("target1","")),
                str(t.get("target2","")),
                t.get("tip_text") or t.get("text",""),
                t.get("price"),
                t.get("generated",""),
            ))
        con.commit()
        con.close()
        return jsonify({"success": True, "saved": len(tips)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tips/log", methods=["GET"])
def api_tips_log_get():
    """Return recent tips log entries. ?limit=50&symbol=NIFTY"""
    limit  = min(int(request.args.get("limit", 100)), 500)
    symbol = request.args.get("symbol", "").upper()
    try:
        con = _tips_log_conn()
        if symbol:
            rows = con.execute(
                "SELECT * FROM tips_log WHERE symbol=? ORDER BY id DESC LIMIT ?",
                (symbol, limit)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM tips_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        con.close()
        return jsonify({"count": len(rows), "logs": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/tips/log/<int:log_id>", methods=["DELETE"])
def api_tips_log_delete(log_id):
    """Delete a single log entry."""
    try:
        con = _tips_log_conn()
        con.execute("DELETE FROM tips_log WHERE id=?", (log_id,))
        con.commit()
        con.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/tips/log/clear", methods=["POST"])
def api_tips_log_clear():
    """Clear all tips log entries."""
    try:
        con = _tips_log_conn()
        con.execute("DELETE FROM tips_log")
        con.commit()
        con.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Premium Intraday Calls ────────────────────────────────────────────────────

@app.route("/api/premium-call/<symbol>")
def api_premium_call(symbol):
    """
    Generate a premium intraday call for a symbol.
    Fuses price action + VIX + live options OI.
    """
    try:
        from engine.premium_calls import generate_premium_call
        from engine.fetcher import fetch_ohlcv
        sym  = symbol.upper()
        data = fetch_ohlcv(sym, period="3mo")
        # Inject live price if available
        live = None
        try:
            if HAS_UPSTOX_LIVE and is_market_hours():
                from engine.upstox_live import batch_quote_all
                q = batch_quote_all([sym])
                live = q.get(sym, {}).get("price")
        except Exception:
            pass
        result = generate_premium_call(sym, data=data, live_price=live,
                                       fetch_options=True, fetch_vix=True)
        return jsonify(result)
    except Exception as e:
        import traceback
        print(f"[premium-call] {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/premium-call/batch", methods=["POST"])
def api_premium_call_batch():
    """
    POST {symbols: ["NIFTY50", "BANKNIFTY", ...]}
    Returns sorted list of premium calls by conviction score.
    """
    try:
        from engine.premium_calls import generate_batch_calls
        body    = request.get_json(force=True) or {}
        symbols = [s.upper() for s in body.get("symbols", ["NIFTY50", "BANKNIFTY"])]
        results = generate_batch_calls(symbols[:10])
        return jsonify({"count": len(results), "calls": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Profit Guard ─────────────────────────────────────────────────────────────@app.route("/api/profit-guard/settings", methods=["GET"])
def api_profit_guard_get():
    """Return current profit guard settings."""
    from engine.profit_guard import get_settings
    return jsonify(get_settings())


@app.route("/api/profit-guard/settings", methods=["POST"])
def api_profit_guard_save():
    """Save profit guard settings. Body: {enabled, target, protection_level, cooldown_mins, total_clicks}"""
    from engine.profit_guard import save_settings
    body = request.get_json(force=True) or {}
    try:
        result = save_settings(body)
        return jsonify({"success": True, "settings": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/profit-guard/status", methods=["GET"])
def api_profit_guard_status():
    """Get current block/lock status (no P&L needed)."""
    from engine.profit_guard import get_status
    return jsonify(get_status())


@app.route("/api/profit-guard/check", methods=["POST"])
def api_profit_guard_check():
    """
    Check P&L against target. Body: {pnl: float}
    Returns full guard status including whether target was just hit.
    """
    from engine.profit_guard import check_profit
    body = request.get_json(force=True) or {}
    pnl = float(body.get("pnl", 0))
    return jsonify(check_profit(pnl))


@app.route("/api/profit-guard/unlock-click", methods=["POST"])
def api_profit_guard_unlock_click():
    """Record one unlock confirmation click. Returns remaining clicks."""
    from engine.profit_guard import record_unlock_click
    return jsonify(record_unlock_click())


@app.route("/api/profit-guard/reset", methods=["POST"])
def api_profit_guard_reset():
    """Manually reset today's lock state."""
    from engine.profit_guard import manual_reset
    return jsonify(manual_reset())


if __name__ == "__main__":
    print("=" * 50)
    print(f"  Stock Analysis Server  http://localhost:5002")
    print(f"  Upstox live: {HAS_UPSTOX_LIVE}")
    print(f"  Upstox WS:   {HAS_UPSTOX_WS}")
    print(f"  Market open: {is_market_hours()}")
    print("=" * 50)

    # ── Alert checker background thread ──────────────────────────────────────
    import threading as _threading
    _alert_thread_running = True

    def _alert_check_loop():
        """
        Background loop: checks active alerts every 5s during market hours,
        every 60s off-market. Fires matched alerts and pushes SSE notifications.
        """
        import time as _time
        from engine.alerts import check_alerts, get_alerts
        from engine.fetcher import fetch_quote as _yf_quote

        print("[alerts] Background checker started")
        while _alert_thread_running:
            try:
                # Only check if there are active alerts
                active = get_alerts(active_only=True)
                if active:
                    # Build price map for all symbols in active alerts
                    syms = list({a["symbol"] for a in active})
                    price_map = {}
                    rsi_map   = {}
                    vol_map   = {}

                    # Get live prices
                    for sym in syms:
                        try:
                            if HAS_UPSTOX_LIVE and is_market_hours():
                                from engine.upstox_live import smart_quote
                                q = smart_quote(sym)
                            else:
                                q = _yf_quote(sym)
                            p = q.get("price") or 0
                            if p:
                                price_map[sym] = p
                        except Exception:
                            pass

                    # Fire matching alerts
                    fired = check_alerts(price_map, rsi_map, vol_map)
                    if fired:
                        print(f"[alerts] Fired {len(fired)} alert(s): {[f['symbol'] for f in fired]}")
                        # Push to SSE tick stream so dashboard notification panel updates
                        if HAS_UPSTOX_WS:
                            for alert in fired:
                                tick = {
                                    "type":    "alert_fired",
                                    "symbol":  alert["symbol"],
                                    "alert":   alert,
                                    "price_at": alert.get("price_at", 0),
                                }
                                try:
                                    import engine.upstox_ws as _ws
                                    _ws._broadcast(tick)
                                except Exception:
                                    pass

            except Exception as e:
                print(f"[alerts] Checker error: {e}")

            # Sleep 5s during market hours, 60s off-hours
            sleep = 5 if is_market_hours() else 60
            _time.sleep(sleep)

    _alert_thread = _threading.Thread(target=_alert_check_loop, daemon=True, name="alert-checker")
    _alert_thread.start()
    print(f"[server] Alert checker started")

    # Start Upstox WebSocket feed in background if token exists
    if HAS_UPSTOX_WS and HAS_UPSTOX_LIVE:
        try:
            all_syms = get_all_symbols()
            upstox_ws.start(all_syms)
            print(f"[server] Upstox WS feed started for {len(all_syms)} symbols")
        except Exception as e:
            print(f"[server] WS feed start error: {e}")

    app.run(host="0.0.0.0", port=5002, debug=False, threaded=True)


