"""
In-pipeline data quality gates.

These run BETWEEN compute and load and FAIL the run. A quality document nobody
reads is not quality control; an assertion that stops the DAG is. Gating before
the load matters because publishing a wrong buffer and retracting it costs more
than failing: once a planner has acted on it, the damage is physical.
"""
import logging

log = logging.getLogger(__name__)


class DataQualityError(Exception):
    pass


def run_checks(df, conn, max_row_drop_pct: float = 10.0) -> list:
    failures, passed = [], []

    # Checks run on the population that will actually be acted on. Intermittent
    # and stale series carry a reason code instead of a number, so holding them
    # to the same assertions would fail the run on rows nobody will use.
    pub = df[df["is_publishable"]] if "is_publishable" in df else df
    if "is_publishable" in df:
        passed.append(f"publishable series: {len(pub)} of {len(df)}")

    neg = int((pub["ss_observed"] < 0).sum())
    (passed if neg == 0 else failures).append(f"negative safety stock: {neg} rows")

    bad = int(((pub["observed_mean_days"] <= 0) | (pub["observed_mean_days"] > 365)).sum())
    (passed if bad == 0 else failures).append(f"fulfilment duration out of range: {bad} rows")

    null_share = float(pub["ss_observed"].isna().mean() * 100) if len(pub) else 0.0
    (passed if null_share < 5 else failures).append(f"null parameters: {null_share:.2f}%")

    # Volume anomaly check. Compares against the MOST RECENT SNAPSHOT, not the
    # whole table. An earlier version compared against the full table, so its
    # baseline grew with every new calc_date and the check could never fire.
    # A volume gate that moves its own baseline passes forever and catches
    # nothing.
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*) FROM information_schema.tables
                       WHERE table_schema='mart' AND table_name='buffer_params'""")
        prev = 0
        if cur.fetchone()[0] > 0:
            cur.execute("""SELECT count(*) FROM mart.buffer_params
                           WHERE calc_date = (SELECT max(calc_date) FROM mart.buffer_params)""")
            prev = cur.fetchone()[0]
    if prev:
        drop = (prev - len(df)) / prev * 100
        msg = f"row count vs latest snapshot: {prev} -> {len(df)} ({-drop:+.1f}%)"
        (passed if drop <= max_row_drop_pct else failures).append(msg)
    else:
        passed.append("row count: no previous snapshot to compare")

    for p in passed:
        log.info("  PASS  %s", p)
    for f in failures:
        log.error("  FAIL  %s", f)
    if failures:
        raise DataQualityError(f"{len(failures)} quality check(s) failed: {failures}")
    return passed
