"""
Idempotent load.

ON CONFLICT DO UPDATE means rerunning for the same calc_date reproduces the
table rather than doubling it. Idempotency is PER calc_date: the primary key
includes it, so a rerun upserts while a new date adds a snapshot. The table is
therefore a time series of parameter history, which is what answers "why was
the number different last quarter".

Everything about retries and backfills depends on this property.
"""
import logging
from psycopg2.extras import execute_values

log = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS mart.buffer_params (
    calc_date            date    NOT NULL,
    category             text    NOT NULL,
    seller_id            text    NOT NULL,
    abc                  text,
    xyz                  text,
    service_level        numeric,
    demand_mean          numeric,
    demand_sd            numeric,
    zero_week_share      numeric,
    observed_mean_days   numeric,
    observed_sd_days     numeric,
    promised_mean_days   numeric,
    promised_sd_days     numeric,
    pct_late             numeric,
    ss_observed          numeric,
    ss_promised          numeric,
    naive_total_cover    numeric,
    model_total_target   numeric,
    cycle_observed       numeric,
    cycle_promised       numeric,
    rop_observed         numeric,
    supply_variance_share numeric,
    weeks_since_last_sale numeric,
    is_intermittent      boolean,
    is_stale             boolean,
    is_publishable       boolean,
    exclusion_reason     text,
    PRIMARY KEY (calc_date, category, seller_id)
);
"""

COLS = ["calc_date","category","seller_id","abc","xyz","service_level","demand_mean",
        "demand_sd","zero_week_share","observed_mean_days","observed_sd_days",
        "promised_mean_days","promised_sd_days","pct_late","ss_observed","ss_promised",
        "naive_total_cover","model_total_target",
        "cycle_observed","cycle_promised","rop_observed",
        "supply_variance_share","weeks_since_last_sale",
        "is_intermittent","is_stale","is_publishable","exclusion_reason"]


def load_params(conn, df, calc_date) -> int:
    out = df.copy()
    out["calc_date"] = calc_date
    rows = [tuple(None if r[c] != r[c] else r[c] for c in COLS)   # NaN -> None
            for _, r in out[COLS].iterrows()]
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in COLS
                        if c not in ("calc_date", "category", "seller_id"))
    with conn.cursor() as cur:
        cur.execute(DDL)
        execute_values(cur, f"""
            INSERT INTO mart.buffer_params ({",".join(COLS)}) VALUES %s
            ON CONFLICT (calc_date, category, seller_id) DO UPDATE SET {updates}
        """, rows, page_size=1000)
    conn.commit()
    log.info("loaded %d parameter rows for calc_date=%s", len(rows), calc_date)
    return len(rows)
