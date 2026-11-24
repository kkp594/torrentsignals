# TorrentSignals — Project Steering

## Project Context
Kiroween 2026 Hackathon — Frankenstein Category
Builder: Kailash Kumar

## Tech Stack
- Python 3.8+ with Flask
- Pure HTML/CSS/JavaScript frontend
- yfinance for market data
- BitTorrent-style peer architecture

## Conventions
- Bridge server always on port 5050
- Analysis engine always on port 5002
- Peer IDs stored in sessionStorage (unique per tab)
- All signals stored in memory (no database needed)
- CORS enabled on all endpoints

## Key Design Decisions
1. Browser tabs simulate BitTorrent peers
2. Symbol distribution uses integer division for even splits
3. Peers heartbeat every 15 seconds
4. Stale peers removed after 60 seconds
5. Frontend polls every 2 seconds for new signals

## Running the Project
1. Run start.bat to launch both servers
2. Open frontend/index.html in multiple tabs
3. Each tab = one peer in the swarm
