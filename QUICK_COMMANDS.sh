#!/bin/bash
# LIS Systemctl Quick Reference Commands
# Copy-paste these commands for quick operations

# ============================================================
# DEPLOYMENT
# ============================================================

# Deploy all services
deploy_all() {
    echo "Deploying all LIS services..."
    sudo cp lis-server.service lea-agent.service ne-simulator.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable lis-server lea-agent ne-simulator
    sudo systemctl start lis-server lea-agent ne-simulator
    sudo systemctl status lis-server lea-agent ne-simulator
}

# ============================================================
# STATUS CHECKS
# ============================================================

# Check all services status
status_all() {
    echo "=== LIS SERVER ==="
    sudo systemctl status lis-server --no-pager
    echo ""
    echo "=== LEA AGENT ==="
    sudo systemctl status lea-agent --no-pager
    echo ""
    echo "=== NE SIMULATOR ==="
    sudo systemctl status ne-simulator --no-pager
}

# Quick status (one-liner)
quick_status() {
    sudo systemctl is-active lis-server lea-agent ne-simulator
}

# ============================================================
# VIEW LOGS (tail -f)
# ============================================================

# Follow LIS Server logs in real-time
logs_lis() {
    sudo tail -f /var/log/lis-server.log
}

# Follow LEA Agent logs in real-time
logs_lea() {
    sudo tail -f /var/log/lea-agent.log
}

# Follow NE Simulator logs in real-time
logs_ne() {
    sudo tail -f /var/log/ne-simulator.log
}

# Follow all logs simultaneously (opens 3 windows)
logs_all() {
    echo "Opening 3 log windows..."
    gnome-terminal --tab -t "LIS" -e "bash -c 'sudo tail -f /var/log/lis-server.log'" \
                   --tab -t "LEA" -e "bash -c 'sudo tail -f /var/log/lea-agent.log'" \
                   --tab -t "NE"  -e "bash -c 'sudo tail -f /var/log/ne-simulator.log'"
}

# Follow via journalctl (systemd logs)
logs_journal_lis() {
    sudo journalctl -u lis-server.service -f
}

logs_journal_lea() {
    sudo journalctl -u lea-agent.service -f
}

logs_journal_ne() {
    sudo journalctl -u ne-simulator.service -f
}

# ============================================================
# RESTART SERVICES
# ============================================================

# Restart all services
restart_all() {
    echo "Restarting all services..."
    sudo systemctl restart lis-server lea-agent ne-simulator
    sleep 2
    sudo systemctl status lis-server lea-agent ne-simulator
}

# Restart individual services
restart_lis() {
    sudo systemctl restart lis-server
}

restart_lea() {
    sudo systemctl restart lea-agent
}

restart_ne() {
    sudo systemctl restart ne-simulator
}

# ============================================================
# STOP SERVICES
# ============================================================

# Stop all services
stop_all() {
    echo "Stopping all services..."
    sudo systemctl stop lis-server lea-agent ne-simulator
    sudo systemctl status lis-server lea-agent ne-simulator
}

# Start all services
start_all() {
    echo "Starting all services..."
    sudo systemctl start lis-server lea-agent ne-simulator
    sleep 2
    sudo systemctl status lis-server lea-agent ne-simulator
}

# ============================================================
# LOG ANALYSIS
# ============================================================

# View last N lines of all logs
view_logs_tail() {
    lines=${1:-50}
    echo "=== LIS Server (last $lines lines) ==="
    sudo tail -n $lines /var/log/lis-server.log
    echo ""
    echo "=== LEA Agent (last $lines lines) ==="
    sudo tail -n $lines /var/log/lea-agent.log
    echo ""
    echo "=== NE Simulator (last $lines lines) ==="
    sudo tail -n $lines /var/log/ne-simulator.log
}

# Find all errors in logs
find_errors() {
    echo "=== Errors in LIS Server ==="
    sudo grep -i "error\|failed\|exception" /var/log/lis-server.log | tail -20
    echo ""
    echo "=== Errors in LEA Agent ==="
    sudo grep -i "error\|failed\|exception" /var/log/lea-agent.log | tail -20
    echo ""
    echo "=== Errors in NE Simulator ==="
    sudo grep -i "error\|failed\|exception" /var/log/ne-simulator.log | tail -20
}

# View logs from last hour
logs_since_hour() {
    echo "=== LIS Server (last hour) ==="
    sudo journalctl -u lis-server.service --since "1 hour ago" -n 100
    echo ""
    echo "=== LEA Agent (last hour) ==="
    sudo journalctl -u lea-agent.service --since "1 hour ago" -n 100
    echo ""
    echo "=== NE Simulator (last hour) ==="
    sudo journalctl -u ne-simulator.service --since "1 hour ago" -n 100
}

