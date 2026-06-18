#!/bin/bash
# Start LEA Agent
cd "$(dirname "$0")"
echo "Starting LEA Agent..."
echo "SFTP (HI3):   port 2222"
echo "HI2 Receiver: port 8443"
echo "LEA Portal:   http://$(hostname -I | awk '{print $1}'):8080"
echo "Login:        lea_0ff!c3r_1B / L3A@0ff!c3r#IB_2024\$MHA"
echo ""
python3 lea_sftp_server.py --sftp-port 2222 --hi2-port 8443 --portal-port 8080
