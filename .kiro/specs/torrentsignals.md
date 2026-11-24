# TorrentSignals — Kiro Spec

## Overview
TorrentSignals is a Frankenstein project that stitches BitTorrent peer-to-peer networking with real stock market analysis.

## Requirements

### Functional Requirements
1. Multiple browser tabs must be able to join a shared peer swarm
2. Each peer must receive a unique set of stock symbols to analyze
3. Each peer must independently compute RSI, MACD, SuperTrend and ATR
4. Results must aggregate in real time across all peers
5. Dashboard must display live signals with Entry, SL and Target
6. Halloween-themed UI with ghost/zombie peer visualization

### Non-Functional Requirements
1. Bridge server must run on port 5050
2. Analysis engine must run on port 5001/5002
3. Frontend must work as a single HTML file
4. No external frontend frameworks or dependencies
5. Peers must be identified by unique session IDs

## Architecture

### Components
1. **Bridge Server** (backend/bridge.py)
   - Flask REST API on port 5050
   - Manages peer registration and symbol distribution
   - Aggregates signals from all peers
   - Proxies computation requests to analysis engine

2. **Analysis Engine** (analysis-engine/server.py)
   - Flask REST API on port 5001/5002
   - Computes RSI, MACD, SuperTrend, ATR per symbol
   - Returns bias (bullish/bearish/neutral) and trade tips
   - Fetches live market data via yfinance

3. **Frontend Dashboard** (frontend/index.html)
   - Pure HTML/CSS/JS — no frameworks
   - Halloween themed — dark purple, floating ghosts
   - Polls bridge server every 2 seconds
   - Renders signal cards per stock

### API Endpoints
- POST /api/join — peer joins swarm, gets symbol assignment
- POST /api/heartbeat — keeps peer alive
- GET  /api/compute/:symbol — compute signal for one symbol
- POST /api/submit — peer submits computed results
- GET  /api/signals — get all aggregated signals
- GET  /api/status — swarm status

## Tasks

- [x] Create bridge server with peer registration
- [x] Implement symbol distribution algorithm
- [x] Build analysis engine integration
- [x] Create Halloween dashboard UI
- [x] Add swarm visualization with peer nodes
- [x] Implement real-time signal aggregation
- [x] Add activity log
- [x] Test with 3 simultaneous peers
- [x] Push to GitHub
- [x] Submit to Kiroween 2026
