r"""
Inventory buffer parameter engine.

    SS = Z * sqrt( LT * sd_D^2  +  D_bar^2 * sd_LT^2 )
         \_____/   \_________/    \_______________/
         service   demand var      supply-side var
         level     over LT         contribution

PROXY NOTICE. On this dataset LT is OUTBOUND FULFILMENT duration (order placed
-> customer received), not INBOUND REPLENISHMENT lead time (PO placed -> goods
received), which is what the formula expects. The arithmetic is identical; the
business meaning is not. Columns are named observed_/promised_fulfil_* rather
than lead_time_* so the substitution stays visible. See docs/defensibility.md
Step 3.

Computed twice per series: once on OBSERVED fulfilment variability measured
from transactions, once on PROMISED variability from the estimated delivery
date. The gap between them is the finding.

Design note: every function below takes and returns DataFrames and never opens
a database connection. That is what lets the maths be unit-tested with no
Postgres running.
"""
import logging
import numpy as np
import pandas as pd
from scipy.stats import norm

log = logging.getLogger(__name__)


def z_from_service_level(sl: float) -> float:
    """Inverse normal CDF. 0.95 -> 1.645."""
    return float(norm.ppf(sl))


# ---------------------------------------------------------------- EXTRACT
def extract_demand(eng, window_weeks: int, calc_date) -> pd.DataFrame:
    """Trailing window ANCHORED TO calc_date.

    An earlier version took each series' own last N weeks, which meant a series
    that stopped selling in 2017 still contributed a "current" parameter stamped
    with today's calc_date. Parameters in one snapshot must describe the same
    period, so the window is anchored to the calculation date and series with no
    demand inside it are simply not modelled.
    """
    return pd.read_sql("""
        SELECT category, seller_id, purchase_week, units
        FROM mart.fct_demand_weekly
        WHERE purchase_week <= %(d)s
          AND purchase_week >  (%(d)s::date - (%(w)s || ' weeks')::interval)
    """, eng, params={"d": calc_date, "w": window_weeks})


def extract_activity(eng, calc_date) -> pd.DataFrame:
    """Last week each series actually sold anything, over its full history."""
    return pd.read_sql("""
        SELECT category, seller_id,
               max(purchase_week) FILTER (WHERE units > 0) AS last_sale_week
        FROM mart.fct_demand_weekly
        WHERE purchase_week <= %(d)s
        GROUP BY 1,2
    """, eng, params={"d": calc_date})


def extract_fulfilment(eng) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM mart.fulfilment_stats;", eng)


def extract_revenue(eng, window_weeks: int, calc_date) -> pd.DataFrame:
    """Revenue over the SAME anchored window as demand, annualised.

    An earlier version summed revenue over each series' full history and called
    the column annual_revenue. It was neither annual nor comparable: A-class
    series averaged 58 weeks of history against C-class 32, so ranking on the
    raw total partly ranked series by how long they had existed. Anchoring the
    window gives every series the same denominator; annualising makes the number
    mean what its name says.
    """
    df = pd.read_sql("""
        SELECT category, seller_id, sum(revenue) AS window_revenue
        FROM mart.fct_demand_weekly
        WHERE purchase_week <= %(d)s
          AND purchase_week >  (%(d)s::date - (%(w)s || ' weeks')::interval)
        GROUP BY 1,2
    """, eng, params={"d": calc_date, "w": window_weeks})
    df["annualised_revenue"] = df["window_revenue"] / window_weeks * 52.0
    return df


# -------------------------------------------------- TRANSFORM (no DB below)
def demand_stats(demand: pd.DataFrame) -> pd.DataFrame:
    """Per-series mean, sd and intermittency.

    Zero-demand weeks are included deliberately: excluding them raises the
    apparent mean and lowers the apparent sd, which under-buffers.
    """
    g = demand.groupby(["category", "seller_id"])["units"]
    out = g.agg(weeks="count", demand_mean="mean",
                demand_sd=lambda s: s.std(ddof=1)).reset_index()
    zero = (demand.assign(z=lambda d: d["units"] == 0)
            .groupby(["category", "seller_id"])["z"].mean()
            .rename("zero_week_share").reset_index())
    out = out.merge(zero, on=["category", "seller_id"])
    out["demand_sd"] = out["demand_sd"].fillna(0.0)
    out["cv"] = np.where(out["demand_mean"] > 0,
                         out["demand_sd"] / out["demand_mean"], np.nan)
    return out


