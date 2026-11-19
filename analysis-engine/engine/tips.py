"""
Trade Tips Engine — generates actionable trade ideas automatically.
Types: Intraday | BTST | STBT | Positional | Options
No manual scan needed — runs on demand with live price injected.
"""
from __future__ import annotations
import math
from datetime import date, datetime, timedelta
from .indicators import compute_all, atr as calc_atr
from .fetcher import fetch_ohlcv

def _safe(v):
    try: return v if (v is not None and not math.isnan(float(v)) and not math.isinf(float(v))) else None
    except: return None

def _last(arr):
    for v in reversed(arr):
        if _safe(v) is not None: return v
    return None

def _snap(v, step):
    """Round to nearest step."""
    return round(round(v / step) * step, 2) if step else round(v, 2)

def _step(price):
    """Price-appropriate rounding step."""
    if price > 20000: return 50
    if price > 5000:  return 20
    if price > 1000:  return 5
    if price > 100:   return 1
    return 0.5

def _pct(a, b): return round((a - b) / b * 100, 2) if b else 0


# ── Indicator snapshot ────────────────────────────────────────────────────────
def _snapshot(data: list, live_price: float = None) -> dict:
    """Compute indicators and return a clean snapshot dict."""
    if live_price and data:
        data[-1]["close"] = live_price
    if not data or len(data) < 20:
        return {}
    ind = compute_all(data)
    closes = [d["close"] for d in data if _safe(d.get("close"))]
    price  = live_price or closes[-1]

    atr_arr = ind.get("atr14", [])
    atr_val = float(_last(atr_arr) or price * 0.012)

    ema20  = _safe(_last(ind.get("ema20",  [])))
    ema50  = _safe(_last(ind.get("ema50",  [])))
    ema200 = _safe(_last(ind.get("ema200", [])))
    rsi    = _safe(_last(ind.get("rsi14",  [])))

    macd_line, sig_line, hist = ind.get("macd", ([],[],[]))
    macd_val = _safe(_last(macd_line))
    macd_sig = _safe(_last(sig_line))
    macd_hist= _safe(_last(hist))

    bb = ind.get("bb", ([], [], []))
    bb_upper = _safe(_last(bb[0]))
    bb_mid   = _safe(_last(bb[1]))
    bb_lower = _safe(_last(bb[2]))

    st      = ind.get("supertrend", [])
    st_dir  = ind.get("st_direction", [])
    st_val  = _safe(_last(st))
    st_bull = (_last(st_dir) == 1)

    recent20 = data[-20:]
    pivot_h = max(d["high"]  for d in recent20 if _safe(d.get("high")))
    pivot_l = min(d["low"]   for d in recent20 if _safe(d.get("low")))
    pivot_c = closes[-1]
    pivot_p = (pivot_h + pivot_l + pivot_c) / 3
    s1 = 2 * pivot_p - pivot_h
    r1 = 2 * pivot_p - pivot_l

    week_data = data[-5:]
    week_high = max(d["high"] for d in week_data if _safe(d.get("high")))
    week_low  = min(d["low"]  for d in week_data if _safe(d.get("low")))

    vols = [d["volume"] for d in data if d.get("volume")]
    avg_vol = sum(vols[-20:]) / 20 if len(vols) >= 20 else (sum(vols) / len(vols) if vols else 1)
    cur_vol = vols[-1] if vols else 0
    vol_ratio = cur_vol / avg_vol if avg_vol else 1

    return {
        "price": price, "atr": atr_val,
        "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "rsi": rsi, "macd": macd_val, "macd_sig": macd_sig, "macd_hist": macd_hist,
        "bb_upper": bb_upper, "bb_mid": bb_mid, "bb_lower": bb_lower,
        "st_val": st_val, "st_bull": st_bull,
        "pivot": round(pivot_p), "s1": round(s1), "r1": round(r1),
        "week_high": round(week_high), "week_low": round(week_low),
        "vol_ratio": round(vol_ratio, 2),
        "prev_close": closes[-2] if len(closes) >= 2 else closes[-1],
        "day_change_pct": _pct(price, closes[-2]) if len(closes) >= 2 else 0,
    }


