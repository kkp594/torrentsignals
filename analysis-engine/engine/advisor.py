"""
Smart Stock/Index Advisor — multi-factor confluence engine.
Produces BUY / SELL / HOLD recommendation with:
  - Confidence % (0-100)
  - Entry zone, Stop-loss, Target 1 & 2
  - Reasoning breakdown (each factor explained)
  - Risk level (Low / Medium / High)
  - Timeframe suitability (Intraday / Swing / Positional)

This engine uses ONLY price data + pre-computed indicators.
No ML, no external API, no TA-lib.
"""
from __future__ import annotations
from .indicators import compute_all, ema as calc_ema
from .signals import _last, _prev
import math


def _safe(v):
    """Return v if it's a finite number, else None."""
    try:
        return v if (v is not None and not math.isnan(v) and not math.isinf(v)) else None
    except Exception:
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slope(arr: list, n: int = 5) -> float:
    """Approximate slope of last n non-None values (normalised to price %)."""
    vals = [v for v in arr if v is not None][-n:]
    if len(vals) < 2:
        return 0.0
    return (vals[-1] - vals[0]) / vals[0] * 100 if vals[0] else 0.0


def _pct(a, b) -> float:
    """(a-b)/b * 100 — returns 0 if b is 0."""
    return round((a - b) / b * 100, 2) if b else 0.0


def _round2(v) -> float:
    return round(float(v), 2) if v is not None else 0.0


# ── Core Advisor ──────────────────────────────────────────────────────────────

