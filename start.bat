@echo off
echo.
echo  TorrentSignals - Kiroween 2026 Frankenstein Entry
echo  ===================================================
echo  BitTorrent + Stock Analysis = Frankenstein
echo.

echo [1/2] Installing analysis engine requirements...
cd /d D:\TorrentSignals\analysis-engine
pip install -r requirements.txt -q

echo [2/2] Installing bridge requirements...
cd /d D:\TorrentSignals\backend
pip install flask flask-cors requests -q

echo.
echo Starting Analysis Engine on port 5001...
start "Analysis Engine" cmd /k "cd /d D:\TorrentSignals\analysis-engine && python server.py"

echo Waiting for analysis engine to start...
timeout /t 4 /nobreak >nul

echo Starting TorrentSignals Bridge on port 5050...
start "TorrentSignals Bridge" cmd /k "cd /d D:\TorrentSignals\backend && python bridge.py"

echo Waiting for bridge to start...
timeout /t 2 /nobreak >nul

echo.
echo ===================================================
echo  BOTH SERVERS RUNNING!
echo.
echo  Analysis Engine : http://localhost:5001
echo  Bridge Server   : http://localhost:5050
echo.
echo  NOW:
echo  1. Open frontend\index.html in your browser
echo  2. Open it in 2 more tabs (Ctrl+T)
echo  3. Click "Join Swarm" in each tab
echo  4. Click "Compute" in each tab
echo  5. Watch the swarm distribute computation!
echo ===================================================
echo.

start "" "D:\TorrentSignals\frontend\index.html"
pause
