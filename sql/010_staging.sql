-- STAGING LAYER
-- Types applied here. Every exclusion is a named, counted decision written to
-- staging.exclusions, so any downstream number can be reconstructed later.
-- Nothing is dropped silently.

DROP TABLE IF EXISTS staging.exclusions CASCADE;
CREATE TABLE staging.exclusions (
    step         text,
    reason       text,
    rows_removed bigint,
    recorded_at  timestamp DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- staging.orders
--
-- DECISION 1: completed orders only (status = mapping.yaml completed_status).
--   An undelivered order has no observable fulfilment duration. Including them
--   would bias reliability UPWARD, because the worst performers are precisely
--   the ones that never arrived. The exclusion is therefore conservative in
--   the wrong direction and must be visible.
--
-- DECISION 2: drop rows with status 'delivered' but a null delivery timestamp.
--   Internally contradictory.
--
-- DECISION 3: drop rows where delivery precedes dispatch.
--   A negative duration corrupts a series' entire standard deviation, because
--   squaring amplifies it.
--
-- DECISION 4: trim to 2017-01-01 onward.
--   Sep-Dec 2016 holds 329 orders and November 2016 is absent entirely. That
--   is a platform launching, not demand behaving.
--
-- NOTE ON NAMING: these are FULFILMENT durations (order placed -> customer
-- received), not replenishment lead times (PO placed -> goods received). The
-- columns are named fulfil_* rather than lead_time_* so the proxy cannot be
-- forgotten further downstream. See docs/defensibility.md, Step 3.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS staging.orders CASCADE;
CREATE TABLE staging.orders AS
WITH cfg AS (
    -- Which status value means the order actually completed. Written from
    -- mapping.yaml by the ingest step, so no SQL changes are needed to run
    -- this on an extract whose completed status is CLOSED or GR_COMPLETE.
    SELECT value AS completed FROM raw.pipeline_config WHERE key='completed_status'
),
typed AS (
    SELECT order_id, status,
           ordered_at::timestamp                      AS purchased_at,
           NULLIF(dispatched_at,'')::timestamp        AS shipped_at,
           NULLIF(received_at,'')::timestamp          AS delivered_at,
           NULLIF(promised_at,'')::timestamp          AS promised_at
    FROM raw.orders
)
SELECT order_id, purchased_at, delivered_at, promised_at,
       purchased_at::date                      AS purchase_date,
       date_trunc('week', purchased_at)::date  AS purchase_week,
       (delivered_at::date - purchased_at::date) AS observed_fulfil_days,
       (promised_at::date  - purchased_at::date) AS promised_fulfil_days
FROM typed
WHERE status = (SELECT completed FROM cfg)
  AND delivered_at IS NOT NULL
  AND (shipped_at IS NULL OR delivered_at >= shipped_at)
  AND delivered_at > purchased_at
  AND purchased_at >= DATE '2017-01-01';

INSERT INTO staging.exclusions (step, reason, rows_removed)
WITH cfg AS (SELECT value AS completed FROM raw.pipeline_config WHERE key='completed_status')
SELECT 'staging.orders', 'status is not the completed status',
       (SELECT count(*) FROM raw.orders, cfg WHERE status <> cfg.completed)
UNION ALL SELECT 'staging.orders', 'status=delivered but delivery timestamp null',
       (SELECT count(*) FROM raw.orders, cfg WHERE status=cfg.completed
          AND COALESCE(received_at,'')='')
UNION ALL SELECT 'staging.orders', 'delivered before dispatched (timestamp violation)',
       (SELECT count(*) FROM raw.orders
         WHERE received_at <> '' AND dispatched_at <> ''
           AND received_at::timestamp < dispatched_at::timestamp)
UNION ALL SELECT 'staging.orders', 'purchased before 2017-01-01 (platform launch artefact)',
       (SELECT count(*) FROM raw.orders, cfg WHERE status=cfg.completed
          AND ordered_at::timestamp < DATE '2017-01-01');

CREATE INDEX idx_stg_orders_week ON staging.orders (purchase_week);
CREATE INDEX idx_stg_orders_id   ON staging.orders (order_id);

-- 610 products carry no category. Bucket rather than drop: dropping would
-- silently remove their demand from every total.
-- The misspelled source columns are renamed here, the correct layer for it.
DROP TABLE IF EXISTS staging.products CASCADE;
CREATE TABLE staging.products AS
SELECT p.product_id,
       COALESCE(NULLIF(p.category,''), 'unknown')        AS category_src,
       COALESCE(NULLIF(t.label,''),
                NULLIF(p.category,''), 'unknown')        AS category
FROM raw.products p
LEFT JOIN raw.category_labels t ON t.category = p.category;
CREATE INDEX idx_stg_products_id ON staging.products (product_id);

-- Grain here is one row per ITEM LINE, not per order. An order containing
-- 3 units appears as 3 rows, and that is the demand signal we want.
DROP TABLE IF EXISTS staging.order_items CASCADE;
CREATE TABLE staging.order_items AS
-- quantity is optional in mapping.yaml. When a source does not carry one,
-- each line counts as a single unit, which is correct for sources that write
-- one row per unit and wrong for any source that does not. The fallback is
-- explicit here rather than hidden in an aggregate.
SELECT order_id, line_no AS item_seq, product_id,
       supplier_id AS seller_id,
       NULLIF(unit_value,'')::numeric            AS price,
       COALESCE(NULLIF(quantity,'')::numeric, 1) AS qty
FROM raw.order_lines;
CREATE INDEX idx_stg_items_order ON staging.order_items (order_id);

DROP TABLE IF EXISTS staging.sellers CASCADE;
CREATE TABLE staging.sellers AS
SELECT supplier_id AS seller_id, city, region AS state FROM raw.suppliers;
