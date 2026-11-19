"""
Daily Market Briefing Engine
Generates trader-style briefings for indices, commodities, and stocks.
Format mirrors professional tip channels: range, bias, key levels, trade setup, narrative.
"""
from __future__ import annotations
import math
from datetime import date, datetime
from .indicators import compute_all, ema as calc_ema, atr as calc_atr
from .fetcher import fetch_ohlcv


def _safe(v):
    try:
        return v if (v is not None and not math.isnan(float(v)) and not math.isinf(float(v))) else None
    except Exception:
        return None


def _last(arr):
    for v in reversed(arr):
        if _safe(v) is not None:
            return v
    return None


def _prev(arr, n=1):
    vals = [v for v in arr if _safe(v) is not None]
    return vals[-1 - n] if len(vals) > n else None


def _round(v, d=0):
    if v is None: return None
    return round(float(v), d)


def _pct(a, b):
    if not b: return 0
    return round((a - b) / b * 100, 2)


# ── Intraday range estimator ──────────────────────────────────────────────────

def _intraday_range(data: list, atr_val: float, price: float) -> tuple:
    """
    Estimate today's intraday range using:
    - Last 5-day average true range
    - Recent pivot levels
    - ATR-based expansion from yesterday's close
    Returns (low_range, high_range) rounded to nearest 10 for indices, 5 for stocks
    """
    closes = [d["close"] for d in data if _safe(d.get("close"))]
    highs  = [d["high"]  for d in data if _safe(d.get("high"))]
    lows   = [d["low"]   for d in data if _safe(d.get("low"))]

    prev_close = closes[-1] if closes else price
    prev_high  = highs[-1]  if highs  else price * 1.01
    prev_low   = lows[-1]   if lows   else price * 0.99

    # Pivot-based range
    pivot = (prev_high + prev_low + prev_close) / 3
    s1 = 2 * pivot - prev_high
    r1 = 2 * pivot - prev_low
    s2 = pivot - (prev_high - prev_low)
    r2 = pivot + (prev_high - prev_low)

    # ATR-based range (0.6x ATR each side for intraday)
    atr_range = atr_val * 0.65

    intra_low  = max(prev_close - atr_range, s1)
    intra_high = min(prev_close + atr_range, r1)

    # Snap to round numbers
    snap = 50 if price > 10000 else 20 if price > 1000 else 5
    intra_low  = round(intra_low  / snap) * snap
    intra_high = round(intra_high / snap) * snap

    return intra_low, intra_high, round(s1), round(r1), round(s2), round(r2), round(pivot)


def _weekly_range(data: list) -> tuple:
    """Estimate weekly range from last 4 weeks of data."""
    if len(data) < 10:
        return None, None
    week = data[-5:]
    w_low  = min(d["low"]  for d in week if _safe(d.get("low")))
    w_high = max(d["high"] for d in week if _safe(d.get("high")))
    prev_week = data[-10:-5]
    if prev_week:
        pw_low  = min(d["low"]  for d in prev_week if _safe(d.get("low")))
        pw_high = max(d["high"] for d in prev_week if _safe(d.get("high")))
        # Project next week range as avg of last 2 weeks width + current close
        closes = [d["close"] for d in data if _safe(d.get("close"))]
        c = closes[-1]
        avg_range = ((w_high - w_low) + (pw_high - pw_low)) / 2
        snap = 50 if c > 10000 else 20 if c > 1000 else 5
        wk_low  = round((c - avg_range * 0.5) / snap) * snap
        wk_high = round((c + avg_range * 0.5) / snap) * snap
        return wk_low, wk_high
    return round(w_low), round(w_high)


