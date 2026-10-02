\echo '=== F0: population — what is published and what is held back ==='
SELECT coalesce(exclusion_reason,'PUBLISHED') AS status, count(*),
       round(100.0*count(*)/sum(count(*)) OVER (),1) AS pct
FROM mart.buffer_params GROUP BY 1 ORDER BY 2 DESC;

\echo '=== F1: promised vs observed fulfilment duration (published only) ==='
SELECT round(avg(promised_mean_days),1) AS promised_mean_d,
       round(avg(observed_mean_days),1) AS observed_mean_d,
       round(avg(promised_sd_days),2)   AS promised_sd_d,
       round(avg(observed_sd_days),2)   AS observed_sd_d,
       round(avg(promised_mean_days/NULLIF(observed_mean_days,0)),2) AS pad_ratio,
       count(*) FILTER (WHERE promised_mean_days > observed_mean_days) AS series_padded,
       count(*) AS series
FROM mart.buffer_params WHERE is_publishable;

\echo '=== F2: inventory position implied by each ==='
SELECT round(sum(cycle_promised)) AS cycle_promised, round(sum(cycle_observed)) AS cycle_observed,
       round(sum(ss_promised))    AS ss_promised,    round(sum(ss_observed))    AS ss_observed,
       round(sum(cycle_promised+ss_promised)) AS total_promised,
       round(sum(cycle_observed+ss_observed)) AS total_observed,
       round(100.0*(sum(cycle_promised+ss_promised)/sum(cycle_observed+ss_observed)-1),1) AS pct_overstated
FROM mart.buffer_params WHERE is_publishable;

\echo '=== F3: what drives the buffer requirement? ==='
SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY supply_variance_share)*100)::numeric,1) AS median_pct_supply,
       round((min(supply_variance_share)*100)::numeric,1) AS min_pct,
       round((max(supply_variance_share)*100)::numeric,1) AS max_pct,
       count(*) FILTER (WHERE supply_variance_share > 0.5) AS majority_supply_driven,
       count(*) AS series
FROM mart.buffer_params WHERE is_publishable;

\echo '=== F4: flat cover rule vs model — LIKE FOR LIKE (total vs total) ==='
\echo '    A "N weeks of cover" rule is a TOTAL on-hand policy, so it is compared'
\echo '    against the model total target (cycle + safety), not safety alone.'
SELECT abc, count(*) AS series,
       round(sum(naive_total_cover))  AS naive_total,
       round(sum(model_total_target)) AS model_total,
       round(100.0*(sum(naive_total_cover)/sum(model_total_target)-1),1) AS gap_pct,
       count(*) FILTER (WHERE naive_total_cover < model_total_target) AS under,
       count(*) FILTER (WHERE naive_total_cover > model_total_target) AS over
FROM mart.buffer_params WHERE is_publishable GROUP BY 1 ORDER BY 1;

\echo '=== F5: ABC x XYZ grid ==='
SELECT abc, count(*) FILTER (WHERE xyz='X') AS x, count(*) FILTER (WHERE xyz='Y') AS y,
       count(*) FILTER (WHERE xyz='Z') AS z, count(*) AS total
FROM mart.buffer_params WHERE is_publishable GROUP BY 1 ORDER BY 1;

\echo '=== F6: freshness of the published population ==='
SELECT round(max(weeks_since_last_sale),1) AS oldest_weeks_since_sale,
       round(avg(weeks_since_last_sale),1) AS avg_weeks_since_sale
FROM mart.buffer_params WHERE is_publishable;

\echo '=== F7: least consistent sellers, A/B classes ==='
SELECT left(seller_id,8) AS seller, category, abc,
       observed_mean_days AS mean_d, observed_sd_days AS sd_d,
       round(pct_late*100,0) AS pct_late, round(model_total_target,0) AS target_units
FROM mart.buffer_params
WHERE is_publishable AND abc IN ('A','B')
ORDER BY observed_sd_days DESC LIMIT 8;

\echo '=== F8: OUTLIER SENSITIVITY — how much of sigma_D is one week? ==='
WITH r AS (SELECT category, seller_id, units,
             ROW_NUMBER() OVER (PARTITION BY category, seller_id ORDER BY units DESC) rn
           FROM mart.fct_demand_weekly),
s AS (SELECT category, seller_id, stddev_samp(units) sd_all,
             stddev_samp(units) FILTER (WHERE rn>1) sd_ex
      FROM r GROUP BY 1,2)
SELECT round(avg(100.0*(1 - sd_ex/NULLIF(sd_all,0)))::numeric,1) AS pct_of_sigma_from_single_week
FROM s JOIN mart.buffer_params p USING (category,seller_id) WHERE p.is_publishable;
