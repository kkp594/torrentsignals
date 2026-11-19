"""
Rule-based buy/sell signal engine.
Returns a score 0-100 and individual signal breakdown.
"""
from .indicators import compute_all, ema, rsi as calc_rsi


def _last(lst):
    """Get last non-None value from list."""
    for v in reversed(lst):
        if v is not None:
            return v
    return None


def _prev(lst, n=1):
    """Get nth-from-last non-None value."""
    count = 0
    for v in reversed(lst):
        if v is not None:
            count += 1
            if count == n + 1:
                return v
    return None


def analyze(data: list, symbol: str = "") -> dict:
    """
    Full signal analysis for a stock.
    Returns score, signals list, summary.
    """
    if len(data) < 30:
        return {"symbol": symbol, "score": 0, "signals": [], "summary": "Insufficient data"}

    ind = compute_all(data)
    closes = [d["close"] for d in data]
    price  = closes[-1]

    signals = []
    bull_pts = 0
    bear_pts = 0

    # ── RSI ───────────────────────────────────────────────────────────────────
    rsi_val  = _last(ind["rsi14"])
    rsi_prev = _prev(ind["rsi14"])
    if rsi_val is not None:
        if rsi_val < 30:
            signals.append({"name": "RSI Oversold", "type": "bull", "value": f"{rsi_val:.1f}", "weight": 2})
            bull_pts += 2
        elif rsi_val > 70:
            signals.append({"name": "RSI Overbought", "type": "bear", "value": f"{rsi_val:.1f}", "weight": 2})
            bear_pts += 2
        elif 30 <= rsi_val <= 50:
            signals.append({"name": "RSI Neutral-Bullish", "type": "neutral", "value": f"{rsi_val:.1f}", "weight": 1})
            bull_pts += 1
        # RSI crossover 50
        if rsi_prev and rsi_prev < 50 <= rsi_val:
            signals.append({"name": "RSI Crossed 50 ↑", "type": "bull", "value": f"{rsi_val:.1f}", "weight": 2})
            bull_pts += 2
        elif rsi_prev and rsi_prev > 50 >= rsi_val:
            signals.append({"name": "RSI Crossed 50 ↓", "type": "bear", "value": f"{rsi_val:.1f}", "weight": 2})
            bear_pts += 2

    # ── MACD ──────────────────────────────────────────────────────────────────
    macd_line, sig_line, histogram = ind["macd"]
    macd_val  = _last(macd_line)
    macd_prev = _prev(macd_line)
    sig_val   = _last(sig_line)
    sig_prev  = _prev(sig_line)
    hist_val  = _last(histogram)
    hist_prev = _prev(histogram)

    if macd_val is not None and sig_val is not None:
        if macd_prev is not None and sig_prev is not None:
            if macd_prev < sig_prev and macd_val >= sig_val:
                signals.append({"name": "MACD Bullish Crossover", "type": "bull", "value": f"{macd_val:.3f}", "weight": 3})
                bull_pts += 3
            elif macd_prev > sig_prev and macd_val <= sig_val:
                signals.append({"name": "MACD Bearish Crossover", "type": "bear", "value": f"{macd_val:.3f}", "weight": 3})
                bear_pts += 3
        if macd_val > 0:
            signals.append({"name": "MACD Above Zero", "type": "bull", "value": f"{macd_val:.3f}", "weight": 1})
            bull_pts += 1
        else:
            signals.append({"name": "MACD Below Zero", "type": "bear", "value": f"{macd_val:.3f}", "weight": 1})
            bear_pts += 1
        if hist_prev is not None and hist_val is not None:
            if hist_val > hist_prev and hist_val > 0:
                signals.append({"name": "MACD Histogram Rising", "type": "bull", "value": f"{hist_val:.3f}", "weight": 1})
                bull_pts += 1

    # ── EMA Trend ─────────────────────────────────────────────────────────────
    ema20  = _last(ind["ema20"])
    ema50  = _last(ind["ema50"])
    ema200 = _last(ind["ema200"])

    if ema20 and price > ema20:
        signals.append({"name": "Price > EMA20", "type": "bull", "value": f"₹{price:.2f} > ₹{ema20:.2f}", "weight": 1})
        bull_pts += 1
    elif ema20 and price < ema20:
        signals.append({"name": "Price < EMA20", "type": "bear", "value": f"₹{price:.2f} < ₹{ema20:.2f}", "weight": 1})
        bear_pts += 1

    if ema50 and price > ema50:
        signals.append({"name": "Price > EMA50", "type": "bull", "value": f"₹{price:.2f} > ₹{ema50:.2f}", "weight": 2})
        bull_pts += 2
    elif ema50 and price < ema50:
        signals.append({"name": "Price < EMA50", "type": "bear", "value": f"₹{price:.2f} < ₹{ema50:.2f}", "weight": 2})
        bear_pts += 2

    if ema200 and price > ema200:
        signals.append({"name": "Price > EMA200 (Uptrend)", "type": "bull", "value": f"₹{price:.2f} > ₹{ema200:.2f}", "weight": 3})
        bull_pts += 3
    elif ema200 and price < ema200:
        signals.append({"name": "Price < EMA200 (Downtrend)", "type": "bear", "value": f"₹{price:.2f} < ₹{ema200:.2f}", "weight": 3})
        bear_pts += 3

    if ema20 and ema50 and ema20 > ema50:
        signals.append({"name": "EMA20 > EMA50 (Golden)", "type": "bull", "value": f"₹{ema20:.2f} > ₹{ema50:.2f}", "weight": 2})
        bull_pts += 2
    elif ema20 and ema50 and ema20 < ema50:
        signals.append({"name": "EMA20 < EMA50 (Death)", "type": "bear", "value": f"₹{ema20:.2f} < ₹{ema50:.2f}", "weight": 2})
        bear_pts += 2

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_upper, bb_mid, bb_lower = ind["bb"]
    bbu = _last(bb_upper)
    bbl = _last(bb_lower)
    bbm = _last(bb_mid)
    if bbu and bbl:
        if price <= bbl:
            signals.append({"name": "Price at BB Lower (Oversold)", "type": "bull", "value": f"₹{price:.2f}", "weight": 2})
            bull_pts += 2
        elif price >= bbu:
            signals.append({"name": "Price at BB Upper (Overbought)", "type": "bear", "value": f"₹{price:.2f}", "weight": 2})
            bear_pts += 2
        bb_width = (bbu - bbl) / bbm * 100 if bbm else 0
        if bb_width < 5:
            signals.append({"name": "BB Squeeze (Breakout Pending)", "type": "neutral", "value": f"{bb_width:.1f}%", "weight": 1})

    # ── Volume ────────────────────────────────────────────────────────────────
    vols = [d["volume"] for d in data[-20:]]
    avg_vol = sum(vols[:-1]) / max(len(vols)-1, 1)
    cur_vol = data[-1]["volume"]
    if avg_vol > 0:
        vol_ratio = cur_vol / avg_vol
        if vol_ratio > 2 and closes[-1] > closes[-2]:
            signals.append({"name": f"Volume Spike {vol_ratio:.1f}x (Bullish)", "type": "bull", "value": f"{vol_ratio:.1f}x avg", "weight": 2})
            bull_pts += 2
        elif vol_ratio > 2 and closes[-1] < closes[-2]:
            signals.append({"name": f"Volume Spike {vol_ratio:.1f}x (Bearish)", "type": "bear", "value": f"{vol_ratio:.1f}x avg", "weight": 2})
            bear_pts += 2

    # ── Stochastic ────────────────────────────────────────────────────────────
    stoch_k, stoch_d = ind["stoch"]
    sk = _last(stoch_k)
    sd = _last(stoch_d)
    sk_prev = _prev(stoch_k)
    sd_prev = _prev(stoch_d)
    if sk is not None and sd is not None:
        if sk < 20 and sd < 20:
            signals.append({"name": "Stochastic Oversold", "type": "bull", "value": f"K:{sk:.1f} D:{sd:.1f}", "weight": 2})
            bull_pts += 2
        elif sk > 80 and sd > 80:
            signals.append({"name": "Stochastic Overbought", "type": "bear", "value": f"K:{sk:.1f} D:{sd:.1f}", "weight": 2})
            bear_pts += 2
        if sk_prev and sd_prev and sk_prev < sd_prev and sk >= sd:
            signals.append({"name": "Stochastic Bullish Cross", "type": "bull", "value": f"K:{sk:.1f}", "weight": 2})
            bull_pts += 2

    # ── ADX Trend Strength ────────────────────────────────────────────────────
    adx_val = _last(ind["adx14"])
    if adx_val:
        if adx_val > 25:
            signals.append({"name": f"Strong Trend (ADX {adx_val:.1f})", "type": "neutral", "value": f"{adx_val:.1f}", "weight": 1})
        elif adx_val < 20:
            signals.append({"name": f"Weak Trend (ADX {adx_val:.1f})", "type": "neutral", "value": f"{adx_val:.1f}", "weight": 0})

    # ── 52-week levels ────────────────────────────────────────────────────────
    year_closes = [d["close"] for d in data[-252:]]
    if year_closes:
        high52 = max(year_closes)
        low52  = min(year_closes)
        if price >= high52 * 0.98:
            signals.append({"name": "Near 52-Week High", "type": "bull", "value": f"₹{high52:.2f}", "weight": 1})
            bull_pts += 1
        if price <= low52 * 1.02:
            signals.append({"name": "Near 52-Week Low", "type": "bear", "value": f"₹{low52:.2f}", "weight": 1})
            bear_pts += 1

    # ── Score calculation ─────────────────────────────────────────────────────
    total = bull_pts + bear_pts
    score = round((bull_pts / total * 100) if total > 0 else 50)

    if score >= 70:
        summary = "Strong Buy"
    elif score >= 55:
        summary = "Buy"
    elif score >= 45:
        summary = "Neutral"
    elif score >= 30:
        summary = "Sell"
    else:
        summary = "Strong Sell"

    return {
        "symbol":    symbol,
        "price":     price,
        "score":     score,
        "summary":   summary,
        "bull_pts":  bull_pts,
        "bear_pts":  bear_pts,
        "signals":   signals,
        "rsi":       rsi_val,
        "macd":      macd_val,
        "ema20":     ema20,
        "ema50":     ema50,
        "ema200":    ema200,
        "adx":       adx_val,
    }