def _structure(data: list, ind: dict, price: float) -> dict:
    """
    Determine market structure: uptrend / downtrend / consolidation / recovery.
    Returns {bias, key_level, key_level_label, structure_note}
    """
    closes = [d["close"] for d in data if _safe(d.get("close"))]
    ema20  = _last(ind["ema20"])
    ema50  = _last(ind["ema50"])
    ema200 = _last(ind["ema200"])
    rsi    = _last(ind["rsi14"])

    # Count last 5 and 10 day closes above/below ema20
    recent5  = closes[-5:]
    above20  = sum(1 for c in recent5  if ema20 and c > ema20)
    below20  = sum(1 for c in recent5  if ema20 and c < ema20)

    # Swing high/low in last 20 bars
    recent20 = data[-20:]
    sh = max(d["high"]  for d in recent20 if _safe(d.get("high")))
    sl = min(d["low"]   for d in recent20 if _safe(d.get("low")))
    prev10 = data[-30:-10] if len(data) >= 30 else data[:10]
    sh_prev = max(d["high"] for d in prev10 if _safe(d.get("high"))) if prev10 else sh
    sl_prev = min(d["low"]  for d in prev10 if _safe(d.get("low")))  if prev10 else sl

    # Determine bias
    hh_hl = sh > sh_prev and sl > sl_prev   # higher highs and higher lows
    lh_ll = sh < sh_prev and sl < sl_prev   # lower highs and lower lows

    above_ema200 = bool(ema200 and price > ema200)
    above_ema50  = bool(ema50  and price > ema50)
    above_ema20  = bool(ema20  and price > ema20)

    bull_factors = sum([hh_hl, above_ema200, above_ema50, above_ema20, above20 >= 4])
    bear_factors = sum([lh_ll, not above_ema200, not above_ema50, not above_ema20, below20 >= 4])

    if bull_factors >= 3:
        bias = "Bullish"
    elif bear_factors >= 3:
        bias = "Bearish"
    elif rsi and rsi < 40:
        bias = "Oversold / Recovery Likely"
    elif rsi and rsi > 70:
        bias = "Overbought / Caution"
    else:
        bias = "Neutral / Indecisive"

    # Key level to watch
    if ema50:
        key_level = _round(ema50)
        key_label = "EMA50"
    elif ema20:
        key_level = _round(ema20)
        key_label = "EMA20"
    else:
        key_level = _round(closes[-5]) if len(closes) >= 5 else _round(price * 0.99)
        key_label = "5-day close"

    # Structure note
    if hh_hl:
        note = "Higher highs and higher lows — uptrend structure intact"
    elif lh_ll:
        note = "Lower highs and lower lows — downtrend structure intact"
    elif above_ema50 and not above_ema20:
        note = "Price pulled back below EMA20 within uptrend — potential buy-on-dip zone"
    elif not above_ema50 and above_ema20:
        note = "Price bouncing but below EMA50 — recovery attempt, not confirmed reversal"
    else:
        note = "Range-bound consolidation — breakout direction awaited"

    return {
        "bias": bias,
        "key_level": key_level,
        "key_level_label": key_label,
        "structure_note": note,
        "ema20": _round(ema20),
        "ema50": _round(ema50),
        "ema200": _round(ema200),
        "rsi": _round(rsi, 1),
    }


def _trade_setup(price: float, intra_low: float, intra_high: float,
                 s1: float, r1: float, atr: float,
                 bias: str, action: str) -> dict:
    """
    Generate a specific trade setup with entry, SL, targets.
    Mirrors the format: "Buy on dips to X+/-Y. Tgt Z SL W"
    """
    snap = 50 if price > 10000 else 20 if price > 1000 else 5

    if "Bullish" in bias or action == "BUY":
        entry_zone = intra_low
        entry_buffer = max(snap, round(atr * 0.15 / snap) * snap)
        sl = max(round((intra_low - atr * 0.5) / snap) * snap, s1 - snap)
        tgt1 = round((price + atr * 1.2) / snap) * snap
        tgt2 = intra_high
        setup_type = "Buy on dip"
        setup_text = (
            f"Buy on dips to {entry_zone}±{entry_buffer}pts. "
            f"Tgt {tgt1}/{tgt2}  SL {sl}"
        )
        breakout_entry = round((intra_high + snap) / snap) * snap
        breakout_text  = f"Momentum Buy above {breakout_entry} (15m close) → Tgt {round((breakout_entry + atr*1.5)/snap)*snap}  SL {round((breakout_entry-atr*0.8)/snap)*snap}"
    elif "Bearish" in bias or action == "SELL":
        entry_zone = intra_high
        entry_buffer = max(snap, round(atr * 0.15 / snap) * snap)
        sl = round((intra_high + atr * 0.5) / snap) * snap
        tgt1 = round((price - atr * 1.2) / snap) * snap
        tgt2 = intra_low
        setup_type = "Sell on rise"
        setup_text = (
            f"Sell on rise to {entry_zone}±{entry_buffer}pts. "
            f"Tgt {tgt1}/{tgt2}  SL {sl}"
        )
        breakout_entry = round((intra_low - snap) / snap) * snap
        breakout_text  = f"Breakdown below {breakout_entry} (15m close) → Tgt {round((breakout_entry-atr*1.5)/snap)*snap}  SL {round((breakout_entry+atr*0.8)/snap)*snap}"
    else:
        # Neutral — range play
        entry_low  = round((intra_low  + atr * 0.1) / snap) * snap
        entry_high = round((intra_high - atr * 0.1) / snap) * snap
        sl_low  = round((intra_low  - atr * 0.3) / snap) * snap
        sl_high = round((intra_high + atr * 0.3) / snap) * snap
        setup_type = "Range play"
        setup_text = (
            f"Range: {entry_low}–{entry_high}. "
            f"Buy near {entry_low} SL {sl_low} | Sell near {entry_high} SL {sl_high}"
        )
        breakout_entry = entry_high
        breakout_text  = f"Breakout above {intra_high} or breakdown below {intra_low} for momentum trade"
        entry_zone = entry_low

    # Collect return values — each branch must set these
    if "Bullish" in bias or action == "BUY":
        ret_sl = sl;  ret_t1 = tgt1;  ret_t2 = tgt2
    elif "Bearish" in bias or action == "SELL":
        ret_sl = sl;  ret_t1 = tgt1;  ret_t2 = tgt2
    else:
        ret_sl = sl_low;  ret_t1 = entry_high;  ret_t2 = entry_low

    return {
        "type":           setup_type,
        "setup":          setup_text,
        "breakout_setup": breakout_text,
        "entry_zone":     entry_zone,
        "sl":             ret_sl,
        "target1":        ret_t1,
        "target2":        ret_t2,
    }


