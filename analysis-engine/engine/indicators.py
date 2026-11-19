"""
Pure-math technical indicators. No external TA library needed.
Input: list of OHLCV dicts from fetcher.py
"""
import math


def _closes(data): return [d["close"] for d in data]
def _highs(data):  return [d["high"]  for d in data]
def _lows(data):   return [d["low"]   for d in data]
def _vols(data):   return [d["volume"] for d in data]


def ema(values: list, period: int) -> list:
    if len(values) < period:
        return [None] * len(values)
    k = 2 / (period + 1)
    result = [None] * (period - 1)
    result.append(sum(values[:period]) / period)
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def sma(values: list, period: int) -> list:
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i-period+1:i+1]) / period)
    return result


def rsi(values: list, period: int = 14) -> list:
    result = [None] * period
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = values[i] - values[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(round(100 - 100 / (1 + rs), 2))
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i-1]
        avg_gain = (avg_gain * (period-1) + max(diff, 0)) / period
        avg_loss = (avg_loss * (period-1) + max(-diff, 0)) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(round(100 - 100 / (1 + rs), 2))
    return result


def macd(values: list, fast=12, slow=26, signal=9):
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line = [
        round(f - s, 4) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [v for v in macd_line if v is not None]
    sig_raw = ema(valid, signal)
    sig_padded = [None] * (len(macd_line) - len(sig_raw)) + sig_raw
    histogram = [
        round(m - s, 4) if m is not None and s is not None else None
        for m, s in zip(macd_line, sig_padded)
    ]
    return macd_line, sig_padded, histogram


def bollinger_bands(values: list, period=20, std_dev=2):
    if not values:
        return [], [], []
    mid = sma(values, period)
    upper, lower = [], []
    for i in range(len(values)):
        if mid[i] is None:
            upper.append(None); lower.append(None)
        else:
            window = values[i-period+1:i+1]
            mean = mid[i]
            variance = sum((x - mean)**2 for x in window) / period
            sd = math.sqrt(variance)
            upper.append(round(mean + std_dev * sd, 2))
            lower.append(round(mean - std_dev * sd, 2))
    return upper, [round(m, 2) if m else None for m in mid], lower


def atr(data: list, period=14) -> list:
    if not data:
        return []
    trs = [None]
    for i in range(1, len(data)):
        hl = data[i]["high"] - data[i]["low"]
        hc = abs(data[i]["high"] - data[i-1]["close"])
        lc = abs(data[i]["low"]  - data[i-1]["close"])
        trs.append(max(hl, hc, lc))
    result = [None] * period
    valid = [t for t in trs if t is not None]
    if len(valid) < period:
        return result
    atr_val = sum(valid[:period]) / period
    result.append(round(atr_val, 2))
    for t in valid[period:]:
        atr_val = (atr_val * (period-1) + t) / period
        result.append(round(atr_val, 2))
    return result


def stochastic(data: list, k_period=14, d_period=3):
    if not data:
        return [], []
    k_vals = []
    for i in range(len(data)):
        if i < k_period - 1:
            k_vals.append(None)
        else:
            window = data[i-k_period+1:i+1]
            low_min  = min(d["low"]  for d in window)
            high_max = max(d["high"] for d in window)
            if high_max == low_min:
                k_vals.append(50.0)
            else:
                k = (data[i]["close"] - low_min) / (high_max - low_min) * 100
                k_vals.append(round(k, 2))
    valid_k = [v for v in k_vals if v is not None]
    d_raw = sma(valid_k, d_period)
    d_vals = [None] * (len(k_vals) - len(d_raw)) + d_raw
    return k_vals, d_vals


def obv(data: list) -> list:
    if not data:
        return []
    result = [0]
    for i in range(1, len(data)):
        if data[i]["close"] > data[i-1]["close"]:
            result.append(result[-1] + data[i]["volume"])
        elif data[i]["close"] < data[i-1]["close"]:
            result.append(result[-1] - data[i]["volume"])
        else:
            result.append(result[-1])
    return result


def adx(data: list, period=14) -> list:
    """Average Directional Index."""
    if not data or len(data) < period + 1:
        return [None] * len(data)
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(data)):
        up   = data[i]["high"] - data[i-1]["high"]
        down = data[i-1]["low"] - data[i]["low"]
        plus_dm.append(up   if up > down and up > 0   else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        hl = data[i]["high"] - data[i]["low"]
        hc = abs(data[i]["high"] - data[i-1]["close"])
        lc = abs(data[i]["low"]  - data[i-1]["close"])
        trs.append(max(hl, hc, lc))

    def _smooth(vals, p):
        s = [sum(vals[:p])]
        for v in vals[p:]:
            s.append(s[-1] - s[-1]/p + v)
        return s

    atr_s  = _smooth(trs, period)
    pdm_s  = _smooth(plus_dm, period)
    mdm_s  = _smooth(minus_dm, period)
    pdi = [100 * p/a if a else 0 for p, a in zip(pdm_s, atr_s)]
    mdi = [100 * m/a if a else 0 for m, a in zip(mdm_s, atr_s)]
    dx  = [100 * abs(p-m)/(p+m) if (p+m) else 0 for p, m in zip(pdi, mdi)]
    adx_vals = sma(dx, period)
    return [None] * (len(data) - len(adx_vals)) + [round(v, 2) if v else None for v in adx_vals]


def vwap(data: list) -> list:
    if not data:
        return []
    result = []
    cum_pv = cum_v = 0
    for d in data:
        typical = (d["high"] + d["low"] + d["close"]) / 3
        cum_pv += typical * d["volume"]
        cum_v  += d["volume"]
        result.append(round(cum_pv / cum_v, 2) if cum_v else None)
    return result


def supertrend(data: list, period: int = 7, multiplier: float = 3.0) -> tuple:
    """
    Supertrend indicator — most popular intraday trend indicator in India.

    Parameters
    ----------
    period     : ATR lookback period (default 7)
    multiplier : ATR multiplier for band width (default 3.0)

    Returns
    -------
    (supertrend, direction)
    supertrend  : list of float — the supertrend line value at each bar
    direction   : list of int  — +1 = bullish (price above ST), -1 = bearish
    """
    if not data or len(data) < period + 1:
        n = len(data) if data else 0
        return [None] * n, [0] * n

    # Compute ATR
    highs  = [d["high"]  for d in data]
    lows   = [d["low"]   for d in data]
    closes = [d["close"] for d in data]

    tr_list = [None]
    for i in range(1, len(data)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i]  - closes[i-1])
        lc = abs(lows[i]   - closes[i-1])
        tr_list.append(max(hl, hc, lc))

    # Wilder smoothed ATR
    atr_arr = [None] * period
    valid_tr = [t for t in tr_list if t is not None]
    if len(valid_tr) < period:
        return [None] * len(data), [0] * len(data)

    atr_val = sum(valid_tr[:period]) / period
    atr_arr.append(round(atr_val, 4))
    for t in valid_tr[period:]:
        atr_val = (atr_val * (period - 1) + t) / period
        atr_arr.append(round(atr_val, 4))

    n = len(data)
    upper_band = [None] * n
    lower_band = [None] * n
    st         = [None] * n
    direction  = [0]    * n

    for i in range(period, n):
        if atr_arr[i] is None:
            continue
        hl_avg = (highs[i] + lows[i]) / 2
        upper_band[i] = round(hl_avg + multiplier * atr_arr[i], 2)
        lower_band[i] = round(hl_avg - multiplier * atr_arr[i], 2)

    # Supertrend logic
    for i in range(period, n):
        if upper_band[i] is None:
            continue

        # Adjust bands to not widen on each candle
        if i > period:
            prev_ub = upper_band[i-1]
            prev_lb = lower_band[i-1]
            if prev_ub is not None:
                upper_band[i] = min(upper_band[i], prev_ub) if closes[i-1] <= prev_ub else upper_band[i]
            if prev_lb is not None:
                lower_band[i] = max(lower_band[i], prev_lb) if closes[i-1] >= prev_lb else lower_band[i]

        prev_st  = st[i-1]
        prev_dir = direction[i-1]

        if prev_st is None:
            # First value — use upper band
            st[i]        = upper_band[i]
            direction[i] = -1
        elif prev_st == (upper_band[i-1] if upper_band[i-1] is not None else prev_st):
            # Was bearish
            if closes[i] > upper_band[i]:
                st[i]        = lower_band[i]
                direction[i] = 1
            else:
                st[i]        = upper_band[i]
                direction[i] = -1
        else:
            # Was bullish
            if closes[i] < lower_band[i]:
                st[i]        = upper_band[i]
                direction[i] = -1
            else:
                st[i]        = lower_band[i]
                direction[i] = 1

    return st, direction


