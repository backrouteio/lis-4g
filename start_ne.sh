#!/bin/bash
# Start NE Simulator portal
cd "$(dirname "$0")/portal"
echo "Starting NE Simulator portal on port 9090..."
echo "Open: http://$(hostname -I | awk '{print $1}'):9090/ne_simulator.html"
echo "Login: ne_3ng1n33r_4G / N3@S!mul@t0r#4GLTE_2024"
echo ""
python3 -m http.server 9090