def advise(data: list, symbol: str = "") -> dict:
    """
    Full multi-factor recommendation for a stock/index/commodity.

    Returns
    -------
    {
        symbol, price, action,       # BUY / SELL / HOLD
        confidence,                   # 0-100
        entry, stop_loss,
        target1, target2,
        risk,                         # Low / Medium / High
        timeframes,                   # list: Intraday / Swing / Positional
        factors,                      # list of {name, verdict, detail, score}
        summary,                      # one-line plain-English narrative
        warnings,                     # list of caution strings
        score_breakdown               # bull_score, bear_score, neutral_score
    }
    """
    MIN_BARS = 60  # need at least 60 daily bars for meaningful analysis
    if not data or len(data) < MIN_BARS:
        return _insufficient(symbol, len(data) if data else 0)

    ind    = compute_all(data)
    closes = [d["close"] for d in data]
    highs  = [d["high"]  for d in data]
    lows   = [d["low"]   for d in data]
    vols   = [d["volume"] for d in data]

    # Guard: if last close is NaN / 0, walk back to find a valid price
    import math
    for c in reversed(closes):
        if c and not math.isnan(c) and c > 0:
            price = c
            break
    if not price or math.isnan(price):
        return _insufficient(symbol, len(data))

    # Trim trailing NaN/zero bars so indicators compute on clean data
    clean_data = [d for d in data if d.get("close") and not math.isnan(float(d["close"])) and float(d["close"]) > 0]
    if len(clean_data) < MIN_BARS:
        return _insufficient(symbol, len(clean_data))
    # Recompute indicators on clean data
    ind    = compute_all(clean_data)
    closes = [d["close"] for d in clean_data]
    highs  = [d["high"]  for d in clean_data]
    lows   = [d["low"]   for d in clean_data]
    vols   = [d["volume"] for d in clean_data]
    price  = closes[-1]

    # Safe accessor: wraps _last with NaN/None guard
    def sl(arr): return _safe(_last(arr))
    def sp(arr, n=1): return _safe(_prev(arr, n))

    factors   = []
    bull_pts  = 0
    bear_pts  = 0
    warnings  = []

    # ── 1. TREND (EMA alignment) ─────────────────────────────────────────────
    ema20  = sl(ind["ema20"])
    ema50  = sl(ind["ema50"])
    ema200 = sl(ind["ema200"])
    ema20_slope  = _slope(ind["ema20"])
    ema50_slope  = _slope(ind["ema50"])

    trend_score = 0
    trend_detail = []
    if ema200 and price > ema200:
        trend_score += 3
        trend_detail.append(f"Price ₹{price:.2f} > EMA200 ₹{ema200:.2f} → Long-term uptrend")
    elif ema200 and price < ema200:
        trend_score -= 3
        trend_detail.append(f"Price ₹{price:.2f} < EMA200 ₹{ema200:.2f} → Long-term downtrend")
    if ema50 and price > ema50:
        trend_score += 2
        trend_detail.append(f"Price > EMA50 ₹{ema50:.2f} → Medium-term bullish")
    elif ema50 and price < ema50:
        trend_score -= 2
        trend_detail.append(f"Price < EMA50 ₹{ema50:.2f} → Medium-term bearish")
    if ema20 and price > ema20:
        trend_score += 1
        trend_detail.append(f"Price > EMA20 ₹{ema20:.2f} → Short-term bullish")
    elif ema20 and price < ema20:
        trend_score -= 1
        trend_detail.append(f"Price < EMA20 ₹{ema20:.2f} → Short-term bearish")
    if ema20 and ema50:
        if ema20 > ema50:
            trend_score += 2
            trend_detail.append(f"EMA20 > EMA50 (Golden Cross alignment)")
        else:
            trend_score -= 2
            trend_detail.append(f"EMA20 < EMA50 (Death Cross alignment)")
    if ema20_slope > 0.3:
        trend_detail.append(f"EMA20 rising steeply (+{ema20_slope:.1f}%)")
    elif ema20_slope < -0.3:
        trend_detail.append(f"EMA20 falling steeply ({ema20_slope:.1f}%)")

    trend_verdict = "Bullish" if trend_score >= 4 else "Bearish" if trend_score <= -4 else "Mixed"
    factors.append({
        "name": "Trend (EMA Alignment)",
        "verdict": trend_verdict,
        "detail": " | ".join(trend_detail) if trend_detail else "Insufficient data",
        "score": trend_score,
        "icon": "📈" if trend_verdict == "Bullish" else "📉" if trend_verdict == "Bearish" else "↔️"
    })
    if trend_score > 0:
        bull_pts += trend_score
    else:
        bear_pts += abs(trend_score)

    # ── 2. MOMENTUM (RSI + Stochastic) ───────────────────────────────────────
    rsi_val  = sl(ind["rsi14"])
    rsi_prev = sp(ind["rsi14"])
    sk = sl(ind["stoch"][0])
    sd = sl(ind["stoch"][1])
    sk_prev = sp(ind["stoch"][0])
    sd_prev = sp(ind["stoch"][1])

    mom_score = 0
    mom_detail = []

    if rsi_val is not None:
        if 40 <= rsi_val <= 65:
            mom_score += 2
            mom_detail.append(f"RSI {rsi_val:.1f} — healthy momentum zone (40-65)")
        elif rsi_val < 30:
            mom_score += 1     # oversold = potential bounce
            mom_detail.append(f"RSI {rsi_val:.1f} — oversold, watch for reversal")
            warnings.append("RSI oversold — may continue falling in strong downtrend")
        elif rsi_val > 70:
            mom_score -= 1     # overbought = risk of pullback
            mom_detail.append(f"RSI {rsi_val:.1f} — overbought, pullback risk")
            warnings.append("RSI overbought — avoid chasing; wait for dip")
        else:
            mom_detail.append(f"RSI {rsi_val:.1f} — neutral range")
        # RSI direction
        if rsi_prev and rsi_val > rsi_prev:
            mom_score += 1
            mom_detail.append("RSI rising → building momentum")
        elif rsi_prev and rsi_val < rsi_prev:
            mom_score -= 1
            mom_detail.append("RSI falling → weakening momentum")

    if sk is not None and sd is not None:
        if sk < 20 and sd < 20:
            mom_score += 1
            mom_detail.append(f"Stochastic oversold (K:{sk:.0f} D:{sd:.0f})")
        elif sk > 80 and sd > 80:
            mom_score -= 1
            mom_detail.append(f"Stochastic overbought (K:{sk:.0f} D:{sd:.0f})")
        if sk_prev and sd_prev and sk_prev < sd_prev and sk >= sd:
            mom_score += 2
            mom_detail.append(f"Stochastic bullish crossover (K:{sk:.0f} crossed above D:{sd:.0f})")

    mom_verdict = "Bullish" if mom_score >= 3 else "Bearish" if mom_score <= -2 else "Neutral"
    factors.append({
        "name": "Momentum (RSI + Stochastic)",
        "verdict": mom_verdict,
        "detail": " | ".join(mom_detail) if mom_detail else "No data",
        "score": mom_score,
        "icon": "⚡" if mom_verdict == "Bullish" else "🔻" if mom_verdict == "Bearish" else "🔄"
    })
    if mom_score > 0:
        bull_pts += mom_score
    else:
        bear_pts += abs(mom_score)

    # ── 3. MACD (Trend Confirmation) ─────────────────────────────────────────
    macd_line, sig_line, histogram = ind["macd"]
    macd_val  = sl(macd_line)
    macd_prev = sp(macd_line)
    sig_val   = sl(sig_line)
    sig_prev  = sp(sig_line)
    hist_val  = sl(histogram)
    hist_prev = sp(histogram)

    macd_score = 0
    macd_detail = []

    if macd_val is not None and sig_val is not None:
        if macd_val > sig_val:
            macd_score += 2
            macd_detail.append(f"MACD {macd_val:.3f} above signal {sig_val:.3f} — bullish")
        else:
            macd_score -= 2
            macd_detail.append(f"MACD {macd_val:.3f} below signal {sig_val:.3f} — bearish")
        if macd_val > 0:
            macd_score += 1
            macd_detail.append("MACD above zero line — positive territory")
        else:
            macd_score -= 1
            macd_detail.append("MACD below zero line — negative territory")
        if (macd_prev is not None and sig_prev is not None
                and macd_prev < sig_prev and macd_val >= sig_val):
            macd_score += 3
            macd_detail.append("⚡ Fresh MACD bullish crossover — strong buy signal")
        elif (macd_prev is not None and sig_prev is not None
              and macd_prev > sig_prev and macd_val <= sig_val):
            macd_score -= 3
            macd_detail.append("⚡ Fresh MACD bearish crossover — strong sell signal")
        if hist_val is not None and hist_prev is not None:
            if hist_val > hist_prev:
                macd_detail.append(f"Histogram expanding ({hist_val:.3f}) — momentum strengthening")
            else:
                macd_detail.append(f"Histogram contracting ({hist_val:.3f}) — momentum weakening")

    macd_verdict = "Bullish" if macd_score >= 3 else "Bearish" if macd_score <= -2 else "Neutral"
    factors.append({
        "name": "MACD (Trend Confirmation)",
        "verdict": macd_verdict,
        "detail": " | ".join(macd_detail) if macd_detail else "Insufficient data",
        "score": macd_score,
        "icon": "📊" if macd_verdict == "Bullish" else "📉" if macd_verdict == "Bearish" else "📊"
    })
    if macd_score > 0:
        bull_pts += macd_score
    else:
        bear_pts += abs(macd_score)

    # ── 4. BOLLINGER BANDS (Volatility + Mean Reversion) ─────────────────────
    bb_upper, bb_mid, bb_lower = ind["bb"]
    bbu = sl(bb_upper)
    bbl = sl(bb_lower)
    bbm = sl(bb_mid)

    bb_score = 0
    bb_detail = []
    bb_width_pct = 0

    if bbu and bbl and bbm:
        bb_width_pct = (bbu - bbl) / bbm * 100
        pct_from_mid = _pct(price, bbm)
        if price <= bbl * 1.005:
            bb_score += 2
            bb_detail.append(f"Price touching lower BB ₹{bbl:.2f} — oversold zone, mean reversion likely")
        elif price >= bbu * 0.995:
            bb_score -= 1
            bb_detail.append(f"Price touching upper BB ₹{bbu:.2f} — overbought zone")
        else:
            bb_detail.append(f"Price {pct_from_mid:+.1f}% from BB midline ₹{bbm:.2f}")
        if bb_width_pct < 4:
            bb_detail.append(f"⚠ BB Squeeze ({bb_width_pct:.1f}%) — explosive move imminent, direction unclear")
            warnings.append("Bollinger Squeeze active — avoid large positions until breakout direction confirmed")
        elif bb_width_pct > 15:
            bb_detail.append(f"BB very wide ({bb_width_pct:.1f}%) — high volatility, widen stop-loss")
            warnings.append("High volatility phase — use wider stops")

    bb_verdict = "Bullish" if bb_score >= 2 else "Bearish" if bb_score <= -1 else "Neutral"
    factors.append({
        "name": "Bollinger Bands (Volatility)",
        "verdict": bb_verdict,
        "detail": " | ".join(bb_detail) if bb_detail else "No data",
        "score": bb_score,
        "icon": "📦"
    })
    if bb_score > 0:
        bull_pts += bb_score
    else:
        bear_pts += abs(bb_score)

    # ── 5. VOLUME (Conviction Check) ─────────────────────────────────────────
    avg_vol_20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else (sum(vols[:-1]) / max(len(vols)-1, 1))
    cur_vol    = vols[-1]
    vol_ratio  = cur_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0
    day_chg    = _pct(closes[-1], closes[-2]) if len(closes) >= 2 else 0

    vol_score  = 0
    vol_detail = []

    if vol_ratio >= 2.0:
        if day_chg > 0:
            vol_score += 3
            vol_detail.append(f"Volume {vol_ratio:.1f}x above average with +{day_chg:.2f}% price rise — strong buying conviction")
        else:
            vol_score -= 3
            vol_detail.append(f"Volume {vol_ratio:.1f}x above average with {day_chg:.2f}% price fall — strong selling conviction")
    elif vol_ratio >= 1.3:
        if day_chg > 0:
            vol_score += 1
            vol_detail.append(f"Above-average volume ({vol_ratio:.1f}x) on up day — moderate buying interest")
        else:
            vol_score -= 1
            vol_detail.append(f"Above-average volume ({vol_ratio:.1f}x) on down day — selling pressure")
    else:
        vol_detail.append(f"Normal volume ({vol_ratio:.1f}x avg) — no strong conviction")
        if day_chg > 2:
            warnings.append(f"Price up {day_chg:.1f}% on below-average volume — weak breakout, may not sustain")

    # OBV trend (last 10 days)
    obv_arr = ind["obv"]
    if len(obv_arr) >= 10:
        obv_slope = _slope(obv_arr, 10)
        if obv_slope > 0:
            vol_score += 1
            vol_detail.append(f"OBV rising — accumulation phase")
        else:
            vol_score -= 1
            vol_detail.append(f"OBV falling — distribution phase")

    vol_verdict = "Bullish" if vol_score >= 2 else "Bearish" if vol_score <= -2 else "Neutral"
    factors.append({
        "name": "Volume & OBV (Conviction)",
        "verdict": vol_verdict,
        "detail": " | ".join(vol_detail) if vol_detail else "No volume data",
        "score": vol_score,
        "icon": "📊" if vol_verdict == "Bullish" else "📉" if vol_verdict == "Bearish" else "📊"
    })
    if vol_score > 0:
        bull_pts += vol_score
    else:
        bear_pts += abs(vol_score)

    # ── 6. ADX (Trend Strength — is there even a trade?) ─────────────────────
    adx_val = sl(ind["adx14"])
    adx_score = 0
    adx_detail = []

    if adx_val:
        if adx_val >= 25:
            adx_score += 2
            adx_detail.append(f"ADX {adx_val:.1f} — strong trend in place, trend-following strategies favored")
        elif adx_val >= 20:
            adx_score += 1
            adx_detail.append(f"ADX {adx_val:.1f} — developing trend, moderate conviction")
        else:
            adx_detail.append(f"ADX {adx_val:.1f} — no clear trend (< 20), choppy market")
            warnings.append(f"ADX {adx_val:.1f} indicates range-bound market — trend signals may be unreliable")
    else:
        adx_detail.append("ADX data unavailable")

    adx_verdict = "Strong Trend" if adx_val and adx_val >= 25 else "Weak Trend" if adx_val and adx_val < 20 else "Developing"
    factors.append({
        "name": "ADX (Trend Strength)",
        "verdict": adx_verdict,
        "detail": " | ".join(adx_detail),
        "score": adx_score,
        "icon": "💪" if adx_val and adx_val >= 25 else "😴"
    })
    bull_pts += adx_score  # ADX only adds to bull (it confirms, not opposes)

    # ── 7. SUPPORT / RESISTANCE proximity ─────────────────────────────────────
    # Classic pivot points from recent 20 bars — filter out NaN values
    recent = [d for d in data[-20:] if _safe(d.get("high")) and _safe(d.get("low")) and _safe(d.get("close"))]
    if not recent:
        recent = data[-5:]  # last resort
    pivot_H = max((_safe(d["high"])  or 0) for d in recent)
    pivot_L = min((_safe(d["low"])   or price) for d in recent)
    pivot_C = _safe(closes[-1]) or price
    pivot_P = (pivot_H + pivot_L + pivot_C) / 3 if pivot_H and pivot_L else price
    s1 = (2 * pivot_P - pivot_H) if pivot_H else price * 0.97
    r1 = (2 * pivot_P - pivot_L) if pivot_L else price * 1.03

    # 52-week levels
    year_closes = [_safe(c) for c in closes[-252:] if _safe(c)]
    high52 = max(year_closes) if len(year_closes) >= 50 else None
    low52  = min(year_closes) if len(year_closes) >= 50 else None

    sr_score  = 0
    sr_detail = []

    dist_r1   = _pct(r1, price)
    dist_s1   = _pct(price, s1)

    if dist_s1 <= 1.5:
        sr_score += 2
        sr_detail.append(f"Price ₹{price:.2f} within 1.5% of pivot support S1 ₹{s1:.2f} — excellent entry zone")
    elif dist_s1 <= 3:
        sr_score += 1
        sr_detail.append(f"Price near pivot support S1 ₹{s1:.2f} ({dist_s1:.1f}% away)")
    if dist_r1 <= 1.5:
        sr_score -= 2
        sr_detail.append(f"⚠ Price ₹{price:.2f} within 1.5% of pivot resistance R1 ₹{r1:.2f} — avoid buying here")
        warnings.append(f"Price is near resistance R1 ₹{r1:.2f} — poor risk/reward for new long entry")
    if high52 and price >= high52 * 0.98:
        sr_detail.append(f"Near 52-week high ₹{high52:.2f} — breakout or rejection risk")
    if low52 and price <= low52 * 1.02:
        sr_score += 1
        sr_detail.append(f"Near 52-week low ₹{low52:.2f} — historically strong support zone")

    sr_verdict = "Good Entry" if sr_score >= 2 else "Near Resistance" if sr_score <= -2 else "Neutral"
    factors.append({
        "name": "Support/Resistance Proximity",
        "verdict": sr_verdict,
        "detail": " | ".join(sr_detail) if sr_detail else f"Pivot P: ₹{pivot_P:.2f} | S1: ₹{s1:.2f} | R1: ₹{r1:.2f}",
        "score": sr_score,
        "icon": "🎯" if sr_score >= 2 else "🚧" if sr_score <= -2 else "↔️"
    })
    if sr_score > 0:
        bull_pts += sr_score
    else:
        bear_pts += abs(sr_score)

    # ── Final Scoring ─────────────────────────────────────────────────────────
    total_pts = bull_pts + bear_pts
    if total_pts == 0:
        confidence = 50
    else:
        confidence = round(bull_pts / total_pts * 100)

    # Action
    if confidence >= 65:
        action = "BUY"
    elif confidence <= 35:
        action = "SELL"
    else:
        action = "HOLD"

    # ── ATR-based targets and stop-loss ───────────────────────────────────────
    atr_val = sl(ind["atr14"]) or (price * 0.015)  # fallback 1.5%

    if action == "BUY":
        entry     = round(price, 2)
        stop_loss = round(price - 1.5 * atr_val, 2)
        target1   = round(price + 2.0 * atr_val, 2)
        target2   = round(price + 3.5 * atr_val, 2)
    elif action == "SELL":
        entry     = round(price, 2)
        stop_loss = round(price + 1.5 * atr_val, 2)  # SL above for short/exit
        target1   = round(price - 2.0 * atr_val, 2)
        target2   = round(price - 3.5 * atr_val, 2)
    else:  # HOLD
        entry     = round(price, 2)
        stop_loss = round(price - 1.5 * atr_val, 2)
        target1   = round(price + 1.5 * atr_val, 2)
        target2   = round(price + 3.0 * atr_val, 2)

    sl_pct  = abs(_pct(stop_loss, entry))
    t1_pct  = abs(_pct(target1, entry))
    rr_ratio = round(t1_pct / sl_pct, 2) if sl_pct else 0

    # Risk Level
    if bb_width_pct > 15 or (adx_val and adx_val > 35) or vol_ratio > 3:
        risk = "High"
    elif bb_width_pct < 4 or (adx_val and adx_val < 15):
        risk = "Medium"  # squeeze = uncertain
    else:
        risk = "Low" if sl_pct <= 3 else "Medium" if sl_pct <= 5 else "High"

    # Timeframe suitability
    timeframes = []
    if adx_val and adx_val > 20 and vol_ratio > 1.5:
        timeframes.append("Intraday")
    if rsi_val and 35 <= rsi_val <= 70 and (ema20 and ema50 and ema20 > ema50):
        timeframes.append("Swing (3-10 days)")
    if ema200 and price > ema200 and trend_score >= 4:
        timeframes.append("Positional (weeks)")
    if not timeframes:
        timeframes = ["Swing (3-10 days)"]  # default fallback

    # ── One-line narrative ────────────────────────────────────────────────────
    def _build_summary():
        bull_factors = [f["name"] for f in factors if f["score"] > 0]
        bear_factors = [f["name"] for f in factors if f["score"] < 0]
        if action == "BUY":
            top_bull = ", ".join(bull_factors[:2]) or "technical indicators"
            return (f"{symbol} shows bullish confluence — {top_bull} aligned. "
                    f"Entry ₹{entry}, target ₹{target1}–₹{target2}, SL ₹{stop_loss}. "
                    f"R/R ratio {rr_ratio:.1f}:1.")
        elif action == "SELL":
            top_bear = ", ".join(bear_factors[:2]) or "technical indicators"
            return (f"{symbol} shows bearish signals — {top_bear} turning negative. "
                    f"Consider booking profits or placing SL at ₹{stop_loss}.")
        else:
            return (f"{symbol} in consolidation zone — mixed signals. "
                    f"Wait for breakout above ₹{r1:.2f} (BUY trigger) or breakdown below ₹{s1:.2f} (SELL trigger).")

    summary = _build_summary()

    # Disclaimer
    warnings.append("⚠ This analysis is based on technical indicators only. Always use your own judgement and set appropriate position sizes.")

    return {
        "symbol":      symbol,
        "price":       price,
        "action":      action,
        "confidence":  confidence,
        "entry":       entry,
        "stop_loss":   stop_loss,
        "target1":     target1,
        "target2":     target2,
        "sl_pct":      round(sl_pct, 2),
        "t1_pct":      round(t1_pct, 2),
        "rr_ratio":    rr_ratio,
        "risk":        risk,
        "timeframes":  timeframes,
        "factors":     factors,
        "summary":     summary,
        "warnings":    warnings,
        "score_breakdown": {
            "bull": bull_pts,
            "bear": bear_pts,
            "confidence": confidence,
        },
        "levels": {
            "pivot":    round(pivot_P, 2),
            "r1":       round(r1, 2),
            "s1":       round(s1, 2),
            "52w_high": round(high52, 2) if high52 else None,
            "52w_low":  round(low52,  2) if low52  else None,
        },
        "atr": round(atr_val, 2),
    }


def _insufficient(symbol: str, bars: int) -> dict:
    return {
        "symbol":     symbol,
        "price":      0,
        "action":     "HOLD",
        "confidence": 0,
        "entry":      0, "stop_loss": 0, "target1": 0, "target2": 0,
        "sl_pct": 0, "t1_pct": 0, "rr_ratio": 0,
        "risk":       "Unknown",
        "timeframes": [],
        "factors":    [],
        "summary":    f"Insufficient data ({bars} bars — need at least 60)",
        "warnings":   [],
        "score_breakdown": {"bull": 0, "bear": 0, "confidence": 0},
        "levels":     {},
        "atr":        0,
    }