# View logs with warnings only
logs_warnings() {
    echo "=== LIS Server Warnings ==="
    sudo journalctl -u lis-server.service -p warning
    echo ""
    echo "=== LEA Agent Warnings ==="
    sudo journalctl -u lea-agent.service -p warning
    echo ""
    echo "=== NE Simulator Warnings ==="
    sudo journalctl -u ne-simulator.service -p warning
}

# ============================================================
# TROUBLESHOOTING
# ============================================================

# Check if ports are in use
check_ports() {
    echo "Checking LIS ports (8001, 4000, 4001)..."
    sudo netstat -tlnp | grep -E "8001|4000|4001"
    echo ""
    echo "Checking LEA ports (2222, 8443, 8080)..."
    sudo netstat -tlnp | grep -E "2222|8443|8080"
}

# Check process resource usage
check_resources() {
    echo "=== Process Resource Usage ==="
    ps aux | grep -E "run_standalone|lea_sftp_server|ne_simulator" | grep -v grep
}

# Full system diagnosis
diagnose() {
    echo "======== LIS SYSTEM DIAGNOSIS ========"
    echo ""
    echo "1. Service Status:"
    sudo systemctl status lis-server lea-agent ne-simulator --no-pager
    echo ""
    echo "2. Port Usage:"
    check_ports
    echo ""
    echo "3. Process Resources:"
    check_resources
    echo ""
    echo "4. Recent Errors:"
    find_errors
    echo ""
    echo "======== END DIAGNOSIS ========"
}

# ============================================================
# UTILITIES
# ============================================================

# Backup logs
backup_logs() {
    backup_file="/root/lis-logs-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    echo "Backing up logs to $backup_file..."
    sudo tar czf $backup_file /var/log/lis-*.log /var/log/lea-*.log /var/log/ne-*.log
    ls -lh $backup_file
}

# Clear old logs (keep only last 7 days)
clear_old_logs() {
    echo "Clearing logs older than 7 days..."
    sudo find /var/log -name "lis-*.log*" -o -name "lea-*.log*" -o -name "ne-*.log*" | xargs sudo find -mtime +7 -delete
    ls -lh /var/log/lis-*.log /var/log/lea-*.log /var/log/ne-*.log 2>/dev/null | head -20
}

# ============================================================
# DASHBOARD ACCESS
# ============================================================

# Open all dashboards in browser
open_dashboards() {
    echo "Opening dashboards..."
    echo "  LIS Dashboard:    http://10.80.20.56:8001/"
    echo "  LEA Portal:       http://10.80.20.45:8080/"
    echo "  NE Simulator:     http://10.80.20.62:8080/"
    # Uncomment to auto-open (requires browser)
    # xdg-open http://10.80.20.56:8001/ &
    # xdg-open http://10.80.20.45:8080/ &
    # xdg-open http://10.80.20.62:8080/ &
}

# ============================================================
# SHOW HELP
# ============================================================

show_help() {
    cat << 'EOF'
LIS Systemctl Quick Commands
============================

DEPLOYMENT:
  deploy_all          Deploy all services and enable auto-start

STATUS:
  status_all          Check status of all 3 services
  quick_status        One-liner status check
  check_ports         Verify all ports are listening
  check_resources     View process resource usage

LOGS (Real-time tail -f):
  logs_lis            Follow LIS Server logs
  logs_lea            Follow LEA Agent logs
  logs_ne             Follow NE Simulator logs
  logs_all            Open 3 log windows (requires gnome-terminal)

LOGS (Systemd journalctl):
  logs_journal_lis    Follow LIS via journalctl
  logs_journal_lea    Follow LEA via journalctl
  logs_journal_ne     Follow NE via journalctl

LOGS (Analysis):
  view_logs_tail [N]  View last N lines of all logs (default: 50)
  find_errors         Search for errors in all logs
  logs_since_hour     View logs from last hour
  logs_warnings       View warnings only

RESTART:
  restart_all         Restart all services
  restart_lis         Restart LIS Server only
  restart_lea         Restart LEA Agent only
  restart_ne          Restart NE Simulator only

STOP/START:
  stop_all            Stop all services
  start_all           Start all services

TROUBLESHOOTING:
  diagnose            Full system diagnosis (status, ports, resources, errors)

UTILITIES:
  backup_logs         Backup all logs to tar.gz
  clear_old_logs      Delete logs older than 7 days
  open_dashboards     Show dashboard URLs

HELP:
  show_help           Show this help message

USAGE EXAMPLES:
  source QUICK_COMMANDS.sh
  status_all
  logs_lis
  logs_all
  restart_all
  diagnose
EOF
}

# Show help if no arguments
if [ $# -eq 0 ]; then
    show_help
fi