# ── Bias detector ─────────────────────────────────────────────────────────────
def _bias(s: dict) -> str:
    """Return 'bullish' | 'bearish' | 'neutral' based on indicator confluence."""
    price = s["price"]
    bull = 0; bear = 0
    if s["ema20"] and price > s["ema20"]: bull += 1
    elif s["ema20"] and price < s["ema20"]: bear += 1
    if s["ema50"] and price > s["ema50"]: bull += 1
    elif s["ema50"] and price < s["ema50"]: bear += 1
    if s["ema200"] and price > s["ema200"]: bull += 1
    elif s["ema200"] and price < s["ema200"]: bear += 1
    if s["rsi"] and s["rsi"] > 55: bull += 1
    elif s["rsi"] and s["rsi"] < 45: bear += 1
    if s["macd"] and s["macd_sig"] and s["macd"] > s["macd_sig"]: bull += 1
    elif s["macd"] and s["macd_sig"] and s["macd"] < s["macd_sig"]: bear += 1
    if s["st_bull"]: bull += 2
    else: bear += 2
    if bull >= 5: return "bullish"
    if bear >= 5: return "bearish"
    return "neutral"

# ── Next expiry helper ────────────────────────────────────────────────────────
def _next_expiry(weeks_ahead: int = 0) -> str:
    """Return next Thursday expiry date (NSE weekly)."""
    today = date.today()
    days_to_thu = (3 - today.weekday()) % 7
    if days_to_thu == 0 and datetime.now().hour >= 15:
        days_to_thu = 7  # after 3:30 PM, move to next week
    days_to_thu += weeks_ahead * 7
    exp = today + timedelta(days=days_to_thu)
    return exp.strftime("%d %b '%y").lstrip("0")

def _monthly_expiry() -> str:
    """Last Thursday of current month."""
    today = date.today()
    # Find last Thursday
    last_day = date(today.year, today.month % 12 + 1, 1) - timedelta(days=1) if today.month < 12 \
               else date(today.year + 1, 1, 1) - timedelta(days=1)
    while last_day.weekday() != 3:
        last_day -= timedelta(days=1)
    if last_day <= today:
        # Already passed — next month
        m = today.month % 12 + 1
        y = today.year if today.month < 12 else today.year + 1
        last_day = date(y, m % 12 + 1, 1) - timedelta(days=1) if m < 12 else date(y + 1, 1, 1) - timedelta(days=1)
        while last_day.weekday() != 3:
            last_day -= timedelta(days=1)
    return last_day.strftime("%d %b '%y").lstrip("0")


# ── Individual tip generators ─────────────────────────────────────────────────

def _intraday_tip(sym: str, s: dict, bias: str) -> dict | None:
    price = s["price"]; atr = s["atr"]; st = _step(price)

    if bias == "bullish":
        # Buy on dip near support
        entry_low  = _snap(max(s["s1"], price - atr * 0.4), st)
        entry_high = _snap(price - atr * 0.1, st)
        sl         = _snap(entry_low - atr * 0.4, st)
        tgt1       = _snap(price + atr * 0.8, st)
        tgt2       = _snap(s["r1"], st)
        breakout   = _snap(s["week_high"] + st, st)
        note       = (f"ST bullish. EMA aligned. "
                      f"Buy dips {entry_low}–{entry_high}. Breakout above {breakout} → tgt {_snap(breakout+atr*0.8,st)}")
        return {
            "type": "Intraday", "direction": "BUY",
            "entry": f"{entry_low}–{entry_high}",
            "sl": sl, "target1": tgt1, "target2": tgt2,
            "note": note, "confidence": 72 if s["vol_ratio"] > 1.2 else 60
        }
    elif bias == "bearish":
        entry_low  = _snap(price + atr * 0.1, st)
        entry_high = _snap(min(s["r1"], price + atr * 0.4), st)
        sl         = _snap(entry_high + atr * 0.4, st)
        tgt1       = _snap(price - atr * 0.8, st)
        tgt2       = _snap(s["s1"], st)
        note       = (f"ST bearish. Sell rise {entry_low}–{entry_high}. "
                      f"Breakdown below {_snap(s['week_low']-st,st)} → tgt {_snap(s['week_low']-atr*0.8,st)}")
        return {
            "type": "Intraday", "direction": "SELL",
            "entry": f"{entry_low}–{entry_high}",
            "sl": sl, "target1": tgt1, "target2": tgt2,
            "note": note, "confidence": 68 if s["vol_ratio"] > 1.2 else 58
        }
    else:
        # Range play
        buy_entry  = _snap(s["s1"] + st, st)
        sell_entry = _snap(s["r1"] - st, st)
        return {
            "type": "Intraday", "direction": "RANGE",
            "entry": f"Buy {buy_entry} / Sell {sell_entry}",
            "sl": _snap(s["s1"] - atr * 0.3, st),
            "target1": _snap(s["r1"] - st, st),
            "target2": _snap(s["r1"] + atr * 0.5, st),
            "note": f"Choppy. Range {s['s1']}–{s['r1']}. Trade only on clear breakout.",
            "confidence": 45
        }

