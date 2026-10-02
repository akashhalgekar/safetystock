-- PROFILING
-- Six universal checks plus two derived from the formula's own assumptions.
-- Run against raw BEFORE any cleaning, so every exclusion downstream is a
-- response to something observed rather than something assumed.

\echo '=== 1. Order status distribution ==='
SELECT order_status, count(*) AS n,
       round(100.0*count(*)/sum(count(*)) OVER (), 2) AS pct
FROM raw.orders GROUP BY 1 ORDER BY 2 DESC;

\echo '=== 2. Null timestamps ==='
SELECT count(*) AS total_orders,
  count(*) FILTER (WHERE COALESCE(order_delivered_customer_date,'')='') AS null_delivered,
  count(*) FILTER (WHERE COALESCE(order_approved_at,'')='')             AS null_approved,
  count(*) FILTER (WHERE COALESCE(order_delivered_carrier_date,'')='')  AS null_carrier
FROM raw.orders;

\echo '=== 3. Contradiction: delivered status, no delivery timestamp ==='
SELECT count(*) FROM raw.orders
WHERE order_status='delivered' AND COALESCE(order_delivered_customer_date,'')='';

\echo '=== 4. Timestamp ordering violations (delivered before dispatched) ==='
SELECT count(*) FROM raw.orders
WHERE order_delivered_customer_date <> '' AND order_delivered_carrier_date <> ''
  AND order_delivered_customer_date::timestamp < order_delivered_carrier_date::timestamp;

\echo '=== 5. Non-positive lead times ==='
SELECT count(*) FROM raw.orders
WHERE order_delivered_customer_date <> ''
  AND order_delivered_customer_date::timestamp <= order_purchase_timestamp::timestamp;

\echo '=== 6. Monthly volume (is the period representative?) ==='
SELECT to_char(order_purchase_timestamp::timestamp,'YYYY-MM') AS ym, count(*) AS orders
FROM raw.orders GROUP BY 1 ORDER BY 1 LIMIT 8;

\echo '=== 7. Products with no category ==='
SELECT count(*) FROM raw.products WHERE COALESCE(product_category_name,'')='';

\echo '=== 8. Is per-product demand thick enough for a standard deviation? ==='
WITH per_product AS (SELECT product_id, count(*) AS lines FROM raw.order_items GROUP BY 1)
SELECT count(*) AS products_sold, round(avg(lines),2) AS avg_lines,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY lines) AS median_lines,
  count(*) FILTER (WHERE lines = 1)   AS sold_exactly_once,
  count(*) FILTER (WHERE lines >= 20) AS sold_20_or_more
FROM per_product;
