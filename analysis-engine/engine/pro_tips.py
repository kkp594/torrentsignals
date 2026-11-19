"""
Professional Market Tips Engine
================================
Generates tips in the style of premium trading channels:
  - Range-based levels (support1/support2 - resistance1/resistance2)
  - 15m timeframe triggers
  - VIX / BankNifty cross-reference
  - Weekly expiry context
  - "No trading zone" discipline
  - Positional + Intraday + Options in one note

Format mirrors reference tips exactly.
"""
from __future__ import annotations
import math
from datetime import date, datetime, timedelta
from .indicators import compute_all
from .fetcher import fetch_ohlcv

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(v):
    try: return v if (v is not None and not math.isnan(float(v))
                      and not math.isinf(float(v))) else None
    except: return None

def _last(arr):
    for v in reversed(arr):
        if _safe(v) is not None: return float(v)
    return None

def _snap(v, step):
    return int(round(v / step) * step) if step else round(v)

def _step(p):
    if p > 20000: return 50
    if p > 5000:  return 25
    if p > 1000:  return 10
    if p > 100:   return 5
    return 1

def _pct(a, b): return round((a - b) / b * 100, 2) if b else 0

def _is_expiry_week() -> bool:
    """True if today is Thursday (weekly expiry day)."""
    return date.today().weekday() == 3

def _is_expiry_day() -> bool:
    return date.today().weekday() == 3

def _next_thursday() -> str:
    today = date.today()
    days  = (3 - today.weekday()) % 7
    if days == 0: days = 7
    return (today + timedelta(days=days)).strftime("%d%b").upper()

def _month_label() -> str:
    return date.today().strftime("%b").upper()

# ── Core indicator snapshot ───────────────────────────────────────────────────

def _build_snapshot(data: list, live_price: float = None) -> dict | None:
    if not data or len(data) < 30:
        return None
    if live_price:
        data[-1]["close"] = live_price

    closes = [d["close"] for d in data if _safe(d.get("close"))]
    highs  = [d["high"]  for d in data if _safe(d.get("high"))]
    lows   = [d["low"]   for d in data if _safe(d.get("low"))]
    if len(closes) < 20: return None

    price = closes[-1]
    ind   = compute_all(data)

    atr   = float(_last(ind.get("atr14", [])) or price * 0.012)
    ema20 = _safe(_last(ind.get("ema20", [])))
    ema50 = _safe(_last(ind.get("ema50", [])))
    ema200= _safe(_last(ind.get("ema200",[])))
    rsi   = _safe(_last(ind.get("rsi14", [])))

    macd_line, sig_line, hist = ind.get("macd", ([], [], []))
    macd_val  = _safe(_last(macd_line))
    macd_sig  = _safe(_last(sig_line))
    macd_hist = _safe(_last(hist))

    st_dir = ind.get("st_direction", [])
    st_bull= (_last(st_dir) == 1)

    # Pivots from last session
    ph = highs[-2]  if len(highs)  >= 2 else highs[-1]
    pl = lows[-2]   if len(lows)   >= 2 else lows[-1]
    pc = closes[-2] if len(closes) >= 2 else closes[-1]
    pivot  = (ph + pl + pc) / 3
    r1 = 2 * pivot - pl
    s1 = 2 * pivot - ph
    r2 = pivot + (ph - pl)
    s2 = pivot - (ph - pl)

    # Week range (last 5 sessions)
    wh = max(highs[-5:])
    wl = min(lows[-5:])

    # 20-day swing
    sh20 = max(highs[-20:])
    sl20 = min(lows[-20:])

    # Volume
    vols    = [d.get("volume", 0) or 0 for d in data]
    avg_vol = sum(vols[-20:]) / 20 if len(vols) >= 20 else 1
    vol_r   = (vols[-1] / avg_vol) if avg_vol else 1

    return {
        "price":  price, "atr": atr,
        "ema20":  ema20, "ema50": ema50, "ema200": ema200,
        "rsi":    rsi,
        "macd":   macd_val, "macd_sig": macd_sig, "macd_hist": macd_hist,
        "st_bull": st_bull,
        "pivot": pivot, "r1": r1, "s1": s1, "r2": r2, "s2": s2,
        "week_high": wh, "week_low": wl,
        "swing_high": sh20, "swing_low": sl20,
        "prev_close": closes[-2] if len(closes) >= 2 else price,
        "day_chg":    _pct(price, closes[-2]) if len(closes) >= 2 else 0,
        "vol_ratio":  round(vol_r, 2),
        "closes":     closes[-10:],
    }