def stoch_rsi(closes: list, rsi_period: int = 14, stoch_period: int = 14,
              k_smooth: int = 3, d_smooth: int = 3) -> tuple:
    """
    Stochastic RSI — momentum indicator on RSI values.
    More sensitive than plain RSI; shows overbought/oversold faster.

    Returns (k, d) — both lists, values 0-100
    """
    rsi_vals = rsi(closes, rsi_period)
    k_raw = []
    for i in range(len(rsi_vals)):
        if rsi_vals[i] is None or i < stoch_period - 1:
            k_raw.append(None)
            continue
        window = [v for v in rsi_vals[i - stoch_period + 1: i + 1] if v is not None]
        if len(window) < stoch_period:
            k_raw.append(None)
            continue
        lo = min(window)
        hi = max(window)
        if hi == lo:
            k_raw.append(50.0)
        else:
            k_raw.append(round((rsi_vals[i] - lo) / (hi - lo) * 100, 2))

    # Smooth K with SMA
    valid_k = [v for v in k_raw if v is not None]
    k_smoothed_raw = sma(valid_k, k_smooth)
    k_pad = [None] * (len(k_raw) - len(k_smoothed_raw)) + k_smoothed_raw

    # D = SMA of smoothed K
    valid_ks = [v for v in k_pad if v is not None]
    d_raw = sma(valid_ks, d_smooth)
    d_pad = [None] * (len(k_pad) - len(d_raw)) + d_raw

    return k_pad, d_pad


