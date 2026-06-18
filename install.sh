#!/bin/bash
# LIS Suite Installer — Ubuntu/Debian
set -e

REPO_URL="https://github.com/backrouteio/lis-4g.git"
INSTALL_DIR="$HOME/lis-4g"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║       LIS Suite Installer — India 4G LTE                    ║"
echo "║       HI1 / HI2 / HI3 / X1 / X2 / X3                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# 1. System packages
echo "[1/3] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip git

# 2. Clone or pull repo
echo ""
echo "[2/3] Fetching LIS code from GitHub..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "      Repo exists — pulling latest changes..."
    cd "$INSTALL_DIR"
    git pull origin main
else
    echo "      Cloning repo to $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 3. Python packages
echo ""
echo "[3/3] Installing Python packages..."
pip3 install fastapi uvicorn paramiko pyasn1 httpx cryptography \
    --break-system-packages -q

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Installation complete!                                      ║"
echo "║                                                              ║"
echo "║  Location: $INSTALL_DIR"
echo "║                                                              ║"
echo "║  Run ONE of these based on this machine's role:             ║"
echo "║                                                              ║"
echo "║    cd ~/lis-4g && ./start_lis.sh   → LIS Server  :8001     ║"
echo "║    cd ~/lis-4g && ./start_lea.sh   → LEA Agent   :8080     ║"
echo "║    cd ~/lis-4g && ./start_ne.sh    → NE Simulator :9090    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