def _btst_tip(sym: str, s: dict, bias: str) -> dict | None:
    """Buy Today Sell Tomorrow — momentum continuation."""
    price = s["price"]; atr = s["atr"]; st = _step(price)
    # BTST only in bullish bias with volume confirmation
    if bias != "bullish": return None
    if s["rsi"] and s["rsi"] > 75: return None  # overbought — skip
    if s["vol_ratio"] < 1.0: return None  # no conviction
    entry = _snap(price + st, st)  # buy on breakout above current price
    sl    = _snap(price - atr * 0.6, st)
    tgt1  = _snap(price + atr * 1.2, st)
    tgt2  = _snap(price + atr * 2.0, st)
    exp   = _next_expiry(0)
    return {
        "type": "BTST", "direction": "BUY",
        "entry": f"{entry}",
        "sl": sl, "target1": tgt1, "target2": tgt2,
        "note": (f"Strong close + volume {s['vol_ratio']:.1f}x avg. "
                 f"Buy above {entry}, hold overnight. SL {sl}. "
                 f"Book partial at {tgt1}, trail rest."),
        "confidence": min(85, 55 + int(s["vol_ratio"] * 10))
    }

def _stbt_tip(sym: str, s: dict, bias: str) -> dict | None:
    """Sell Today Buy Tomorrow — weakness continuation."""
    price = s["price"]; atr = s["atr"]; st = _step(price)
    if bias != "bearish": return None
    if s["rsi"] and s["rsi"] < 25: return None  # oversold — skip
    if s["vol_ratio"] < 1.0: return None
    entry = _snap(price - st, st)
    sl    = _snap(price + atr * 0.6, st)
    tgt1  = _snap(price - atr * 1.2, st)
    tgt2  = _snap(price - atr * 2.0, st)
    return {
        "type": "STBT", "direction": "SELL",
        "entry": f"{entry}",
        "sl": sl, "target1": tgt1, "target2": tgt2,
        "note": (f"Weak close + volume {s['vol_ratio']:.1f}x avg. "
                 f"Short below {entry}, hold overnight. SL {sl}. "
                 f"Cover partial at {tgt1}."),
        "confidence": min(82, 55 + int(s["vol_ratio"] * 10))
    }


def _positional_tip(sym: str, s: dict, bias: str) -> dict | None:
    price = s["price"]; atr = s["atr"]; st = _step(price)
    if bias == "bullish":
        entry = _snap(price, st)
        sl    = _snap(max(s["ema50"] or price * 0.95, price - atr * 2), st)
        tgt1  = _snap(price + atr * 3, st)
        tgt2  = _snap(price + atr * 5, st)
        ema50_str  = f"{s['ema50']:.0f}"  if s.get('ema50')  else 'N/A'
        ema200_str = f"{s['ema200']:.0f}" if s.get('ema200') else 'N/A'
        ema20_str  = f"{s['ema20']:.0f}"  if s.get('ema20')  else 'N/A'
        return {
            "type": "Positional (1–4 wks)", "direction": "BUY",
            "entry": f"{entry}",
            "sl": sl, "target1": tgt1, "target2": tgt2,
            "note": (f"Above EMA50 {ema50_str} and EMA200 {ema200_str}. "
                     f"Weekly structure bullish. Hold above {sl}. "
                     f"Accumulate on dips to EMA20 {ema20_str}."),
            "confidence": 65
        }
    elif bias == "bearish":
        entry = _snap(price, st)
        sl    = _snap(min(s["ema50"] or price * 1.05, price + atr * 2), st)
        tgt1  = _snap(price - atr * 3, st)
        tgt2  = _snap(price - atr * 5, st)
        ema50_str2 = f"{s['ema50']:.0f}" if s.get('ema50') else 'N/A'
        return {
            "type": "Positional (1–4 wks)", "direction": "SELL/AVOID",
            "entry": f"{entry}",
            "sl": sl, "target1": tgt1, "target2": tgt2,
            "note": (f"Below EMA50 {ema50_str2}. "
                     f"Avoid fresh longs. Short opportunities on bounce to EMA20."),
            "confidence": 60
        }
    return None


