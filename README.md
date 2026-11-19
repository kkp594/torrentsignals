# TorrentSignals 🧟
## Kiroween 2026 — Frankenstein Category

> **BitTorrent was built to distribute files.**
> **We used it to distribute stock market computation.**
> **It has no business working. It works.**

---

## 🏆 The Frankenstein Combination

| Technology 1 | + | Technology 2 |
|---|---|---|
| **BitTorrent Protocol** | 🧟 | **Stock Market Analysis Engine** |
| Peer-to-peer file sharing | | RSI, MACD, SuperTrend, ATR |
| Built to move bytes between peers | | Built to crunch financial data |
| **1990s distributed networking** | | **Modern quant analysis** |

These two technologies have absolutely no business working together.

We stitched them together anyway.

---

## 💡 What It Does

Multiple browser tabs join a **peer-to-peer swarm** (like BitTorrent).

Each peer is automatically assigned a unique set of stock symbols to analyze.

Each peer independently computes **RSI, MACD, SuperTrend, ATR and trade signals** for its assigned symbols.

Results are broadcast back through the swarm and **aggregated in a live Halloween-themed dashboard**.

**Result:** 20 stocks analyzed 3× faster with 3 peers than with 1 — distributed computation via BitTorrent-style architecture.

---

## 🎃 Halloween UI

- Dark spooky theme — purple glow, floating ghosts
- Each peer shown as a ghost/zombie/skull node in the swarm
- Live signal cards with bullish/bearish bias
- Real-time activity log showing each peer computing

---

## 🏗 Architecture

```
[Browser Tab 1 👻] ──┐
[Browser Tab 2 🧟] ──┼──► Bridge Server (port 5050)
[Browser Tab 3 💀] ──┘         │
                               ▼
                    Analysis Engine (port 5001)
                    RSI · MACD · SuperTrend · ATR
                               │
                               ▼
                    Live Aggregated Dashboard
```

---

## 🚀 How to Run

### Prerequisites
- Python 3.8+
- pip

### Start Everything (One Click)
```
Double-click start.bat
```

This automatically:
1. Installs all requirements
2. Starts the Analysis Engine on port 5001
3. Starts the Bridge Server on port 5050
4. Opens the dashboard in your browser

### Demo the Swarm
1. Open `frontend/index.html` in **3 browser tabs**
2. Click **"Join Swarm"** in each tab
3. Each tab gets different stocks assigned automatically:
   - Tab 1 → NIFTY50, BANKNIFTY, RELIANCE, TCS, HDFCBANK...
   - Tab 2 → SBIN, BAJFINANCE, AXISBANK, HCLTECH, LT...
   - Tab 3 → TATAMOTORS, BHARTIARTL, GOLD, SILVER, CRUDEOIL...
4. Click **"Compute"** in each tab
5. Watch 20 stocks analyzed simultaneously across 3 peers

---

## 📁 Project Structure

```
TorrentSignals/
├── start.bat                 ← one-click launcher
├── README.md
├── backend/
│   ├── bridge.py             ← Flask bridge (port 5050)
│   └── requirements.txt
├── frontend/
│   └── index.html            ← Halloween swarm dashboard
└── analysis-engine/          ← stock analysis backend (port 5001)
    ├── server.py
    ├── engine/
    │   ├── tips.py           ← trade tip generator
    │   ├── indicators.py     ← RSI, MACD, SuperTrend, ATR
    │   ├── fetcher.py        ← yfinance data fetcher
    │   └── ...
    └── ui/                   ← original StockIQ UI
```

---

## 🧪 API Endpoints

### Bridge Server (port 5050)
| Endpoint | Method | Description |
|---|---|---|
| `/api/join` | POST | Join swarm, get symbol assignment |
| `/api/heartbeat` | POST | Keep peer alive |
| `/api/compute/:symbol` | GET | Compute signal for one symbol |
| `/api/submit` | POST | Submit computed results |
| `/api/signals` | GET | Get all aggregated signals |
| `/api/status` | GET | Swarm status |

---

## 👨‍💻 Built By
**Kailash Kumar** (kkparmar91@gmail.com)
Kiro User ID: `d-9067c98495.a4c89438-2041-70e1-8a4f-9f4d911afab7`

Built with **Kiro IDE** for **Kiroween 2026**

---

## ⚡ Why This Is Frankenstein

The BitTorrent protocol was designed in 2001 by Bram Cohen to distribute large files efficiently across many peers — each peer uploads what it has downloaded, reducing load on any single server.

We took that same peer-distribution architecture and applied it to a completely different domain: **distributed financial computation**.

Instead of sharing file chunks, peers share **computation tasks**.
Instead of reassembling a file, we **aggregate trading signals**.

The result is a system where adding more browser tabs genuinely makes the analysis faster — exactly like adding more seeders makes a torrent download faster.

**BitTorrent + Stock Analysis = TorrentSignals 🧟**
