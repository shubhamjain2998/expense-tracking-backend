#!/bin/bash
set -e

DB_NAME="expense_tracking"
DB_USER="shubhamjain"

echo "Terminating active connections to '$DB_NAME'..."
psql -U "$DB_USER" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();"

echo "Dropping database '$DB_NAME'..."
dropdb --if-exists -U "$DB_USER" "$DB_NAME"

echo "Creating database '$DB_NAME'..."
createdb -U "$DB_USER" "$DB_NAME"

echo "Running migrations..."
cd "$(dirname "$0")"
source venv/bin/activate
alembic upgrade head

echo "Done."
