#!/usr/bin/env bash
# setup-server.sh — Ubuntu 24.04 VPS provisioning for Unified Agent System
# Run once as root after initial SSH setup.
set -euo pipefail

NEW_USER="${NEW_USER:-deploy}"
SSH_PORT="${SSH_PORT:-22}"
APP_DIR="/opt/unified-agent-system"

echo "[1/7] Updating system packages..."
apt-get update && apt-get -y upgrade
apt-get -y install ufw fail2ban unattended-upgrades ca-certificates curl git sqlite3 openssl gnupg

echo "[2/7] Creating non-root user '${NEW_USER}'..."
id "$NEW_USER" &>/dev/null || adduser --disabled-password --gecos "" "$NEW_USER"
usermod -aG sudo "$NEW_USER"
mkdir -p "/home/${NEW_USER}/.ssh"
if [ -f /root/.ssh/authorized_keys ] && [ ! -f "/home/${NEW_USER}/.ssh/authorized_keys" ]; then
  cp /root/.ssh/authorized_keys "/home/${NEW_USER}/.ssh/authorized_keys"
fi
chown -R "${NEW_USER}:${NEW_USER}" "/home/${NEW_USER}/.ssh"
chmod 700 "/home/${NEW_USER}/.ssh"
chmod 600 "/home/${NEW_USER}/.ssh/authorized_keys" || true

echo "[3/7] Configuring UFW firewall (22/80/443 ONLY; Postgres/Redis/noVNC blocked)..."
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}/tcp"
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "[4/7] Enabling fail2ban & unattended-upgrades..."
systemctl enable --now fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "[5/7] Installing Docker Engine & Compose plugin..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker "$NEW_USER"

echo "[6/7] Hardening kernel sysctl (ptrace_scope=2 for F5)..."
cat <<'EOF' >/etc/sysctl.d/99-unified-agent-security.conf
kernel.yama.ptrace_scope = 2
EOF
sysctl -p /etc/sysctl.d/99-unified-agent-security.conf || true

echo "[7/7] Preparing ${APP_DIR} directory structure..."
mkdir -p "${APP_DIR}"/{data,backups,caddy,secrets}
chown -R "${NEW_USER}:${NEW_USER}" "${APP_DIR}"
chmod 700 "${APP_DIR}/secrets"
chmod 700 "${APP_DIR}/backups"

echo ""
echo "Setup complete."
echo "IMPORTANT: Test SSH login as '${NEW_USER}' in a new terminal BEFORE disabling PasswordAuthentication/PermitRootLogin in /etc/ssh/sshd_config."
echo "noVNC access is localhost-only: ssh -L 6080:127.0.0.1:6080 ${NEW_USER}@YOUR_SERVER_IP"
