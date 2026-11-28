#!/usr/bin/env bash
set -euo pipefail

# F2: Generate a random per-boot VNC password file (0600) — never in env or logs
mkdir -p /home/browser/.vnc
VNC_PASS="$(openssl rand -hex 12)"
x11vnc -storepasswd "${VNC_PASS}" /home/browser/.vnc/passwd
chmod 600 /home/browser/.vnc/passwd
unset VNC_PASS

# Start virtual X display & lightweight WM for visible noVNC human intervention
Xvfb :99 -screen 0 1366x900x24 -nolisten tcp &
sleep 1
fluxbox >/dev/null 2>&1 &

# Localhost-only VNC inside container; compose maps 127.0.0.1:6080:6080 only
x11vnc -display :99 -forever -shared -rfbauth /home/browser/.vnc/passwd -listen 127.0.0.1 -rfbport 5900 -bg -o /tmp/x11vnc.log
websockify --web=/usr/share/novnc/ 6080 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &

exec uvicorn interactive_worker:app --host "${BIND_HOST:-0.0.0.0}" --port "${CONTROL_PORT:-7080}"