def squeeze_momentum(data: list, bb_period: int = 20, bb_mult: float = 2.0,
                     kc_period: int = 20, kc_mult: float = 1.5) -> tuple:
    """
    TTM Squeeze Momentum — John Carter's squeeze indicator.
    Measures when Bollinger Bands contract inside Keltner Channels (the squeeze).

    Returns:
      (histogram, squeeze_direction)
      histogram         : list of float  — momentum values (positive = bullish)
      squeeze_direction : list of int    — 1 = squeeze ON (low volatility), 0 = no squeeze
    """
    closes = _closes(data)
    highs  = _highs(data)
    lows   = _lows(data)
    n = len(data)

    if n < bb_period:
        return [None] * n, [0] * n

    # Bollinger Bands
    bb_mid_arr = sma(closes, bb_period)
    bb_upper_arr = []
    bb_lower_arr = []
    for i in range(n):
        if bb_mid_arr[i] is None:
            bb_upper_arr.append(None)
            bb_lower_arr.append(None)
        else:
            window = closes[i - bb_period + 1: i + 1]
            mean   = bb_mid_arr[i]
            sd     = math.sqrt(sum((x - mean) ** 2 for x in window) / bb_period)
            bb_upper_arr.append(mean + bb_mult * sd)
            bb_lower_arr.append(mean - bb_mult * sd)

    # Keltner Channels
    atr_arr = atr(data, kc_period)
    kc_upper_arr = []
    kc_lower_arr = []
    kc_mid_arr   = sma(closes, kc_period)
    for i in range(n):
        if kc_mid_arr[i] is None or atr_arr[i] is None:
            kc_upper_arr.append(None)
            kc_lower_arr.append(None)
        else:
            kc_upper_arr.append(kc_mid_arr[i] + kc_mult * atr_arr[i])
            kc_lower_arr.append(kc_mid_arr[i] - kc_mult * atr_arr[i])

    # Squeeze: BB inside KC
    squeeze_dir = []
    for i in range(n):
        bu, bl = bb_upper_arr[i], bb_lower_arr[i]
        ku, kl = kc_upper_arr[i], kc_lower_arr[i]
        if None in (bu, bl, ku, kl):
            squeeze_dir.append(0)
        else:
            squeeze_dir.append(1 if (bu < ku and bl > kl) else 0)

    # Momentum: Linear regression of delta (close - midpoint of highest high/lowest low)
    histogram = []
    for i in range(n):
        if i < bb_period - 1:
            histogram.append(None)
            continue
        win_h = max(highs[i - bb_period + 1: i + 1])
        win_l = min(lows [i - bb_period + 1: i + 1])
        mid   = (win_h + win_l) / 2
        delta = closes[i] - (mid + bb_mid_arr[i]) / 2 if bb_mid_arr[i] else 0

        # Linear regression value of delta over bb_period
        window_c = closes[i - bb_period + 1: i + 1]
        x_mean   = (bb_period - 1) / 2
        xy_sum   = sum((j - x_mean) * (c - sum(window_c) / bb_period)
                       for j, c in enumerate(window_c))
        xx_sum   = sum((j - x_mean) ** 2 for j in range(bb_period))
        slope    = xy_sum / xx_sum if xx_sum else 0
        intercept = sum(window_c) / bb_period - slope * x_mean
        linreg_val = slope * (bb_period - 1) + intercept

        histogram.append(round(linreg_val - sum(window_c) / bb_period, 4))

    return histogram, squeeze_dir


def compute_all(data: list) -> dict:
    """Compute all indicators and return as dict of lists."""
    if not data:
        return {}
    closes = _closes(data)
    st, st_dir = supertrend(data, 7, 3.0)
    stoch_k, stoch_d = stochastic(data)
    srsi_k, srsi_d   = stoch_rsi(closes)
    sq_hist, sq_dir  = squeeze_momentum(data)
    return {
        "ema20":        ema(closes, 20),
        "ema50":        ema(closes, 50),
        "ema100":       ema(closes, 100),
        "ema200":       ema(closes, 200),
        "sma20":        sma(closes, 20),
        "rsi14":        rsi(closes, 14),
        "macd":         macd(closes),
        "bb":           bollinger_bands(closes),
        "atr14":        atr(data, 14),
        "stoch":        stochastic(data),
        "obv":          obv(data),
        "adx14":        adx(data, 14),
        "vwap":         vwap(data),
        "supertrend":   st,
        "st_direction": st_dir,
        "stoch_rsi_k":  srsi_k,
        "stoch_rsi_d":  srsi_d,
        "squeeze_hist": sq_hist,
        "squeeze_dir":  sq_dir,
    }