# ── Main briefing generator ───────────────────────────────────────────────────

def generate_briefing(symbol: str, data: list = None) -> dict:
    """
    Generate a full daily briefing for a symbol.
    If data is not provided, fetches via yfinance (3 months).
    """
    if data is None:
        data = fetch_ohlcv(symbol, period="3mo")
    if not data or len(data) < 20:
        return {"error": f"Insufficient data for {symbol}"}

    # Clean data
    data = [d for d in data
            if _safe(d.get("close")) and _safe(d.get("high")) and _safe(d.get("low"))]
    if len(data) < 20:
        return {"error": f"Too many null bars for {symbol}"}

    closes = [d["close"] for d in data]
    price  = closes[-1]
    prev_close = closes[-2] if len(closes) >= 2 else price
    day_chg    = _pct(price, prev_close)

    ind = compute_all(data)
    atr_arr = ind.get("atr14", [])
    atr_val = float(_last(atr_arr) or price * 0.012)

    # Ranges
    intra_low, intra_high, s1, r1, s2, r2, pivot = _intraday_range(data[:-1], atr_val, price)
    wk_low, wk_high = _weekly_range(data)

    # Structure + bias
    struct = _structure(data, ind, price)
    bias   = struct["bias"]

    # Action from advisor confidence
    from .advisor import advise
    adv = advise(data, symbol)
    action = adv.get("action", "HOLD")
    confidence = adv.get("confidence", 50)

    # Trade setup
    setup = _trade_setup(price, intra_low, intra_high, s1, r1, atr_val, bias, action)

    # Opening sentiment
    chg_desc = (
        f"opens flat" if abs(day_chg) < 0.2 else
        f"opens higher by {day_chg:.1f}%" if day_chg > 0 else
        f"opens lower by {abs(day_chg):.1f}%"
    )

    # Key level narrative
    key_lv = struct["key_level"]
    key_lb = struct["key_level_label"]
    if "Bullish" in bias:
        key_note = f"As long as {symbol} holds above {key_lv} ({key_lb}), bulls remain in control"
    elif "Bearish" in bias:
        key_note = f"Recovery hopes only if {symbol} reclaims {key_lv} ({key_lb}) on closing basis"
    else:
        key_note = f"Watch {key_lv} ({key_lb}) — close above = bullish, close below = bearish"

    # Weekly close warning
    weekly_warn = None
    if wk_low and price < wk_low * 1.01:
        weekly_warn = f"Weekly close below {wk_low} will warrant re-test of {s2} levels"
    elif wk_high and price > wk_high * 0.99:
        weekly_warn = f"Weekly close above {wk_high} opens up {r2} target"

    # Supertrend signal
    st      = ind.get("supertrend", [])
    st_dir  = ind.get("st_direction", [])
    st_last = _last(st)
    dir_last = _last(st_dir)
    dir_prev = _prev(st_dir)
    st_note  = None
    if dir_last and dir_prev and dir_last != dir_prev:
        st_note = f"⚡ Supertrend just flipped {'BULLISH 🟢' if dir_last == 1 else 'BEARISH 🔴'} at {_round(st_last)}"
    elif dir_last == 1:
        st_note = f"Supertrend bullish — support at {_round(st_last)}"
    elif dir_last == -1:
        st_note = f"Supertrend bearish — resistance at {_round(st_last)}"

    today = datetime.now().strftime("%-d %b" if __import__('os').name != 'nt' else "%d %b").lstrip('0')

    briefing_text = _format_briefing(
        symbol=symbol, date_str=today, price=price, day_chg=day_chg,
        chg_desc=chg_desc, bias=bias, struct_note=struct["structure_note"],
        intra_low=intra_low, intra_high=intra_high,
        wk_low=wk_low, wk_high=wk_high,
        key_note=key_note, weekly_warn=weekly_warn,
        setup=setup, st_note=st_note,
        rsi=struct["rsi"], ema20=struct["ema20"], ema50=struct["ema50"],
        action=action, confidence=confidence
    )

    return {
        "symbol":       symbol,
        "price":        price,
        "day_change":   day_chg,
        "date":         today,
        "bias":         bias,
        "action":       action,
        "confidence":   confidence,
        "intraday_range": {"low": intra_low, "high": intra_high},
        "weekly_range":   {"low": wk_low,   "high": wk_high},
        "levels": {
            "pivot": pivot, "s1": s1, "r1": r1, "s2": s2, "r2": r2,
            "ema20": struct["ema20"], "ema50": struct["ema50"], "ema200": struct["ema200"],
            "st": _round(st_last),
        },
        "structure":    struct["structure_note"],
        "key_note":     key_note,
        "weekly_warn":  weekly_warn,
        "st_note":      st_note,
        "setup":        setup,
        "rsi":          struct["rsi"],
        "atr":          _round(atr_val, 2),
        "briefing_text": briefing_text,
        "advisor":      adv,
    }


