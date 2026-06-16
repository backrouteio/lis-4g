#!/bin/bash
# Start NE Simulator (Python — real X1/X2/X3 protocol simulator)
cd "$(dirname "$0")"

LIS_IP="${LIS_IP:-127.0.0.1}"
NE_TYPE="${NE_TYPE:-MME}"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   NE Simulator — India 4G LTE LIS                          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║   X1: HTTP poll  → LIS $LIS_IP:8001                        ║"
echo "║   X2: TCP client → LIS $LIS_IP:4000 (ASN.1 BER)           ║"
echo "║   X3: UDP sender → LIS $LIS_IP:4001 (ULICv08/v1)          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  NE type: $NE_TYPE"
echo "  Usage:   LIS_IP=<ip> NE_TYPE=MME ./start_ne.sh"
echo ""

python3 ne_simulator.py --lis-ip "$LIS_IP" --ne "$NE_TYPE"
