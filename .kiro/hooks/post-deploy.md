# Post Deploy Hook

After any changes to bridge.py or analysis-engine:
1. Restart bridge server: python backend/bridge.py
2. Restart analysis engine: python analysis-engine/server.py
3. Hard refresh all browser tabs: Ctrl+Shift+R
4. Re-join swarm in each tab
5. Click Compute to verify signals flow correctly