def classify_abc(df: pd.DataFrame, a_thr: float, b_thr: float) -> pd.DataFrame:
    """Pareto by revenue.

    Classifies on the EXCLUSIVE cumulative share. Using the inclusive share
    pushes the series that crosses the 80% line down into B or C even though it
    is part of the top 80%; with a single series it classified the only item as
    C. Caught by test_formula_matches_hand_calculation.

    Ranks on ANNUALISED revenue over the anchored window, so series with
    different amounts of history are comparable.

    Note: revenue is a proxy for cost here because Olist has price but no cost.
    Acceptable for ranking; NOT acceptable for a holding-cost tradeoff, where
    holding cost is a share of cost and stockout cost is margin.
    """
    df = df.sort_values("annualised_revenue", ascending=False).copy()
    total = df["annualised_revenue"].sum()
    if not total:
        df["cum_share"] = 0.0
        df["abc"] = "C"
        return df
    cum_prev = df["annualised_revenue"].cumsum().shift(1).fillna(0.0) / total
    df["cum_share"] = df["annualised_revenue"].cumsum() / total
    df["abc"] = np.select([cum_prev < a_thr, cum_prev < b_thr], ["A", "B"], default="C")
    return df


def classify_xyz(df: pd.DataFrame, x_thr: float, y_thr: float) -> pd.DataFrame:
    df = df.copy()
    df["xyz"] = np.select([df["cv"] <= x_thr, df["cv"] <= y_thr], ["X", "Y"], default="Z")
    df.loc[df["cv"].isna(), "xyz"] = "Z"
    return df


def compute_buffers(stats: pd.DataFrame, fulfil: pd.DataFrame,
                    rev: pd.DataFrame, cfg: dict,
                    activity: pd.DataFrame = None, calc_date=None) -> pd.DataFrame:
    df = (stats.merge(fulfil, on=["category", "seller_id"], how="inner")
               .merge(rev, on=["category", "seller_id"], how="left"))

    # Freshness. A planner must be able to see how recently a series actually
    # sold before acting on its buffer: 12% of the earlier population had not
    # sold in six months, and one had not sold in eighteen.
    if activity is not None and calc_date is not None:
        df = df.merge(activity, on=["category", "seller_id"], how="left")
        cd = pd.Timestamp(calc_date)
        df["weeks_since_last_sale"] = (
            (cd - pd.to_datetime(df["last_sale_week"])).dt.days / 7.0).round(1)
        df["is_stale"] = df["weeks_since_last_sale"] > cfg["activity_window_weeks"]
    else:
        df["weeks_since_last_sale"] = np.nan
        df["is_stale"] = False

    df = classify_abc(df, cfg["abc_thresholds"]["A"], cfg["abc_thresholds"]["B"])
    df = classify_xyz(df, cfg["xyz_thresholds"]["X"], cfg["xyz_thresholds"]["Y"])
    df["service_level"] = df["abc"].map(cfg["service_levels"])
    df["z"] = df["service_level"].apply(z_from_service_level)

    # durations arrive in days, the demand series is weekly
    for src in ("observed", "promised"):
        df[f"{src}_weeks"]    = df[f"{src}_mean_days"].astype(float) / 7.0
        df[f"{src}_sd_weeks"] = df[f"{src}_sd_days"].astype(float) / 7.0

    d_bar = df["demand_mean"].astype(float)
    sd_d  = df["demand_sd"].astype(float)

    for src in ("observed", "promised"):
        lt_w, sd_lt_w = df[f"{src}_weeks"], df[f"{src}_sd_weeks"]
        demand_term = lt_w * sd_d ** 2
        supply_term = d_bar ** 2 * sd_lt_w ** 2
        sigma = np.sqrt(demand_term + supply_term)
        df[f"ss_{src}"]           = df["z"] * sigma
        df[f"cycle_{src}"]        = d_bar * lt_w
        df[f"rop_{src}"]          = d_bar * lt_w + df[f"ss_{src}"]
        df[f"{src}_demand_term"]  = demand_term
        df[f"{src}_supply_term"]  = supply_term

    denom = df["observed_demand_term"] + df["observed_supply_term"]
    df["supply_variance_share"] = np.where(denom > 0,
                                           df["observed_supply_term"] / denom, np.nan)

    # A planner saying "we hold two weeks of cover" means TOTAL on-hand, not a
    # safety buffer on top of cycle stock. An earlier version compared this
    # against ss_observed alone, which overstated how well the flat rule did on
    # C-class items (it looked within 0.3% when like-for-like it is 42% short).
    # The like-for-like counterpart is the model's total target: cycle + safety.
    df["naive_total_cover"]  = d_bar * cfg["baseline_weeks_of_cover"]
    df["model_total_target"] = df["rop_observed"]

    # Intermittent series: the normal model is invalid, so flag rather than
    # publish a number that would look authoritative and be wrong.
    df["is_intermittent"] = df["zero_week_share"] > cfg["intermittency_threshold"]

    thr = cfg["intermittency_threshold"]
    aw = cfg["activity_window_weeks"]
    df["exclusion_reason"] = np.select(
        [df["is_intermittent"] & df["is_stale"], df["is_intermittent"], df["is_stale"]],
        [f"intermittent (>{thr:.0%} zero weeks) and no sale in {aw}+ weeks",
         f"intermittent demand: >{thr:.0%} zero weeks, normal model invalid",
         f"stale: no sale within {aw} weeks of calc_date"],
        default=None)
    df["is_publishable"] = ~(df["is_intermittent"] | df["is_stale"])
    return df
