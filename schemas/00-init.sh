#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
    CREATE DATABASE catalog;
    CREATE DATABASE mlflow;
    CREATE DATABASE labelstudio;
    CREATE DATABASE lakefs;
    GRANT ALL PRIVILEGES ON DATABASE catalog TO $POSTGRES_USER;
    GRANT ALL PRIVILEGES ON DATABASE mlflow TO $POSTGRES_USER;
    GRANT ALL PRIVILEGES ON DATABASE labelstudio TO $POSTGRES_USER;
    GRANT ALL PRIVILEGES ON DATABASE lakefs TO $POSTGRES_USER;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname catalog -f /schema/01_catalog.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname catalog -f /schema/02_issues.sql
