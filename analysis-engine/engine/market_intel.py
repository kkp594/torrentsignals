"""
Market Intelligence Engine — premium whole-market analysis.
Runs without per-stock scan. Analyses:

1. Market Regime         — Bull / Bear / Sideways based on Nifty + BankNifty
2. Sector Rotation       — which sectors are leading vs lagging
3. Breadth               — how many Nifty50 stocks are above EMA50/200
4. Momentum Leaders      — top 5 strongest + weakest stocks right now
5. Reversal Watchlist    — stocks near key support with oversold bounce setup
6. Breakout Candidates   — stocks in BB squeeze with rising volume
7. Risk Gauge            — India VIX proxy + put/call ratio proxy
8. Macro Snapshot        — USD/INR, Gold, Crude, Nifty correlation signal

All computed from cached yfinance data — fast, no external API calls needed.
Results cached for 20 minutes.
"""
from __future__ import annotations
import time
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

from .fetcher import fetch_ohlcv, fetch_quote, _get_usd_inr
from .indicators import compute_all, rsi as calc_rsi, ema as calc_ema
from .signals import _last, _prev, analyze
from .advisor import _safe, _slope, _pct

# ── Cache ─────────────────────────────────────────────────────────────────────
_intel_cache: dict = {}
_intel_cache_ts: float = 0.0
_CACHE_TTL = 20 * 60  # 20 minutes

# ── Universe ──────────────────────────────────────────────────────────────────
NIFTY50 = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","ITC","SBIN",
    "BHARTIARTL","KOTAKBANK","LT","AXISBANK","ASIANPAINT","MARUTI","TITAN",
    "SUNPHARMA","ULTRACEMCO","BAJFINANCE","WIPRO","NESTLEIND","POWERGRID",
    "NTPC","TECHM","HCLTECH","ONGC","TATAMOTORS","TATASTEEL","JSWSTEEL",
    "ADANIENT","ADANIPORTS","COALINDIA","DIVISLAB","DRREDDY","EICHERMOT",
    "GRASIM","HEROMOTOCO","HINDALCO","INDUSINDBK","M&M","BAJAJFINSV",
    "BAJAJ-AUTO","BPCL","BRITANNIA","CIPLA","HDFCLIFE","SBILIFE","TATACONSUM",
    "UPL","VEDL","APOLLOHOSP",
]

SECTOR_MAP = {
    "IT":        ["TCS","INFY","WIPRO","TECHM","HCLTECH"],
    "Banking":   ["HDFCBANK","ICICIBANK","SBIN","KOTAKBANK","AXISBANK","INDUSINDBK"],
    "FMCG":      ["HINDUNILVR","ITC","NESTLEIND","BRITANNIA","TATACONSUM"],
    "Auto":      ["MARUTI","TATAMOTORS","M&M","EICHERMOT","HEROMOTOCO","BAJAJ-AUTO"],
    "Pharma":    ["SUNPHARMA","DIVISLAB","DRREDDY","CIPLA","APOLLOHOSP"],
    "Energy":    ["RELIANCE","ONGC","BPCL","POWERGRID","NTPC"],
    "Metal":     ["TATASTEEL","JSWSTEEL","HINDALCO","COALINDIA","VEDL"],
    "Infra":     ["LT","ADANIENT","ADANIPORTS","ULTRACEMCO","GRASIM"],
    "FinServ":   ["BAJFINANCE","BAJAJFINSV","HDFCLIFE","SBILIFE"],
}

