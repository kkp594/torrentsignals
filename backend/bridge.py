"""
TorrentSignals Bridge Server
Connects WebTorrent swarm peers to the stock analysis engine.
Runs on port 5050.

The analysis engine runs locally on port 5001.
Start it with: cd analysis-engine && python server.py
"""
import json
import time
import threading
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── Config ────────────────────────────────────────────────────────────────────
# Analysis engine — runs from analysis-engine/server.py on port 5001
ANALYSIS_URL = "http://127.0.0.1:5002"
PORT         = 5050

# ── Symbol pool — split across peers ─────────────────────────────────────────
ALL_SYMBOLS = [
    "NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "HDFCBANK",
    "INFY", "ICICIBANK", "SBIN", "BAJFINANCE", "AXISBANK",
    "HCLTECH", "LT", "WIPRO", "MARUTI", "BHARTIARTL",
    "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS", "COPPER",
]

# ── In-memory signal store (shared across peers) ──────────────────────────────
_signals   = {}   # {symbol: {bias, rsi, atr, tip, price, ts}}
_peers     = {}   # {peer_id: {symbols, last_seen, computed}}
_lock      = threading.Lock()


# ── Peer registration — assigns symbols to a new peer ────────────────────────
@app.route("/api/join", methods=["POST"])
def join_swarm():
    """A new peer joins — assign it a unique chunk of symbols."""
    data    = request.get_json(force=True) or {}
    peer_id = data.get("peer_id", f"peer_{int(time.time()*1000)}")

    with _lock:
        # Clean stale peers (no heartbeat in 60s)
        stale = [p for p, v in _peers.items() if time.time() - v["last_seen"] > 60]
        for p in stale:
            del _peers[p]

        # If peer already exists just refresh it
        if peer_id in _peers:
            _peers[peer_id]["last_seen"] = time.time()
            return jsonify({
                "peer_id":     peer_id,
                "symbols":     _peers[peer_id]["symbols"],
                "total_peers": len(_peers),
                "swarm_size":  len(ALL_SYMBOLS),
            })

        # Add new peer first
        _peers[peer_id] = {
            "symbols":   [],
            "last_seen": time.time(),
            "computed":  0,
        }

        # Re-distribute ALL symbols across ALL active peers equally
        all_peer_ids = list(_peers.keys())
        n = len(all_peer_ids)

        for i, pid in enumerate(all_peer_ids):
            # Each peer gets its own slice
            start = (i * len(ALL_SYMBOLS)) // n
            end   = ((i + 1) * len(ALL_SYMBOLS)) // n
            _peers[pid]["symbols"] = ALL_SYMBOLS[start:end]

        my_symbols = _peers[peer_id]["symbols"]

    return jsonify({
        "peer_id":     peer_id,
        "symbols":     my_symbols,
        "total_peers": n,
        "swarm_size":  len(ALL_SYMBOLS),
    })


# ── Heartbeat — keeps peer alive ──────────────────────────────────────────────
@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    data    = request.get_json(force=True) or {}
    peer_id = data.get("peer_id")
    with _lock:
        if peer_id in _peers:
            _peers[peer_id]["last_seen"] = time.time()
    return jsonify({"ok": True})


# ── Fetch signal for one symbol (proxied from analysis server) ────────────────
@app.route("/api/compute/<symbol>")
def compute_signal(symbol):
    """Proxy a signal computation request to the analysis server."""
    sym = symbol.upper()
    try:
        r = requests.get(f"{ANALYSIS_URL}/api/tips/{sym}", timeout=10)
        data = r.json()
        result = {
            "symbol":  sym,
            "price":   data.get("price", 0),
            "bias":    data.get("bias", "neutral"),
            "rsi":     data.get("rsi"),
            "atr":     data.get("atr"),
            "vol":     data.get("vol_ratio"),
            "st_bull": data.get("st_bull"),
            "tip":     _extract_tip(data),
            "ts":      int(time.time()),
            "source":  "analysis_engine",
        }
        # Store in shared signal store
        with _lock:
            _signals[sym] = result
        return jsonify(result)
    except Exception as e:
        # Fallback — return placeholder so peer doesn't crash
        return jsonify({
            "symbol": sym, "price": 0, "bias": "neutral",
            "error": str(e), "ts": int(time.time()),
        })


def _extract_tip(data):
    """Pull the best intraday tip from tips list."""
    tips = data.get("tips", [])
    for t in tips:
        if "Intraday" in (t.get("type") or ""):
            return {
                "type":      t.get("type"),
                "direction": t.get("direction"),
                "entry":     t.get("entry"),
                "sl":        t.get("sl"),
                "target1":   t.get("target1"),
                "confidence":t.get("confidence", 50),
            }
    return None


# ── Submit computed result from a peer ───────────────────────────────────────
@app.route("/api/submit", methods=["POST"])
def submit_result():
    """Peer submits its computed signal."""
    data    = request.get_json(force=True) or {}
    peer_id = data.get("peer_id")
    signals = data.get("signals", [])

    with _lock:
        for sig in signals:
            sym = sig.get("symbol", "").upper()
            if sym:
                _signals[sym] = sig
        if peer_id in _peers:
            _peers[peer_id]["computed"] += len(signals)

    return jsonify({"ok": True, "stored": len(signals)})


# ── Get all aggregated signals (for dashboard) ────────────────────────────────
@app.route("/api/signals")
def get_signals():
    """Return all aggregated signals from all peers."""
    with _lock:
        sigs   = list(_signals.values())
        peers  = [{"id": k, "symbols": v["symbols"],
                   "computed": v["computed"],
                   "active": time.time() - v["last_seen"] < 30}
                  for k, v in _peers.items()]
    return jsonify({
        "signals":    sigs,
        "peers":      peers,
        "peer_count": len([p for p in peers if p["active"]]),
        "computed":   len(sigs),
        "total":      len(ALL_SYMBOLS),
        "ts":         int(time.time()),
    })


# ── Swarm status ──────────────────────────────────────────────────────────────
@app.route("/api/status")
def status():
    with _lock:
        active = sum(1 for v in _peers.values() if time.time() - v["last_seen"] < 30)
    return jsonify({
        "bridge":   "TorrentSignals",
        "version":  "1.0.0",
        "category": "Frankenstein — Kiroween 2026",
        "peers":    active,
        "signals":  len(_signals),
        "analysis": ANALYSIS_URL,
    })


@app.route("/")
def index():
    return jsonify({"status": "TorrentSignals bridge running", "port": PORT})


if __name__ == "__main__":
    print("🧟 TorrentSignals Bridge starting on port", PORT)
    print(f"   Analysis server: {ANALYSIS_URL}")
    print("   Open D:\\TorrentSignals\\frontend\\index.html in multiple tabs")
    app.run(host="0.0.0.0", port=PORT, debug=False)
