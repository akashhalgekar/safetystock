# All psql runs inside the container, so no local Postgres client is needed.
PSQL = docker exec -i ss-postgres psql -U postgres -d safetystock -v ON_ERROR_STOP=1
PY   = python3
CALC_DATE ?= 2018-08-31

.PHONY: up down data check ddl ingest profile marts run test findings dashboard all reset airflow airflow-down

up:         ## start Postgres and wait until it accepts connections
	docker compose up -d
	@until docker exec ss-postgres pg_isready -U postgres -q 2>/dev/null; do sleep 1; done
	@echo "postgres ready"

down:       ## stop (the volume keeps the data)
	docker compose down

data:       ## download the Olist dataset
	mkdir -p data/raw
	kaggle datasets download -d olistbr/brazilian-ecommerce -p data/raw --unzip

check:      ## validate your own extract against mapping.yaml BEFORE loading
	$(PY) scripts/check_input.py

ddl:        ## schemas + raw tables
	$(PSQL) -f /dev/stdin < sql/001_create_schemas.sql
	$(PSQL) -f /dev/stdin < sql/002_raw_tables.sql

ingest:     ## bulk-load the CSVs via COPY, with row-count verification
	$(PY) -c "import logging;logging.basicConfig(level=logging.INFO,format='%(levelname)-5s %(message)s');\
from pipeline.db import get_conn; from pipeline.ingest import load_raw; load_raw(get_conn())"

profile:    ## surface the data defects BEFORE cleaning anything
	docker exec -i ss-postgres psql -U postgres -d safetystock -f /dev/stdin < sql/003_profile_raw.sql

marts:      ## staging + mart layers
	$(PSQL) -f /dev/stdin < sql/010_staging.sql
	$(PSQL) -f /dev/stdin < sql/020_marts.sql

run:        ## compute, validate, load (CALC_DATE=YYYY-MM-DD to override)
	$(PY) pipeline/run_pipeline.py --calc-date $(CALC_DATE)

test:       ## unit tests, no database needed
	$(PY) -m pytest tests/ -q

findings:   ## print the results
	docker exec -i ss-postgres psql -U postgres -d safetystock -f /dev/stdin < sql/030_findings.sql

dashboard:  ## open the review dashboard
	open dashboard/index.html 2>/dev/null || xdg-open dashboard/index.html

airflow:    ## bring up Airflow on the same network (http://localhost:8080, admin/admin)
	docker compose -f docker-compose.yml -f docker-compose.airflow.yml up -d

airflow-down:
	docker compose -f docker-compose.yml -f docker-compose.airflow.yml down

all: up check ddl ingest profile marts run test findings

reset:      ## destroy everything including the volume
	docker compose -f docker-compose.yml -f docker-compose.airflow.yml down -v
