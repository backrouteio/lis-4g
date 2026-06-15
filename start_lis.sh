#!/bin/bash
# Start LIS Server
cd "$(dirname "$0")"
echo "Starting LIS Server on port 8001..."
echo "Portal:   http://$(hostname -I | awk '{print $1}'):8001"
echo "API Docs: http://$(hostname -I | awk '{print $1}'):8001/docs"
echo "Login:    lis_adm!n_d0t / L!S@Adm1n#2024\$IN_D0T"
echo ""
python3 run_standalone.py --host 0.0.0.0 --port 8001
