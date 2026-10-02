-- MART LAYER
--
-- GRAIN: one row = one category, one seller, one week.
-- Forced by the data, not chosen for convenience. The median product sold once
-- in 21 months and 18,117 of 32,951 products sold exactly once, so per-product
-- variability is not computable. Category-and-seller mirrors how planners
-- actually manage long-tail items: by family.
-- What is lost: within-category mix. Documented as a known limitation.

-- ---------------------------------------------------------------------------
-- dim_seller, Slowly Changing Dimension Type 2.
-- A seller's profile changes over time. Overwriting it would make last
-- quarter's parameters unreproducible, which is the most common reason a
-- planner stops trusting an inventory system. Versioned by half-year here.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS mart.dim_seller CASCADE;
CREATE TABLE mart.dim_seller (
    seller_key          bigserial PRIMARY KEY,
    seller_id           text NOT NULL,
    state               text,
    city                text,
    avg_fulfil_days     numeric,
    valid_from          date NOT NULL,
    valid_to            date,           -- NULL = still current
    is_current          boolean NOT NULL
);

INSERT INTO mart.dim_seller (seller_id, state, city, avg_fulfil_days, valid_from, valid_to, is_current)
WITH periods AS (
    SELECT oi.seller_id,
           CASE WHEN o.purchase_date < DATE '2018-01-01'
                THEN DATE '2017-01-01' ELSE DATE '2018-01-01' END AS valid_from,
           avg(o.observed_fulfil_days)::numeric(10,2)             AS avg_fulfil_days
    FROM staging.orders o
    JOIN staging.order_items oi ON oi.order_id = o.order_id
    GROUP BY 1,2
)
SELECT p.seller_id, s.state, s.city, p.avg_fulfil_days, p.valid_from,
       LEAD(p.valid_from) OVER (PARTITION BY p.seller_id ORDER BY p.valid_from) - 1,
       LEAD(p.valid_from) OVER (PARTITION BY p.seller_id ORDER BY p.valid_from) IS NULL
FROM periods p
LEFT JOIN staging.sellers s ON s.seller_id = p.seller_id;
CREATE INDEX idx_dim_seller_lookup ON mart.dim_seller (seller_id, is_current);

-- ---------------------------------------------------------------------------
-- fct_demand_weekly
-- Weeks with no sales are NOT missing rows. A zero-demand week is a real
-- observation; dropping it raises the apparent mean, lowers the apparent
-- standard deviation, and under-buffers exactly the erratic series that need
-- the buffer most. So we generate the full calendar spine per series and
-- left-join demand onto it.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS mart.fct_demand_weekly CASCADE;
CREATE TABLE mart.fct_demand_weekly AS
WITH sales AS (
    SELECT p.category, oi.seller_id, o.purchase_week,
           sum(oi.qty)::numeric AS units,
           sum(oi.price * oi.qty)::numeric AS revenue
    FROM staging.orders o
    JOIN staging.order_items oi ON oi.order_id = o.order_id
    JOIN staging.products    p  ON p.product_id = oi.product_id
    GROUP BY 1,2,3
),
series AS (
    SELECT category, seller_id, min(purchase_week) AS first_week,
           max(purchase_week) AS last_week
    FROM sales GROUP BY 1,2
    -- Minimums come from config.yaml via raw.pipeline_config, so changing
    -- them in config actually changes behaviour.
    HAVING count(*) >= (SELECT value::int FROM raw.pipeline_config WHERE key='min_weeks_history')
       AND sum(units) >= (SELECT value::numeric FROM raw.pipeline_config WHERE key='min_units_history')
),
spine AS (
    SELECT s.category, s.seller_id, gs::date AS purchase_week
    FROM series s,
         LATERAL generate_series(s.first_week, s.last_week, INTERVAL '1 week') gs
)
SELECT sp.category, sp.seller_id, sp.purchase_week,
       COALESCE(sa.units,0) AS units, COALESCE(sa.revenue,0) AS revenue
FROM spine sp
LEFT JOIN sales sa ON sa.category=sp.category AND sa.seller_id=sp.seller_id
                  AND sa.purchase_week=sp.purchase_week;
CREATE INDEX idx_fct_demand_series ON mart.fct_demand_weekly (category, seller_id, purchase_week);

-- ---------------------------------------------------------------------------
-- mart.fulfilment_stats: promised vs observed, per series.
-- promised_* comes from the estimated delivery date, a policy value.
-- observed_* is computed from transactions.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS mart.fulfilment_stats CASCADE;
CREATE TABLE mart.fulfilment_stats AS
SELECT p.category, oi.seller_id, count(*) AS n_deliveries,
  avg(o.observed_fulfil_days)::numeric(10,3)                        AS observed_mean_days,
  COALESCE(stddev_samp(o.observed_fulfil_days),0)::numeric(10,3)    AS observed_sd_days,
  avg(o.promised_fulfil_days)::numeric(10,3)                        AS promised_mean_days,
  COALESCE(stddev_samp(o.promised_fulfil_days),0)::numeric(10,3)    AS promised_sd_days,
  avg(CASE WHEN o.observed_fulfil_days > o.promised_fulfil_days THEN 1.0 ELSE 0.0 END)::numeric(6,4) AS pct_late
FROM staging.orders o
JOIN staging.order_items oi ON oi.order_id = o.order_id
JOIN staging.products    p  ON p.product_id = oi.product_id
GROUP BY 1,2
HAVING count(*) >= 10;
CREATE INDEX idx_fulfil_stats ON mart.fulfilment_stats (category, seller_id);
