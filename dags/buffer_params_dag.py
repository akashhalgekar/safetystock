"""
Nightly refresh of inventory buffer parameters.

WHY AIRFLOW AND NOT CRON
  - cron has no dependency graph: a failed ingest still lets transform run on
    stale data and publish numbers a planner acts on.
  - cron has no retry: a briefly unreachable source becomes a missed day.
  - cron has no backfill: reprocessing history means hand-running dates.
  - cron has no per-step visibility: you learn "the job failed", not "the
    volume gate failed".

EXECUTION DATE vs WALL CLOCK
  The run stamped 2018-09-01 computes parameters AS OF 2018-09-01 regardless of
  when it actually executes. That is what makes a backfill meaningful, and it
  is why calc_date comes from the DAG context rather than date.today().

VALIDATE SITS BEFORE LOAD
  Bad numbers must never reach the table a planner reads. Failing the run is
  cheaper than publishing and retracting.
"""
from datetime import datetime, timedelta
import os
import sys

from airflow import DAG

# PythonOperator moved packages in Airflow 3. Import either way so this DAG
# runs on both 2.x and 3.x without edits.
try:                                                    # Airflow 3
    from airflow.providers.standard.operators.python import PythonOperator
except ImportError:                                     # Airflow 2
    from airflow.operators.python import PythonOperator

PROJECT_DIR = os.environ.get("PROJECT_DIR", "/opt/airflow/project")
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

default_args = {
    "owner": "supply-chain-analytics",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def _ingest(**_):
    from pipeline.db import get_conn
    from pipeline.ingest import load_raw
    conn = get_conn()
    try:
        return load_raw(conn)
    finally:
        conn.close()


def _transform(**context):
    from pipeline.db import get_engine
    from pipeline import buffer_engine as be
    from pipeline.run_pipeline import CFG
    eng = get_engine()
    demand = be.extract_demand(eng, CFG["demand_window_weeks"])
    fulfil, rev = be.extract_fulfilment(eng), be.extract_revenue(eng)
    if demand.empty or fulfil.empty:
        raise ValueError("empty source data; refusing to publish parameters")
    params = be.compute_buffers(be.demand_stats(demand), fulfil, rev, CFG)
    # Parameters go to a run-scoped parquet file rather than XCom: XCom is for
    # small messages, not DataFrames.
    path = f"/tmp/buffer_params_{context['run_id'].replace(':', '_')}.parquet"
    params.to_parquet(path)
    context["ti"].xcom_push(key="params_path", value=path)
    return len(params)


def _validate(**context):
    import pandas as pd
    from pipeline.db import get_conn
    from pipeline.quality import run_checks
    path = context["ti"].xcom_pull(key="params_path", task_ids="compute_parameters")
    params = pd.read_parquet(path)
    conn = get_conn()
    try:
        run_checks(params, conn)          # raises DataQualityError -> fails run
    finally:
        conn.close()


def _load(**context):
    import pandas as pd
    from pipeline.db import get_conn
    from pipeline.load import load_params
    path = context["ti"].xcom_pull(key="params_path", task_ids="compute_parameters")
    params = pd.read_parquet(path)
    calc_date = context["data_interval_end"].date()      # NOT date.today()
    conn = get_conn()
    try:
        return load_params(conn, params, calc_date)
    finally:
        conn.close()


with DAG(
    dag_id="buffer_params_refresh",
    description="Recompute inventory buffer parameters from transaction data",
    start_date=datetime(2018, 1, 1),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["supply-chain", "inventory"],
) as dag:
    ingest    = PythonOperator(task_id="ingest_raw",         python_callable=_ingest)
    transform = PythonOperator(task_id="compute_parameters", python_callable=_transform)
    validate  = PythonOperator(task_id="validate_quality",   python_callable=_validate)
    load      = PythonOperator(task_id="load_parameters",    python_callable=_load)

    ingest >> transform >> validate >> load
