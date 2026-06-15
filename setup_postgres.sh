#!/bin/bash
# ─────────────────────────────────────────────────────────────
# LIS PostgreSQL Setup — run once on Ubuntu server
# Usage: chmod +x setup_postgres.sh && sudo ./setup_postgres.sh
# ─────────────────────────────────────────────────────────────

set -e

DB_NAME="lisdb"
DB_USER="lis"
DB_PASS="LisSecure2024!"   # change this before running

echo "==> Installing PostgreSQL..."
apt-get update -qq
apt-get install -y postgresql postgresql-contrib

echo "==> Starting PostgreSQL..."
systemctl enable postgresql
systemctl start postgresql

echo "==> Creating database and user..."
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;

SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec

GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL

echo ""
echo "==> PostgreSQL ready!"
echo ""
echo "    DB:   ${DB_NAME}"
echo "    User: ${DB_USER}"
echo "    Pass: ${DB_PASS}  ← CHANGE THIS"
echo ""
echo "==> Run LIS with PostgreSQL:"
echo "    python3 run_standalone.py --db-url postgresql://${DB_USER}:${DB_PASS}@localhost/${DB_NAME}"
echo ""
echo "==> Or set it permanently in a .env file:"
echo "    echo 'DB_URL=postgresql://${DB_USER}:${DB_PASS}@localhost/${DB_NAME}' > .env"