def _format_briefing(symbol, date_str, price, day_chg, chg_desc, bias,
                     struct_note, intra_low, intra_high, wk_low, wk_high,
                     key_note, weekly_warn, setup, st_note,
                     rsi, ema20, ema50, action, confidence) -> str:
    """Format the briefing as a clean readable text block."""
    chg_arrow = "▲" if day_chg >= 0 else "▼"
    bias_emoji = {"Bullish":"🟢","Bearish":"🔴","Neutral / Indecisive":"🟡",
                  "Oversold / Recovery Likely":"🟡","Overbought / Caution":"🟠"}.get(bias, "🟡")

    lines = [
        f"#{symbol}  {date_str}",
        f"{chg_arrow} {abs(day_chg):.2f}%  ₹{price:.2f}",
        "",
        struct_note + ".",
        "",
        f"Intraday range: {intra_low}–{intra_high}",
    ]
    if wk_low and wk_high:
        lines.append(f"Weekly range:   {wk_low}–{wk_high}")
    lines.append("")
    lines.append(f"➡️ {setup['setup']}")
    if setup.get("breakout_setup"):
        lines.append(f"🚀 {setup['breakout_setup']}")
    lines.append("")
    lines.append(key_note + ".")
    if weekly_warn:
        lines.append(weekly_warn + ".")
    if st_note:
        lines.append(st_note + ".")
    lines.append("")
    lines.append(f"RSI {rsi}  |  EMA20 {ema20}  |  EMA50 {ema50}")
    lines.append(f"Bias: {bias_emoji} {bias}  |  Signal: {action} ({confidence}% confidence)")
    lines.append("")
    lines.append("📍 Use strict SL. No carry-forward if volatile.")

    return "\n".join(lines)


# ── Batch briefing for multiple symbols ───────────────────────────────────────

def generate_batch(symbols: list, max_workers: int = 6) -> list:
    """Generate briefings for multiple symbols in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(generate_briefing, sym): sym for sym in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                r = fut.result()
                results.append(r)
            except Exception as e:
                results.append({"symbol": sym, "error": str(e)})
    # Sort by symbol name
    results.sort(key=lambda x: x.get("symbol",""))
    return results