# ── Bias engine ───────────────────────────────────────────────────────────────

def _bias(s: dict) -> str:
    """Multi-factor bias: bullish / bearish / neutral."""
    p = s["price"]
    bull = bear = 0
    for ema in ["ema20", "ema50", "ema200"]:
        v = s.get(ema)
        if v:
            if p > v: bull += 1
            else:     bear += 1
    if s.get("rsi"):
        if s["rsi"] > 56: bull += 1
        elif s["rsi"] < 44: bear += 1
    if s.get("macd") is not None and s.get("macd_sig") is not None:
        if s["macd"] > s["macd_sig"]: bull += 1
        else: bear += 1
    if s.get("st_bull"): bull += 2
    else: bear += 2
    # Close vs recent closes
    rec = s.get("closes", [])
    if len(rec) >= 5:
        above = sum(1 for c in rec[-5:] if c >= rec[-1])
        if above <= 1: bull += 1
        elif above >= 4: bear += 1
    if bull >= 5: return "bullish"
    if bear >= 5: return "bearish"
    return "neutral"

def _structure_label(s: dict) -> str:
    closes = s.get("closes", [])
    if len(closes) < 5: return "indecisive"
    ch3 = _pct(closes[-1], closes[-4])
    if ch3 > 1.5:  return "uptrend"
    if ch3 < -1.5: return "downtrend"
    if abs(ch3) < 0.5: return "consolidation"
    return "indecisive"

# ── Level calculator ──────────────────────────────────────────────────────────

def _calc_levels(s: dict, sym: str) -> dict:
    """
    Compute professional-grade intraday levels.
    Returns multiple support/resistance pairs like 23950/800 - 24150/250
    """
    p    = s["price"]
    atr  = s["atr"]
    st   = _step(p)

    # Pivot-based levels
    r1 = _snap(s["r1"], st)
    s1 = _snap(s["s1"], st)
    r2 = _snap(s["r2"], st)
    s2 = _snap(s["s2"], st)

    # ATR-based intraday range from prev close
    pc = s["prev_close"]
    atr_factor = 0.55    # tighter for indices, looser for stocks
    if sym in ("NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX"):
        atr_factor = 0.60

    intra_low  = _snap(pc - atr * atr_factor, st)
    intra_high = _snap(pc + atr * atr_factor, st)

    # Sub-levels: intermediate pivots
    intra_low2  = _snap(intra_low  + atr * 0.2, st)
    intra_high2 = _snap(intra_high - atr * 0.2, st)

    # Weekly range
    wk_low  = _snap(s["week_low"],  st)
    wk_high = _snap(s["week_high"], st)

    # Positional support/resistance (EMA-based)
    pos_supp = _snap(s["ema50"] or p * 0.97, st)
    pos_res  = _snap(s["swing_high"], st)

    # Congestion zone (where price is grinding)
    cong_low  = _snap(min(s1, intra_low),  st)
    cong_high = _snap(max(r1, intra_high), st)

    return {
        "intra_low1":  intra_low,
        "intra_low2":  intra_low2,
        "intra_high1": intra_high,
        "intra_high2": intra_high2,
        "s1": s1, "s2": s2, "r1": r1, "r2": r2,
        "wk_low": wk_low, "wk_high": wk_high,
        "pos_supp": pos_supp, "pos_res": pos_res,
        "cong_low": cong_low, "cong_high": cong_high,
        "pivot": _snap(s["pivot"], st),
        "step": st,
    }

# ── Trade setup builder ───────────────────────────────────────────────────────

