#!/bin/bash
# LIS Suite Installer — Ubuntu/Debian
set -e

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║       LIS Suite Installer — India 4G LTE                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip git

pip3 install fastapi uvicorn paramiko pyasn1 httpx cryptography --break-system-packages -q

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Done! Run one of:                                          ║"
echo "║    ./start_lis.sh      → LIS Server  (port 8001)           ║"
echo "║    ./start_lea.sh      → LEA Agent   (ports 2222/8443/8080)║"
echo "║    ./start_ne.sh       → NE Simulator (port 9090)          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