def _options_tip(sym: str, s: dict, bias: str) -> dict | None:
    """
    Generate options tip with specific strike, expiry, premium range, SL, target.
    Only for index/F&O symbols.
    """
    FNO = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX",
           "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","SBIN",
           "BAJFINANCE","TATAMOTORS","AXISBANK","KOTAKBANK","LT",
           "HINDUNILVR","ITC","BHARTIARTL","WIPRO","HCLTECH"}
    if sym.upper() not in FNO:
        return None

    price = s["price"]; atr = s["atr"]

    # Strike selection
    INDEX_STEP = {"NIFTY":50,"BANKNIFTY":100,"FINNIFTY":50,"MIDCPNIFTY":25,"SENSEX":100}
    strike_step = INDEX_STEP.get(sym.upper(), 50 if price > 10000 else 20)

    if bias == "bullish":
        # Buy slightly OTM call — 1 strike above ATM
        atm = round(price / strike_step) * strike_step
        strike = atm + strike_step
        opt_type = "CE"
        # Premium estimate based on ATR and moneyness, NOT full index price
        # OTM CE 1 strike away ≈ ATR * 0.35–0.50 for weekly
        otm_dist = strike - price          # how far OTM
        premium_est = max(
            round(atr * 0.40),             # time value component
            round(max(0, atm - otm_dist))  # intrinsic if ITM
        )
        # Clamp to realistic range per instrument
        max_prem = {"BANKNIFTY": 800, "NIFTY": 300, "FINNIFTY": 200,
                    "MIDCPNIFTY": 150, "SENSEX": 500}.get(sym.upper(), 500)
        premium_est = min(premium_est, max_prem)
        premium_est = max(premium_est, 20)  # floor
        sl_prem   = round(premium_est * 0.50)   # SL at 50% loss
        tgt1_prem = round(premium_est * 1.60)   # target 1 = 60% gain
        tgt2_prem = round(premium_est * 2.20)   # target 2 = 120% gain
        expiry = _next_expiry(0)                # current week expiry
        entry_lo = round(premium_est * 0.93)
        entry_hi = round(premium_est * 1.07)
        note = (f"Bullish setup. Buy {sym} {strike} {opt_type} "
                f"{expiry} @ {entry_lo}–{entry_hi}. "
                f"SL {sl_prem}. Tgt {tgt1_prem}–{tgt2_prem}. "
                f"Hold if {sym} stays above {round(atm)}.")
    elif bias == "bearish":
        atm = round(price / strike_step) * strike_step
        strike = atm - strike_step
        opt_type = "PE"
        otm_dist = price - strike
        premium_est = max(
            round(atr * 0.40),
            round(max(0, otm_dist - (price - atm)))
        )
        max_prem = {"BANKNIFTY": 800, "NIFTY": 300, "FINNIFTY": 200,
                    "MIDCPNIFTY": 150, "SENSEX": 500}.get(sym.upper(), 500)
        premium_est = min(premium_est, max_prem)
        premium_est = max(premium_est, 20)
        sl_prem   = round(premium_est * 0.50)
        tgt1_prem = round(premium_est * 1.60)
        tgt2_prem = round(premium_est * 2.20)
        expiry = _next_expiry(0)
        entry_lo = round(premium_est * 0.93)
        entry_hi = round(premium_est * 1.07)
        note = (f"Bearish setup. Buy {sym} {strike} {opt_type} "
                f"{expiry} @ {entry_lo}–{entry_hi}. "
                f"SL {sl_prem}. Tgt {tgt1_prem}–{tgt2_prem}. "
                f"Hold if {sym} stays below {round(atm)}.")
    else:
        # Neutral — sell straddle / strangle
        atm = round(price / strike_step) * strike_step
        # ATM straddle premium ≈ ATR * 0.65 (both CE+PE combined)
        half_prem = round(atr * 0.33)
        max_half  = {"BANKNIFTY": 500, "NIFTY": 200, "FINNIFTY": 150,
                     "MIDCPNIFTY": 100, "SENSEX": 350}.get(sym.upper(), 300)
        half_prem = min(max(half_prem, 15), max_half)
        ce_prem   = half_prem
        pe_prem   = half_prem
        total_prem = ce_prem + pe_prem
        expiry = _next_expiry(0)
        note = (f"Rangebound. Sell {sym} {atm} Straddle "
                f"{expiry} (CE+PE @ {total_prem}). "
                f"Max profit if {sym} stays {atm-strike_step}–{atm+strike_step}. "
                f"SL if premium doubles.")
        return {
            "type": "Options – Straddle Sell", "direction": "NEUTRAL",
            "entry": f"Sell {atm} CE+PE @ {total_prem}",
            "sl": total_prem + round(half_prem * 0.5),
            "target1": round(total_prem * 0.5),
            "target2": round(total_prem * 0.3),
            "strike": atm, "expiry": expiry, "opt_type": "STRADDLE",
            "note": note, "confidence": 55
        }

    return {
        "type": f"Options – {opt_type} Buy", "direction": "BUY " + opt_type,
        "entry": f"{entry_lo}–{entry_hi}",
        "sl": sl_prem, "target1": tgt1_prem, "target2": tgt2_prem,
        "strike": strike, "expiry": expiry, "opt_type": opt_type,
        "note": note,
        "confidence": 65 if bias != "neutral" else 50
    }