def _trade_setup(s: dict, lv: dict, bias: str, sym: str) -> dict:
    """Build the actionable trade setup — entry zone, target, SL."""
    p   = s["price"]
    atr = s["atr"]
    st  = lv["step"]

    # ATR-based buffer for entry zone (±X pts)
    buf = _snap(atr * 0.12, st)
    buf = max(buf, st * 2)

    if bias == "bullish":
        # Buy on dip near intra_low1 (support)
        entry     = lv["intra_low1"]
        entry_buf = buf
        tgt1      = lv["intra_high1"]
        tgt2      = lv["intra_high2"]
        sl        = _snap(entry - atr * 0.45, st)
        direction = "BUY"
        action    = f"Buy on dips close to {entry}+/-{entry_buf} Pts"
        tgt_str   = f"TGT {tgt1}  SL {sl}"
        breakout_note = (f"Close/sustenance on 15m tf above {lv['intra_high1']} "
                         f"→ momentum target {lv['intra_high2']}–{lv['r1']}")
        positional_view = (f"Positive — negates only on close below {lv['s2']}")

    elif bias == "bearish":
        entry     = lv["intra_high1"]
        entry_buf = buf
        tgt1      = lv["intra_low1"]
        tgt2      = lv["intra_low2"]
        sl        = _snap(entry + atr * 0.45, st)
        direction = "SELL"
        action    = f"Sell on rise close to {entry}+/-{entry_buf} Pts"
        tgt_str   = f"TGT {tgt1}  SL {sl}"
        breakout_note = (f"Sustenance on 15m tf below {lv['intra_low1']} "
                         f"→ drag to {lv['intra_low2']}–{lv['s1']}")
        positional_view = (f"Negative — recovery only on close above {lv['r2']}")

    else:
        # Neutral — range play / no-trade zone
        entry     = lv["cong_low"]
        entry_buf = buf
        tgt1      = lv["cong_high"]
        tgt2      = lv["r1"]
        sl        = _snap(entry - atr * 0.35, st)
        direction = "RANGE"
        action    = (f"No-trade zone {lv['cong_low']}–{lv['cong_high']}. "
                     f"Avoid compulsive trading within this range")
        tgt_str   = f"Trade only on clear breakout above {lv['cong_high']} or breakdown below {lv['cong_low']}"
        breakout_note = (f"Breakout above {lv['cong_high']} → {lv['r1']}–{lv['r2']} | "
                         f"Breakdown below {lv['cong_low']} → {lv['s1']}–{lv['s2']}")
        positional_view = f"Range-bound. Directional move on weekly close outside {lv['cong_low']}–{lv['cong_high']}"

    return {
        "direction":       direction,
        "entry":           entry,
        "entry_buf":       entry_buf,
        "tgt1":            tgt1,
        "tgt2":            tgt2,
        "sl":              sl,
        "action":          action,
        "tgt_str":         tgt_str,
        "breakout_note":   breakout_note,
        "positional_view": positional_view,
    }

# ── Options tip ───────────────────────────────────────────────────────────────

INDEX_STEP = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "MIDCPNIFTY": 25, "SENSEX": 100,
}
INDEX_SYMS = set(INDEX_STEP.keys())

def _options_tip(sym: str, s: dict, lv: dict, bias: str) -> str | None:
    """Returns an options hedge/directional suggestion line or None."""
    if sym not in INDEX_SYMS:
        return None
    p   = s["price"]
    atr = s["atr"]
    ss  = INDEX_STEP.get(sym, 50)
    atm = round(p / ss) * ss
    exp = _next_thursday()

    if bias == "bullish":
        strike = atm + ss
        prem   = round(p * 0.008)          # ~0.8% for weekly OTM CE
        sl_p   = round(prem * 0.70)
        tgt_p  = round(prem * 1.50)
        return (f"Options: Buy {sym} {strike}CE {exp} ~{prem}. "
                f"SL {sl_p}  Tgt {tgt_p}. "
                f"Hold if {sym} sustains above {atm}")
    elif bias == "bearish":
        strike = atm - ss
        prem   = round(p * 0.009)
        sl_p   = round(prem * 0.70)
        tgt_p  = round(prem * 1.60)
        return (f"Options: Buy {sym} {strike}PE {exp} ~{prem}. "
                f"SL {sl_p}  Tgt {tgt_p}. "
                f"Hold if {sym} stays below {atm}")
    else:
        # Hedge suggestion for portfolio
        hedge_strike = atm - ss * 2
        prem = round(p * 0.006)
        return (f"Hedge: Around {lv['intra_high1']}–{lv['r1']} area, "
                f"Buy {sym} {hedge_strike}PE {exp} ~{prem} as portfolio hedge. "
                f"SL on {sym} close above {lv['r2']}")


# ── VIX context note ──────────────────────────────────────────────────────────