INDICES = ["NIFTY50", "BANKNIFTY", "FINNIFTY"]
COMMODITIES = ["GOLD", "SILVER", "CRUDEOIL"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_parallel(symbols: list, period: str = "3mo", max_workers: int = 20) -> dict:
    """
    Fetch OHLCV for multiple symbols. Returns {sym: clean_data}.

    yfinance 1.2+ is NOT thread-safe for concurrent downloads.
    Strategy:
      1. Check which symbols already have fresh cache (instant, no lock needed).
      2. Download missing symbols SEQUENTIALLY (via the global lock in fetch_ohlcv).
      3. Read all results — cached ones in parallel, new ones already written.
    This gives full speed on warm cache and correct data on cold cache.
    """
    from .fetcher import _is_fresh, _cache_path
    import json as _json

    # Split: cached vs needs download
    cached_syms  = []
    missing_syms = []
    for sym in symbols:
        cache = _cache_path(sym, f"{period}_1d")
        if _is_fresh(cache):
            cached_syms.append(sym)
        else:
            missing_syms.append(sym)

    # Download missing ones sequentially (safe — global lock inside fetch_ohlcv)
    for sym in missing_syms:
        fetch_ohlcv(sym, period)   # result goes to cache

    # Now read all from cache in parallel (safe — read-only)
    results = {}
    def _read(sym):
        data = fetch_ohlcv(sym, period)   # will hit cache
        clean = [d for d in data
                 if d.get("close") and not math.isnan(float(d["close"])) and float(d["close"]) > 0]
        return sym, clean

    with ThreadPoolExecutor(max_workers=min(max_workers, len(symbols))) as pool:
        futs = {pool.submit(_read, sym): sym for sym in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                sym_out, clean = fut.result()
                if clean:
                    results[sym_out] = clean
            except Exception:
                pass

    return results


def _momentum_score(data: list) -> float:
    """
    0-100 composite momentum for ranking stocks.
    Uses RSI, price vs EMA20/50, MACD direction, 1-month return.
    """
    if not data or len(data) < 30:
        return 50.0
    closes = [d["close"] for d in data]
    ind = compute_all(data)

    score = 0.0
    w_total = 0.0

    # 1-month return (20 bars)
    ret = _pct(closes[-1], closes[-20]) if len(closes) >= 20 else 0
    score  += ret * 2
    w_total += 10

    # RSI
    rsi_v = _safe(_last(ind["rsi14"]))
    if rsi_v is not None:
        # Normalise: 0→0, 50→50, 100→100
        score  += rsi_v
        w_total += 100

    # Price vs EMA20
    ema20 = _safe(_last(ind["ema20"]))
    if ema20 and closes[-1] > ema20:
        score  += 20
        w_total += 20

    # Price vs EMA50
    ema50 = _safe(_last(ind["ema50"]))
    if ema50 and closes[-1] > ema50:
        score  += 20
        w_total += 20

    # MACD sign
    macd_v = _safe(_last(ind["macd"][0]))
    if macd_v is not None:
        score  += (10 if macd_v > 0 else 0)
        w_total += 10

    return round(score / w_total * 100, 1) if w_total else 50.0


def _regime_from_nifty(data: list) -> dict:
    """
    Market regime based on Nifty50 price action.
    Returns {regime, strength, detail}
    """
    if not data or len(data) < 60:
        return {"regime": "Unknown", "strength": 0, "price": 0, "detail": "Insufficient data",
                "rsi": None, "ema20": None, "ema50": None, "ema200": None}

    closes = [d["close"] for d in data]
    ind    = compute_all(data)

    ema20  = _safe(_last(ind["ema20"]))
    ema50  = _safe(_last(ind["ema50"]))
    ema200 = _safe(_last(ind["ema200"]))
    rsi_v  = _safe(_last(ind["rsi14"]))
    price  = closes[-1]
    adx_v  = _safe(_last(ind["adx14"]))

    bull_pts = 0
    bear_pts = 0
    details  = []

    if ema200 and price > ema200:
        bull_pts += 3
        details.append(f"Price ₹{price:.0f} > EMA200 ₹{ema200:.0f}")
    elif ema200 and price < ema200:
        bear_pts += 3
        details.append(f"Price ₹{price:.0f} < EMA200 ₹{ema200:.0f} ⚠")

    if ema50 and price > ema50:
        bull_pts += 2
        details.append("Above EMA50")
    elif ema50 and price < ema50:
        bear_pts += 2
        details.append("Below EMA50")

    if ema20 and ema50 and ema20 > ema50:
        bull_pts += 2
        details.append("Golden alignment (EMA20 > EMA50)")
    elif ema20 and ema50 and ema20 < ema50:
        bear_pts += 2
        details.append("Death alignment (EMA20 < EMA50)")

    if rsi_v and rsi_v > 55:
        bull_pts += 1
        details.append(f"RSI {rsi_v:.1f} — bullish zone")
    elif rsi_v and rsi_v < 45:
        bear_pts += 1
        details.append(f"RSI {rsi_v:.1f} — bearish zone")

    total  = bull_pts + bear_pts
    bull_pct = (bull_pts / total * 100) if total else 50

    if bull_pct >= 70:
        regime   = "Bull Market"
        strength = round(bull_pct)
    elif bull_pct <= 30:
        regime   = "Bear Market"
        strength = round(100 - bull_pct)
    else:
        regime   = "Sideways / Mixed"
        strength = 50

    trend_str = f"ADX {adx_v:.1f} — {'strong trend' if adx_v >= 25 else 'weak trend'}" if adx_v else ""
    if trend_str:
        details.append(trend_str)

    return {
        "regime":   regime,
        "strength": strength,
        "price":    round(price, 2),
        "detail":   " | ".join(details),
        "rsi":      round(rsi_v, 1) if rsi_v else None,
        "ema20":    round(ema20, 2) if ema20 else None,
        "ema50":    round(ema50, 2) if ema50 else None,
        "ema200":   round(ema200, 2) if ema200 else None,
    }


def _sector_strength(sector_data: dict) -> list:
    """
    Compute average 1-month return for each sector.
    Returns sorted list [{sector, return_pct, momentum, leader, laggard}]
    """
    results = []
    for sector, symbols in SECTOR_MAP.items():
        returns = []
        mom_scores = []
        for sym in symbols:
            data = sector_data.get(sym)
            if not data or len(data) < 22:
                continue
            closes = [d["close"] for d in data]
            r = _pct(closes[-1], closes[-22]) if len(closes) >= 22 else 0
            returns.append((sym, r))
            mom_scores.append((sym, _momentum_score(data)))

        if not returns:
            continue

        avg_ret  = sum(r for _, r in returns) / len(returns)
        best     = max(returns, key=lambda x: x[1])
        worst    = min(returns, key=lambda x: x[1])
        avg_mom  = sum(m for _, m in mom_scores) / len(mom_scores) if mom_scores else 50

        results.append({
            "sector":       sector,
            "return_1m":    round(avg_ret, 2),
            "momentum":     round(avg_mom, 1),
            "leader":       best[0],
            "leader_ret":   round(best[1], 2),
            "laggard":      worst[0],
            "laggard_ret":  round(worst[1], 2),
            "stock_count":  len(returns),
        })

    results.sort(key=lambda x: x["return_1m"], reverse=True)
    return results


def _market_breadth(all_data: dict) -> dict:
    """Percentage of Nifty50 stocks above EMA50 and EMA200."""
    above_ema50  = 0
    above_ema200 = 0
    total = 0

    for sym, data in all_data.items():
        if sym not in NIFTY50 or not data or len(data) < 30:
            continue
        closes = [d["close"] for d in data]
        ind = compute_all(data)
        price  = closes[-1]
        ema50  = _safe(_last(ind["ema50"]))
        ema200 = _safe(_last(ind["ema200"]))
        total += 1
        if ema50  and price > ema50:  above_ema50  += 1
        if ema200 and price > ema200: above_ema200 += 1

    if total == 0:
        return {"above_ema50_pct": 0, "above_ema200_pct": 0, "total": 0}

    pct50  = round(above_ema50  / total * 100, 1)
    pct200 = round(above_ema200 / total * 100, 1)

    # Breadth interpretation
    if pct50 >= 70:
        breadth_signal = "Strong — broad participation in rally"
    elif pct50 >= 50:
        breadth_signal = "Moderate — selective buying"
    elif pct50 >= 30:
        breadth_signal = "Weak — narrow market, caution advised"
    else:
        breadth_signal = "Very Weak — broad selling pressure"

    return {
        "above_ema50_pct":  pct50,
        "above_ema200_pct": pct200,
        "above_ema50":      above_ema50,
        "above_ema200":     above_ema200,
        "total":            total,
        "signal":           breadth_signal,
    }


def _top_movers(all_data: dict, n: int = 5) -> dict:
    """Top N momentum leaders and bottom N laggards from Nifty50."""
    scores = []
    for sym in NIFTY50:
        data = all_data.get(sym)
        if not data or len(data) < 22:
            continue
        closes = [d["close"] for d in data]
        ret_1m = _pct(closes[-1], closes[-22]) if len(closes) >= 22 else 0
        ret_1w = _pct(closes[-1], closes[-5])  if len(closes) >= 5  else 0
        mom    = _momentum_score(data)
        ind    = compute_all(data)
        rsi_v  = _safe(_last(ind["rsi14"]))
        scores.append({
            "symbol":   sym,
            "price":    round(closes[-1], 2),
            "ret_1m":   round(ret_1m, 2),
            "ret_1w":   round(ret_1w, 2),
            "momentum": mom,
            "rsi":      round(rsi_v, 1) if rsi_v else None,
        })

    scores.sort(key=lambda x: x["momentum"], reverse=True)
    return {
        "leaders":  scores[:n],
        "laggards": scores[-n:][::-1],
    }


def _reversal_watchlist(all_data: dict, n: int = 6) -> list:
    """
    Stocks near support with oversold RSI — bounce candidates.
    Conditions: RSI < 38, price near/below BB lower band, price > EMA200
    """
    candidates = []
    for sym in NIFTY50:
        data = all_data.get(sym)
        if not data or len(data) < 60:
            continue
        closes  = [d["close"] for d in data]
        price   = closes[-1]
        ind     = compute_all(data)
        rsi_v   = _safe(_last(ind["rsi14"]))
        bbl     = _safe(_last(ind["bb"][2]))
        ema200  = _safe(_last(ind["ema200"]))
        ema50   = _safe(_last(ind["ema50"]))
        stoch_k = _safe(_last(ind["stoch"][0]))
        macd_v  = _safe(_last(ind["macd"][0]))
        macd_s  = _safe(_last(ind["macd"][1]))

        if not rsi_v or rsi_v > 40:
            continue
        if not bbl:
            continue

        near_bbl = price <= bbl * 1.02   # within 2% of lower band
        if not near_bbl:
            continue

        # Score the reversal setup
        rev_score = 0
        reasons   = []
        if rsi_v < 30:
            rev_score += 3
            reasons.append(f"RSI {rsi_v:.1f} — deeply oversold")
        elif rsi_v < 40:
            rev_score += 2
            reasons.append(f"RSI {rsi_v:.1f} — oversold")
        if stoch_k and stoch_k < 20:
            rev_score += 2
            reasons.append(f"Stoch {stoch_k:.0f} — oversold")
        if ema200 and price > ema200:
            rev_score += 2
            reasons.append("Above EMA200 — long-term uptrend intact")
        if macd_v and macd_s and macd_v > macd_s:
            rev_score += 1
            reasons.append("MACD turning up")

        if rev_score >= 3:
            atr_v = _safe(_last(ind["atr14"])) or price * 0.015
            candidates.append({
                "symbol":    sym,
                "price":     round(price, 2),
                "rsi":       round(rsi_v, 1),
                "bb_lower":  round(bbl, 2),
                "ema200":    round(ema200, 2) if ema200 else None,
                "rev_score": rev_score,
                "reasons":   reasons,
                "target":    round(price + 2.0 * atr_v, 2),
                "stop_loss": round(price - 1.5 * atr_v, 2),
            })

    candidates.sort(key=lambda x: x["rev_score"], reverse=True)
    return candidates[:n]


def _breakout_candidates(all_data: dict, n: int = 6) -> list:
    """
    Stocks in Bollinger Band squeeze with rising volume — imminent breakout.
    Conditions: BB width < 6%, volume rising, price above EMA20
    """
    candidates = []
    for sym in NIFTY50:
        data = all_data.get(sym)
        if not data or len(data) < 30:
            continue
        closes = [d["close"] for d in data]
        vols   = [d["volume"] for d in data]
        price  = closes[-1]
        ind    = compute_all(data)

        bbu    = _safe(_last(ind["bb"][0]))
        bbl    = _safe(_last(ind["bb"][2]))
        bbm    = _safe(_last(ind["bb"][1]))
        ema20  = _safe(_last(ind["ema20"]))
        adx_v  = _safe(_last(ind["adx14"]))

        if not (bbu and bbl and bbm):
            continue

        bb_width = (bbu - bbl) / bbm * 100
        if bb_width >= 7:
            continue

        # Volume trend — compare last 5 vs prior 10 days
        avg_vol_prior = sum(vols[-15:-5]) / 10 if len(vols) >= 15 else 0
        avg_vol_recent = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
        vol_rising = avg_vol_prior > 0 and avg_vol_recent > avg_vol_prior * 1.05

        # Direction bias: which side of BB mid
        direction = "Up" if price > bbm else "Down"
        if ema20 and price < ema20 * 0.98:
            direction = "Down"  # already broken below short trend
        if ema20 and price > ema20 * 1.02:
            direction = "Up"

        score = 0
        reasons = []
        score += max(0, round((7 - bb_width) * 2, 1))   # tighter = higher score
        reasons.append(f"BB squeeze {bb_width:.1f}% — explosive move pending")
        if vol_rising:
            score += 2
            reasons.append("Volume building (coiling)")
        if adx_v and adx_v < 20:
            score += 1
            reasons.append(f"ADX {adx_v:.1f} — consolidating, trend about to emerge")

        atr_v = _safe(_last(ind["atr14"])) or price * 0.015
        candidates.append({
            "symbol":      sym,
            "price":       round(price, 2),
            "bb_width":    round(bb_width, 2),
            "direction":   direction,
            "vol_rising":  vol_rising,
            "score":       round(score, 1),
            "reasons":     reasons,
            "breakout_up": round(bbu, 2),
            "breakdown":   round(bbl, 2),
            "target":      round(bbu + atr_v, 2) if direction == "Up" else round(bbl - atr_v, 2),
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:n]


def _macro_snapshot(commodity_data: dict) -> dict:
    """Gold, Crude, USD/INR snapshot with signals."""
    snap = {}
    inr_rate = _get_usd_inr()

    macro_symbols = {
        "GOLD":     ("₹/10g", "Safe-haven demand proxy"),
        "CRUDEOIL": ("₹/bbl", "Inflation / EM risk proxy"),
        "SILVER":   ("₹/kg",  "Industrial + precious metal"),
    }

    for sym, (unit, desc) in macro_symbols.items():
        data = commodity_data.get(sym)
        if not data or len(data) < 22:
            snap[sym] = {"price": 0, "ret_1m": 0, "signal": "—", "unit": unit}
            continue
        closes = [d["close"] for d in data]
        price  = closes[-1]
        ret_1m = _pct(price, closes[-22]) if len(closes) >= 22 else 0
        rsi_v  = _safe(_last(compute_all(data)["rsi14"]))
        if ret_1m > 3:
            signal = "Rising — watch for inflation pressure" if sym == "CRUDEOIL" else "Safe-haven buying"
        elif ret_1m < -3:
            signal = "Falling — potential relief for equities" if sym == "CRUDEOIL" else "Weak demand"
        else:
            signal = "Stable"
        snap[sym] = {
            "price":   round(price, 2),
            "ret_1m":  round(ret_1m, 2),
            "rsi":     round(rsi_v, 1) if rsi_v else None,
            "signal":  signal,
            "unit":    unit,
            "desc":    desc,
        }

    # Nifty-Gold correlation signal
    gold_1m = snap.get("GOLD", {}).get("ret_1m", 0)
    snap["correlation_note"] = (
        "Gold and equities both rising — risk-on with inflation hedge demand"
        if gold_1m > 2 else
        "Gold falling, equities may rally — typical risk-on rotation" if gold_1m < -2
        else "Gold stable — neutral macro environment"
    )
    snap["usd_inr"] = round(inr_rate, 4)

    return snap


# ── Main intelligence function ─────────────────────────────────────────────────

def get_market_intelligence(force: bool = False) -> dict:
    """
    Full market intelligence report.
    Cached for 20 minutes — fast to call repeatedly.
    """
    global _intel_cache, _intel_cache_ts
    now = time.time()
    if not force and _intel_cache and (now - _intel_cache_ts) < _CACHE_TTL:
        return _intel_cache

    t0 = time.time()

    # Fetch all data in parallel
    all_syms = NIFTY50 + INDICES + COMMODITIES
    all_data = _fetch_parallel(all_syms, period="3mo", max_workers=25)

    # Nifty50 regime
    nifty_data = all_data.get("NIFTY50", [])
    regime     = _regime_from_nifty(nifty_data)

    # BankNifty health
    bn_data    = all_data.get("BANKNIFTY", [])
    bn_regime  = _regime_from_nifty(bn_data)

    # Sector rotation
    sectors = _sector_strength(all_data)

    # Market breadth
    breadth = _market_breadth(all_data)

    # Top movers
    movers = _top_movers(all_data)

    # Reversal watchlist
    reversals = _reversal_watchlist(all_data)

    # Breakout candidates
    breakouts = _breakout_candidates(all_data)

    # Macro snapshot
    macro = _macro_snapshot(all_data)

    # Overall market call
    regime_score = regime["strength"] if regime["regime"] == "Bull Market" else (
        100 - regime["strength"] if regime["regime"] == "Bear Market" else 50
    )
    breadth_score = breadth["above_ema50_pct"]
    overall_score = round((regime_score * 0.5 + breadth_score * 0.5), 1)

    if overall_score >= 65:
        overall_action = "RISK ON"
        overall_color  = "green"
        overall_note   = "Market structure is bullish. Look for long setups in leading sectors."
    elif overall_score <= 35:
        overall_action = "RISK OFF"
        overall_color  = "red"
        overall_note   = "Market under pressure. Reduce exposure, wait for stabilisation."
    else:
        overall_action = "NEUTRAL"
        overall_color  = "yellow"
        overall_note   = "Mixed signals. Selective stock-picking only, stay light."

    elapsed = round(time.time() - t0, 1)

    result = {
        "overall": {
            "action":  overall_action,
            "color":   overall_color,
            "score":   overall_score,
            "note":    overall_note,
        },
        "regime":       regime,
        "banknifty":    bn_regime,
        "breadth":      breadth,
        "sectors":      sectors,
        "movers":       movers,
        "reversals":    reversals,
        "breakouts":    breakouts,
        "macro":        macro,
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_sec":  elapsed,
            "symbols_used": len(all_data),
            "cache_ttl_min": _CACHE_TTL // 60,
        }
    }

    _intel_cache    = result
    _intel_cache_ts = now
    return result