# ── Main tip generator ────────────────────────────────────────────────────────

def generate_tips(symbol: str, data: list = None, live_price: float = None) -> dict:
    """Generate all tip types for a symbol."""
    sym = symbol.upper()
    if data is None:
        data = fetch_ohlcv(sym, period="3mo")
    if not data or len(data) < 20:
        return {"symbol": sym, "error": f"Insufficient data for {sym}"}

    data = [d for d in data if _safe(d.get("close")) and _safe(d.get("high")) and _safe(d.get("low"))]
    if len(data) < 20:
        return {"symbol": sym, "error": "Too many null bars"}

    s = _snapshot(data, live_price)
    if not s:
        return {"symbol": sym, "error": "Indicator computation failed"}

    bias = _bias(s)

    tips = []
    for fn in [_intraday_tip, _btst_tip, _stbt_tip, _positional_tip, _options_tip]:
        try:
            t = fn(sym, s, bias)
            if t:
                t["symbol"] = sym
                tips.append(t)
        except Exception as e:
            pass

    # Format a clean text summary for each tip
    for t in tips:
        t["text"] = _format_tip(sym, t, s)

    return {
        "symbol":    sym,
        "price":     s["price"],
        "bias":      bias,
        "rsi":       s["rsi"],
        "st_bull":   s["st_bull"],
        "atr":       round(s["atr"], 2),
        "vol_ratio": s["vol_ratio"],
        "levels":    {"s1": s["s1"], "r1": s["r1"], "pivot": s["pivot"],
                      "week_high": s["week_high"], "week_low": s["week_low"],
                      "ema20": round(s["ema20"]) if s["ema20"] else None,
                      "ema50": round(s["ema50"]) if s["ema50"] else None},
        "tips":      tips,
        "generated": datetime.now().strftime("%d %b %Y %H:%M"),
    }


def _format_tip(sym: str, t: dict, s: dict) -> str:
    """Format a single tip as a clean, broker-style text message."""
    dir_emoji = {"BUY": "🟢", "SELL": "🔴", "BUY CE": "🟢", "BUY PE": "🔴",
                 "SELL/AVOID": "🔴", "NEUTRAL": "🟡", "RANGE": "🟡"}.get(
                 t["direction"].split()[0], "🔵")
    lines = [
        f"{dir_emoji} #{sym}  {t['type'].upper()}",
        f"Direction: {t['direction']}",
        f"Entry: {t['entry']}",
        f"SL: {t['sl']}",
        f"Target 1: {t['target1']}   Target 2: {t.get('target2', '—')}",
    ]
    if t.get("strike"): lines.append(f"Strike: {t['strike']} {t.get('opt_type','')}  Expiry: {t.get('expiry','')}")
    lines += [f"Confidence: {t.get('confidence', 50)}%", "", t.get("note", "")]
    rsi_str = f"{s['rsi']:.1f}" if s.get('rsi') else "—"
    lines.append(f"RSI {rsi_str} | ATR {s['atr']:.1f} | Vol {s['vol_ratio']:.1f}x avg")
    lines.append("⚠ For education only. Always use your own judgement and position sizing.")
    return "\n".join(lines)


# ── Batch ─────────────────────────────────────────────────────────────────────

def generate_batch_tips(symbols: list, live_prices: dict = None, max_workers: int = 6) -> list:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(sym):
        lp = (live_prices or {}).get(sym)
        return generate_tips(sym, live_price=lp)

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_one, s): s for s in symbols}
        for fut in as_completed(futs):
            try: results.append(fut.result())
            except Exception as e: results.append({"symbol": futs[fut], "error": str(e)})

    results.sort(key=lambda x: x.get("symbol", ""))
    return results