def _vix_note(vix: float | None) -> str:
    """Generate a VIX-awareness note matching reference style."""
    if not vix:
        return ""
    if vix < 12:
        return (f"📍IndiaVIX at {vix:.1f}% — extreme complacency. "
                f"Option writers beware — sudden spike risk")
    if vix < 14:
        return (f"📍IndiaVIX below {vix:.0f}% — low volatility environment. "
                f"Good time for positional trades. Option premium cheap for buyers")
    if vix < 17:
        return (f"📍IndiaVIX in {vix:.0f}% range — moderate volatility. "
                f"Intraday swings normal. Stay with defined SL")
    if vix < 20:
        return (f"📍IndiaVIX at {vix:.1f}% — elevated volatility. "
                f"Wider swings expected. Reduce position size")
    return (f"📍IndiaVIX at {vix:.1f}% — high fear. "
            f"Avoid naked option selling. Expect whipsaws. "
            f"Market bottom possible if VIX starts declining")


# ── Main tip text formatter ───────────────────────────────────────────────────

def _format_pro_tip(sym: str, s: dict, lv: dict,
                    setup: dict, bias: str, vix: float | None,
                    bn_note: str) -> str:
    """
    Format tip exactly like the reference channel:
    #Symbol DDMon
    Weekly range / positional context
    Intraday levels (support1/support2 - resistance1/resistance2)
    📍 Watch items
    ➡️ Actionable trade
    """
    today    = datetime.now().strftime("%d%b").lstrip("0").upper()
    p        = s["price"]
    st       = lv["step"]
    structure = _structure_label(s)

    # ── Header ──
    lines = [f"#{sym} {today}"]

    # ── Weekly / Positional context ──
    if structure == "uptrend":
        if bias == "bullish":
            close_need = lv["intra_high1"]
            lines.append(
                f"Encouraging close above {lv['wk_low']}. "
                f"Hurdle of close above {lv['intra_high2']} yet a challenge"
            )
        else:
            lines.append(
                f"Grinding near highs — selective participation. Broad-based rally missing"
            )
    elif structure == "downtrend":
        if bias == "bearish":
            lines.append(
                f"Failure to sustain above {lv['intra_high1']} keeps {sym} vulnerable. "
                f"Close below {lv['s1']} will open doors to {lv['s2']}"
            )
        else:
            lines.append(
                f"Unconvincing rally — risk of retesting {lv['s1']}. "
                f"Stability only on close above {lv['intra_high2']}"
            )
    elif structure == "consolidation":
        lines.append(
            f"Grinding within range continues. "
            f"Weekly range {lv['wk_low']}–{lv['wk_high']}. "
            f"Close beyond this range will set tone for directional move"
        )
    else:
        lines.append(
            f"Volatile indecisive range. Trapped between "
            f"{lv['cong_low']}–{lv['cong_high']}"
        )

    # ── Weekly option range note for indices ──
    if sym in INDEX_SYMS:
        exp = _next_thursday()
        lines.append(
            f"Options data for {exp} weekly hints at "
            f"{lv['wk_low']}–{lv['wk_high']} range week"
        )

    # ── Intraday levels ──
    lines.append(
        f"Intraday {lv['intra_low1']}/{lv['intra_low2']}–"
        f"{lv['intra_high1']}/{lv['intra_high2']}"
    )

    # ── Positional view ──
    lines.append(f"Positional view: {setup['positional_view']}")

    lines.append("")  # blank line

    # ── VIX note ──
    vix_line = _vix_note(vix)
    if vix_line:
        lines.append(vix_line)

    # ── BankNifty cross-ref ──
    if bn_note:
        lines.append(f"📍{bn_note}")

    # ── Expiry day warning ──
    if _is_expiry_day() and sym in INDEX_SYMS:
        lines.append(
            f"⚠️ Today being weekly expiry, expect volatile swings. "
            f"Attempt directional bets only on trigger points or swing extremes"
        )

    lines.append("")  # blank line

    # ── Actionable trade ──
    if setup["direction"] == "RANGE":
        lines.append(f"➡️ {setup['action']}")
        lines.append(f"📍{setup['breakout_note']}")
    else:
        lines.append(f"➡️ {setup['action']}")
        lines.append(f"   {setup['tgt_str']}")
        lines.append(f"   Once above {lv['intra_high1']} → {setup['breakout_note']}")

    # ── Options line ──
    opt_line = _options_tip(sym, s, lv, bias)
    if opt_line:
        lines.append("")
        lines.append(f"📍{opt_line}")

    # ── RSI / indicator footnote ──
    rsi_str = f"RSI {s['rsi']:.0f}" if s.get("rsi") else ""
    vol_str = f"Vol {s['vol_ratio']:.1f}x avg" if s.get("vol_ratio") else ""
    atr_str = f"ATR {s['atr']:.0f}"
    footer_parts = [x for x in [rsi_str, atr_str, vol_str] if x]
    if footer_parts:
        lines.append("")
        lines.append(" | ".join(footer_parts))

    lines.append("⚠ For educational purposes. Apply your own risk management.")

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_pro_tip(
    symbol: str,
    data: list       = None,
    live_price: float = None,
    vix: float       = None,
    bn_data: list    = None,    # BankNifty OHLCV — used for cross-ref note
) -> dict:
    """
    Generate a professional-grade tip for one symbol.

    Parameters
    ----------
    symbol     : e.g. 'NIFTY', 'BANKNIFTY', 'RELIANCE'
    data       : OHLCV list. Fetched automatically if None.
    live_price : today's live price (injected into last bar)
    vix        : current IndiaVIX float (e.g. 13.2)
    bn_data    : BankNifty OHLCV for cross-reference note

    Returns
    -------
    {
      symbol, price, bias, structure,
      levels: {intra_low1, intra_high1, ...},
      setup:  {direction, entry, sl, tgt1, tgt2, ...},
      tip_text: <formatted professional text>,
      generated: <timestamp>,
    }
    """
    sym = symbol.upper()

    if data is None:
        data = fetch_ohlcv(sym, period="3mo")

    data = [d for d in (data or [])
            if _safe(d.get("close")) and _safe(d.get("high")) and _safe(d.get("low"))]

    if len(data) < 20:
        return {"symbol": sym, "error": "Insufficient data (need 20+ bars)"}

    s = _build_snapshot(data, live_price)
    if not s:
        return {"symbol": sym, "error": "Indicator computation failed"}

    bias      = _bias(s)
    structure = _structure_label(s)
    lv        = _calc_levels(s, sym)
    setup     = _trade_setup(s, lv, bias, sym)

    # BankNifty cross-reference note
    bn_note = ""
    if sym != "BANKNIFTY" and sym in INDEX_SYMS:
        if bn_data:
            bn_s = _build_snapshot(
                [d for d in bn_data if _safe(d.get("close"))], None
            )
            if bn_s:
                bn_bias = _bias(bn_s)
                bn_lv   = _calc_levels(bn_s, "BANKNIFTY")
                if bn_bias == bias:
                    bn_note = (f"BankNifty confirms {bn_bias} bias. "
                               f"Key: sustain above {bn_lv['intra_low1']} for upmove")
                else:
                    bn_note = (f"BankNifty diverging — {bn_bias} vs {sym} {bias}. "
                               f"BN will be the 🔑 in deciding overall trajectory")
        else:
            bn_note = "BN remains the 🔑 in deciding further overall trajectory"

    tip_text = _format_pro_tip(sym, s, lv, setup, bias, vix, bn_note)

    return {
        "symbol":    sym,
        "price":     round(s["price"], 2),
        "bias":      bias,
        "structure": structure,
        "rsi":       round(s["rsi"], 1) if s.get("rsi") else None,
        "atr":       round(s["atr"],  1),
        "vol_ratio": s["vol_ratio"],
        "st_bull":   s["st_bull"],
        "levels":    lv,
        "setup":     setup,
        "tip_text":  tip_text,
        "generated": datetime.now().strftime("%d %b %Y %H:%M"),
    }


def generate_pro_tips_batch(
    symbols: list,
    live_prices: dict = None,
    vix: float        = None,
    max_workers: int  = 6,
) -> list:
    """
    Generate professional tips for multiple symbols in parallel.
    BankNifty data is fetched once and shared for cross-reference.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Fetch BankNifty once for cross-reference
    bn_data = None
    if any(s.upper() in INDEX_SYMS for s in symbols):
        try:
            bn_data = fetch_ohlcv("BANKNIFTY", period="3mo")
        except Exception:
            pass

    def _one(sym):
        lp = (live_prices or {}).get(sym.upper())
        return generate_pro_tip(
            sym,
            live_price = lp,
            vix        = vix,
            bn_data    = bn_data if sym.upper() != "BANKNIFTY" else None,
        )

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(_one, s): s for s in symbols}
        for fut in as_completed(futs):
            try:    results.append(fut.result())
            except Exception as e:
                results.append({"symbol": futs[fut], "error": str(e)})

    results.sort(key=lambda x: x.get("symbol", ""))
    return results
