# Run this on your own data

This pipeline is not tied to the demo dataset. It needs **five facts**. If your
system can export those, you can point it at your extract without renaming a
single column or editing any SQL.

---

## The five facts

| Fact | Why it is needed | Typical source column |
|---|---|---|
| When each order was **placed** | starts the duration clock | PO release date, order date |
| When it was **received** | stops the clock → observed duration | goods receipt date, delivery date |
| When it was **promised** | the quoted duration, for the comparison | confirmed delivery date, requested date |
| What was ordered, from **whom** | the demand series and the supplier | material + vendor on the PO line |
| **How many units** on the line | demand volume and variability | order quantity, PO qty |
| What each line was **worth** | ABC ranking | net price, line value |

That is a purchase order history. Most ERP systems export it in one or two
tables. You do **not** need inventory snapshots, forecasts, or master-data lead
times. The point of the thing is that it does not trust master-data lead times.

## Step 1 — describe your columns in `mapping.yaml`

Change the values on the right to whatever your columns are called. Leave the
keys on the left alone.

```yaml
orders:
  file: ZMM_PO_HEADER.csv
  columns:
    order_id:      PO_NUMBER
    status:        PO_STATUS
    ordered_at:    PO_RELEASE_DT       # clock starts
    received_at:   GR_POSTING_DT       # clock stops
    promised_at:   CONFIRMED_DLV_DT    # the quoted date
    dispatched_at: GI_DT               # optional, sanity check only
  completed_status: GR_COMPLETE        # YOUR value for "it actually arrived"

order_lines:
  file: ZMM_PO_ITEM.csv
  columns:
    order_id:    PO_NUMBER
    line_no:     PO_ITEM
    product_id:  MATNR
    supplier_id: LIFNR
    unit_value:  NET_PRICE
    quantity:    MENGE                 # map this if your lines carry a qty

products:
  file: ZMM_MATERIAL.csv
  columns:
    product_id: MATNR
    category:   MATKL                  # your grouping level

# suppliers: and category_labels: are optional. Omit them entirely.
```

That example is real: the repository is tested against an SAP-style extract
with exactly these names, and it produces the same parameters as the demo data
it was derived from.

> **Map `quantity` if your lines have one.** It is optional only because the
> demo dataset writes one row per unit. If your purchase order lines carry a
> quantity and you leave it unmapped, every line counts as a single unit and
> your demand, variability and buffers are all understated. A verification run
> with quantity set to 3 on every line produces triple the demand, as expected.

Optionally add `expected_rows: 99441` under a file to make a truncated or
swapped export fail loudly rather than quietly producing wrong numbers.

## Step 2 — check before you load

```bash
python scripts/check_input.py --dir /path/to/your/csvs
```

It reports missing columns, dates that will not parse, how many rows survive
each filter, how much history you have, and whether any grain has enough
observations to compute variability at all. It exits non-zero if the data
cannot support the model, so it can sit in front of a scheduled run.

A clean report looks like this:

```
  OK   orders: 99,441 rows, all required columns present
  OK   completed_status 'GR_COMPLETE': 96,478 rows (97%)
  OK   usable for duration stats: 96,470 rows
  OK   history spans 102 weeks (2016-09-15 to 2018-08-29)
       per product: median 1 line(s), 18,117 of 32,951 sold exactly once
       per category+supplier: 1,566 series with 8+ active weeks
  OK   category+supplier grain is viable (1,566 series) — the default
PASSED
```

A failure names the exact column it could not find and lists what your file
actually contains.

## Step 3 — set the business parameters

`config.yaml` holds the judgement calls. Nothing here is a technical setting.

| Setting | What it decides | Change it when |
|---|---|---|
| `service_levels` | A/B/C stockout tolerance (98/95/90%) | your stockout cost differs from your holding cost in a known way |
| `demand_window_weeks` | how much history drives variability (26) | your demand shifted recently, or you have under a year of data |
| `min_weeks_history` / `min_units_history` | the floor below which a series is not modelled (8 weeks, 20 units) | your catalogue moves faster or slower than weekly |
| `activity_window_weeks` | how recently a series must have sold to publish (13) | your reorder cycle is longer or shorter |
| `intermittency_threshold` | zero-week share above which the normal model is refused (40%) | rarely |
| `baseline_weeks_of_cover` | the current flat rule you are comparing against | set it to **your** current policy |

Set `baseline_weeks_of_cover` to whatever your planners actually do today. That
is what turns the output from an academic number into a business case.

## Step 4 — run it

```bash
docker compose up -d
pip install -r requirements.txt
cp .env.example .env            # set PGPASSWORD
make ddl ingest profile marts run findings
make dashboard
```

Read the `profile` output before anything else. It tells you what is wrong with
your data, and every exclusion downstream is a response to something it found.

For a scheduled nightly refresh:

```bash
make airflow                    # http://localhost:8080, admin / admin
```

## What you get

`mart.buffer_params`, one row per series per `calc_date`:

| Column | Meaning |
|---|---|
| `ss_observed`, `rop_observed` | safety stock and reorder point on **observed** durations |
| `ss_promised` | the same computed on **promised** durations, for the comparison |
| `model_total_target` | cycle plus safety: the like-for-like counterpart to a cover rule |
| `naive_total_cover` | what your current flat rule would hold |
| `supply_variance_share` | how much of the buffer exists because supply is unreliable rather than demand |
| `abc`, `xyz` | value and variability segmentation |
| `weeks_since_last_sale` | freshness, so nobody acts on a dead series |
| `is_publishable`, `exclusion_reason` | why a series was held back, if it was |

Plus `sql/030_findings.sql` for the summary, and `dashboard/index.html` for a
review page.

## Read these before acting on the numbers

- **Set `baseline_weeks_of_cover` to your real policy.** The gap against it is
  the whole business case, and against a made-up baseline it means nothing.
- **A flat cover rule is a TOTAL on-hand policy.** Compare it against
  `model_total_target`, not against `ss_observed` alone. Getting this wrong
  understates the gap badly; it is documented in `docs/defensibility.md`.
- **Check `supply_variance_share` before investing in forecasting.** Where it is
  high, better forecasting cannot help; the lever is supplier development, dual
  sourcing or a contractual lead-time commitment.
- **Intermittent series are refused, not estimated.** If most of your catalogue
  comes back intermittent, that is a real finding about your demand, not a bug.
  Croston's method is the correct treatment and is listed as future work.
- **Service levels are defaults, not derived.** 98/95/90 are conventional. A
  real deployment sets them from stockout cost against holding cost per class.
- **Buffers are sensitive to single-week spikes.** The findings report how much
  of each series' standard deviation comes from its largest week. If that figure
  is high for you, add outlier handling before the output drives purchase orders.

## What it will not do

It recommends parameters; it does not write them back to your ERP, and it does
not validate them against your actual stockouts. Backtesting the policy needs
on-hand inventory history, which this pipeline does not ingest. Treat the output
as a reviewed proposal, not an automatic update.
