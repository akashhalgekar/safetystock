#!/usr/bin/env python3
"""
Validate your own data against what this pipeline needs, BEFORE loading it.

    python scripts/check_input.py                 # uses mapping.yaml + data/raw
    python scripts/check_input.py --dir /my/csvs

Reports, per file: missing required columns, unparseable dates, how many rows
survive each filter, and whether enough history remains to compute a standard
deviation. Exits non-zero if the data cannot support the model, so it can sit
in CI or in front of a scheduled run.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
OK, WARN, BAD = "  OK  ", " WARN ", " FAIL "

REQUIRED = {
    "orders":      ["order_id", "status", "ordered_at", "received_at", "promised_at"],
    "order_lines": ["order_id", "line_no", "product_id", "supplier_id", "unit_value"],
    "products":    ["product_id", "category"],
}


def main(data_dir: Path, mapping_path: Path) -> int:
    m = yaml.safe_load(mapping_path.read_text())
    problems, warnings = [], []
    frames = {}

    print(f"\nChecking {data_dir}\n" + "=" * 68)

    for section, req in REQUIRED.items():
        spec = m.get(section)
        if not spec:
            problems.append(f"mapping.yaml has no '{section}' section")
            print(f"{BAD} {section}: missing from mapping.yaml")
            continue

        path = data_dir / spec["file"]
        if not path.exists():
            problems.append(f"{section}: file not found at {path}")
            print(f"{BAD} {section}: {spec['file']} not found")
            continue

        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", low_memory=False)
        cols = spec["columns"]

        missing = [k for k in req if cols.get(k) not in df.columns]
        if missing:
            for k in missing:
                problems.append(f"{section}.{k} -> '{cols.get(k)}' not in {spec['file']}")
            print(f"{BAD} {section}: {len(missing)} required column(s) not found")
            print(f"       looked for: {[cols.get(k) for k in missing]}")
            print(f"       file has:   {list(df.columns)[:8]}{' ...' if len(df.columns) > 8 else ''}")
            continue

        renamed = df.rename(columns={v: k for k, v in cols.items() if v in df.columns})
        frames[section] = renamed
        print(f"{OK} {section}: {len(df):,} rows, all required columns present")

    if problems:
        print("\n" + "=" * 68)
        print("Cannot proceed. Fix these in mapping.yaml or your extract:\n")
        for p in problems:
            print("  -", p)
        return 1

    # ---- deeper checks, now that the columns resolve -------------------
    o = frames["orders"]
    print("\nOrders detail")
    print("-" * 68)

    for c in ("ordered_at", "received_at", "promised_at"):
        parsed = pd.to_datetime(o[c], errors="coerce")
        bad = int(parsed.isna().sum() - o[c].isna().sum())
        if bad:
            warnings.append(f"{c}: {bad:,} values will not parse as dates")
            print(f"{WARN} {c}: {bad:,} unparseable ({bad/len(o):.1%})")
        else:
            print(f"{OK} {c}: parses cleanly")
        o[c] = parsed

    completed = m["orders"].get("completed_status")
    if completed not in set(o["status"].dropna().unique()):
        vals = sorted(o["status"].dropna().unique())[:8]
        problems.append(f"completed_status '{completed}' never appears in the status column")
        print(f"{BAD} completed_status '{completed}' not found. Values present: {vals}")
    else:
        n_done = int((o["status"] == completed).sum())
        print(f"{OK} completed_status '{completed}': {n_done:,} rows ({n_done/len(o):.0%})")

    usable = o[(o["status"] == completed) & o["received_at"].notna() &
               (o["received_at"] > o["ordered_at"])]
    print(f"{OK} usable for duration stats: {len(usable):,} rows")
    if len(usable) < 500:
        problems.append(f"only {len(usable)} usable orders; too few to model")
        print(f"{BAD} fewer than 500 usable orders")

    if len(usable):
        span_weeks = (usable["ordered_at"].max() - usable["ordered_at"].min()).days / 7
        print(f"{OK} history spans {span_weeks:.0f} weeks "
              f"({usable['ordered_at'].min().date()} to {usable['ordered_at'].max().date()})")
        if span_weeks < 26:
            warnings.append(f"only {span_weeks:.0f} weeks of history; lower demand_window_weeks in config.yaml")
            print(f"{WARN} under 26 weeks: reduce demand_window_weeks in config.yaml")

    # ---- can we actually compute variability at some grain? ------------
    print("\nGrain check")
    print("-" * 68)
    lines = frames["order_lines"].merge(frames["products"], on="product_id", how="left")
    per_product = lines.groupby("product_id").size()
    print(f"       per product: median {per_product.median():.0f} line(s), "
          f"{(per_product == 1).sum():,} of {len(per_product):,} sold exactly once")

    joined = lines.merge(o[["order_id", "ordered_at"]], on="order_id", how="inner")
    joined["week"] = pd.to_datetime(joined["ordered_at"]).dt.to_period("W")
    per_cat = joined.groupby(["category", "supplier_id"])["week"].nunique()
    viable = int((per_cat >= 8).sum())
    print(f"       per category+supplier: {viable:,} series with 8+ active weeks")

    if per_product.median() >= 8:
        print(f"{OK} per-product grain is viable; you may set grain to product level")
    elif viable >= 30:
        print(f"{OK} category+supplier grain is viable ({viable:,} series) — the default")
    else:
        problems.append("no grain has enough observations to compute variability")
        print(f"{BAD} too sparse at every grain; aggregate further or extend history")

    # ---- verdict --------------------------------------------------------
    print("\n" + "=" * 68)
    if problems:
        print("FAILED\n")
        for p in problems:
            print("  -", p)
        return 1
    print("PASSED — this data can drive the pipeline.")
    if warnings:
        print("\nWorth knowing:")
        for w in warnings:
            print("  -", w)
    print("\nNext:  make ddl ingest profile marts run findings")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--mapping", default=str(ROOT / "mapping.yaml"))
    a = ap.parse_args()
    sys.exit(main(Path(a.dir), Path(a.mapping)))
