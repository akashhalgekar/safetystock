"""
Load any company's extract into the raw layer, driven by mapping.yaml.

COPY rather than row-by-row INSERT. COPY streams the file into the table in one
statement; INSERT pays per-row parse, planning and WAL overhead. On 112k rows
the gap is roughly two orders of magnitude, and at nightly scale it is the
difference between a job finishing and not.

Column NAMES are mapped to canonical ones on the way in, which is what lets the
same pipeline run on an SAP extract, a Shopify export or a marketplace dump.
Column VALUES are untouched: nothing is cleaned, coerced or dropped here, so
raw stays a faithful record of what arrived. Cleaning is staging's job.

Idempotent by TRUNCATE-then-load: a rerun reproduces the raw layer rather than
doubling it.
"""
import io
import logging
import time
from pathlib import Path

import pandas as pd
import yaml

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
MAPPING = ROOT / "mapping.yaml"

# mapping section -> (raw table, canonical columns in table order, required?)
SECTIONS = [
    ("orders",          "raw.orders",
     ["order_id", "status", "ordered_at", "received_at", "promised_at", "dispatched_at"], True),
    ("order_lines",     "raw.order_lines",
     ["order_id", "line_no", "product_id", "supplier_id", "unit_value", "quantity"], True),
    ("products",        "raw.products",
     ["product_id", "category"], True),
    ("suppliers",       "raw.suppliers",
     ["supplier_id", "city", "region"], False),
    ("category_labels", "raw.category_labels",
     ["category", "label"], False),
]


def load_raw(conn, raw_dir: Path = RAW_DIR, mapping_path: Path = MAPPING,
             verify: bool = True) -> dict:
    m = yaml.safe_load(Path(mapping_path).read_text())
    raw_dir = Path(raw_dir)
    counts = {}

    with conn.cursor() as cur:
        # Settings the SQL layer needs. Written from mapping.yaml so a company
        # whose "completed" status is GR_COMPLETE or CLOSED does not have to
        # edit any SQL.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS raw.pipeline_config (key text PRIMARY KEY, value text);
            TRUNCATE raw.pipeline_config;
        """)
        cur.execute("INSERT INTO raw.pipeline_config VALUES ('completed_status', %s)",
                    (m["orders"].get("completed_status", "delivered"),))
        # Series minimums live in config.yaml but are applied in SQL, so they
        # travel through the same table. Previously they were hardcoded in the
        # SQL while config.yaml advertised a knob that did nothing.
        import yaml as _yaml
        cfg = _yaml.safe_load((ROOT / "config.yaml").read_text())
        for key in ("min_weeks_history", "min_units_history"):
            cur.execute("INSERT INTO raw.pipeline_config VALUES (%s, %s)",
                        (key, str(cfg.get(key, 8 if key == "min_weeks_history" else 20))))

        for section, table, canonical, required in SECTIONS:
            spec = m.get(section)
            if not spec:
                if required:
                    raise ValueError(f"mapping.yaml is missing the required '{section}' section")
                log.info("skipped %-20s (not in mapping.yaml)", table)
                continue

            path = raw_dir / spec["file"]
            if not path.exists():
                if required:
                    raise FileNotFoundError(f"Source file missing: {path}")
                log.info("skipped %-20s (%s not present)", table, spec["file"])
                continue

            started = time.perf_counter()
            # utf-8-sig strips a BOM; dtype=str keeps every value exactly as written
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", low_memory=False)
            cols = spec["columns"]

            missing = [c for c in canonical
                       if c in cols and cols[c] not in df.columns]
            if missing:
                raise ValueError(
                    f"{section}: mapped column(s) not found in {spec['file']}: "
                    + ", ".join(f"{c} -> '{cols[c]}'" for c in missing)
                    + ". Run scripts/check_input.py for a full report."
                )

            out = pd.DataFrame({
                c: (df[cols[c]] if c in cols and cols[c] in df.columns else None)
                for c in canonical
            })

            buf = io.StringIO()
            out.to_csv(buf, index=False, header=False)
            buf.seek(0)
            cur.execute(f"TRUNCATE {table};")
            cur.copy_expert(f"COPY {table} FROM STDIN WITH (FORMAT csv)", buf)

            cur.execute(f"SELECT count(*) FROM {table};")
            n = cur.fetchone()[0]
            counts[table] = n
            log.info("loaded %-20s %8d rows in %5.2fs  <- %s",
                     table, n, time.perf_counter() - started, spec["file"])

            if n == 0 and required:
                raise ValueError(f"{table} loaded 0 rows from {spec['file']}")

            # Optional integrity check: if the mapping declares how many rows
            # the extract should contain, a truncated or swapped file fails
            # loudly here rather than quietly producing wrong parameters.
            expected = spec.get("expected_rows")
            if verify and expected and n != int(expected):
                raise ValueError(
                    f"{table} loaded {n} rows, mapping.yaml expects {expected}. "
                    "Source may be truncated or from a different extract."
                )

    conn.commit()
    return counts
