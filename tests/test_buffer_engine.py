"""
Unit tests. No database required: the payoff for keeping transform functions
free of connections.
"""
import sys, pathlib
import numpy as np, pandas as pd, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pipeline import buffer_engine as be

CFG = {
    "service_levels": {"A": 0.98, "B": 0.95, "C": 0.90},
    "abc_thresholds": {"A": 0.80, "B": 0.95},
    "xyz_thresholds": {"X": 0.50, "Y": 1.00},
    "intermittency_threshold": 0.40,
    "activity_window_weeks": 13,
    "baseline_weeks_of_cover": 2.0,
}


def _one_series(demand_mean=40.0, demand_sd=12.0, zero_share=0.0, cv=0.3,
                obs_mean=14.0, obs_sd=7.7, pro_mean=21.0, pro_sd=1.4, rev=1000.0):
    stats = pd.DataFrame([{"category": "a", "seller_id": "s", "weeks": 26,
                           "demand_mean": demand_mean, "demand_sd": demand_sd,
                           "zero_week_share": zero_share, "cv": cv}])
    fulfil = pd.DataFrame([{"category": "a", "seller_id": "s", "n_deliveries": 50,
                            "observed_mean_days": obs_mean, "observed_sd_days": obs_sd,
                            "promised_mean_days": pro_mean, "promised_sd_days": pro_sd,
                            "pct_late": 0.1}])
    revenue = pd.DataFrame([{"category": "a", "seller_id": "s", "annualised_revenue": rev}])
    return stats, fulfil, revenue


def test_z_factors_match_published_values():
    assert round(be.z_from_service_level(0.95), 2) == 1.64
    assert round(be.z_from_service_level(0.98), 2) == 2.05
    assert round(be.z_from_service_level(0.99), 2) == 2.33


def test_zero_demand_weeks_are_counted_not_dropped():
    """Dropping zero weeks understates variability and under-buffers."""
    d = pd.DataFrame({"category": ["a"]*6, "seller_id": ["s"]*6,
                      "purchase_week": range(6), "units": [10, 0, 10, 0, 10, 0]})
    out = be.demand_stats(d)
    assert out.loc[0, "weeks"] == 6
    assert out.loc[0, "demand_mean"] == 5.0
    assert out.loc[0, "zero_week_share"] == 0.5


def test_formula_matches_hand_calculation():
    """SS = Z*sqrt(LT*sd_D^2 + D_bar^2*sd_LT^2), checked by hand.

    This is the test that caught the ABC classification bug: the single series
    was being classified C instead of A, so the wrong Z was applied.
    """
    out = be.compute_buffers(*_one_series(), CFG)
    expected_sigma = np.sqrt(2.0 * 12.0**2 + 40.0**2 * 1.1**2)   # 14/7, 7.7/7
    z = be.z_from_service_level(0.98)                            # single series => A
    assert out.loc[0, "ss_observed"] == pytest.approx(z * expected_sigma, rel=1e-6)
    assert out.loc[0, "rop_observed"] == pytest.approx(40.0 * 2.0 + z * expected_sigma, rel=1e-6)


def test_abc_uses_exclusive_cumulative_share():
    """The largest series must be class A, even when it alone exceeds 80%."""
    stats = pd.DataFrame([{"category": "a", "seller_id": str(i), "weeks": 26,
                           "demand_mean": 10.0, "demand_sd": 2.0,
                           "zero_week_share": 0.0, "cv": 0.2} for i in range(3)])
    fulfil = pd.DataFrame([{"category": "a", "seller_id": str(i), "n_deliveries": 20,
                            "observed_mean_days": 14.0, "observed_sd_days": 3.0,
                            "promised_mean_days": 21.0, "promised_sd_days": 2.0,
                            "pct_late": 0.1} for i in range(3)])
    rev = pd.DataFrame([{"category": "a", "seller_id": "0", "annualised_revenue": 900.0},
                        {"category": "a", "seller_id": "1", "annualised_revenue": 80.0},
                        {"category": "a", "seller_id": "2", "annualised_revenue": 20.0}])
    out = be.compute_buffers(stats, fulfil, rev, CFG).set_index("seller_id")
    assert out.loc["0", "abc"] == "A"      # 90% of revenue, must be A


def test_intermittent_series_flagged_with_reason():
    out = be.compute_buffers(*_one_series(demand_mean=2.0, demand_sd=3.0,
                                          zero_share=0.7, cv=1.5), CFG)
    assert bool(out.loc[0, "is_intermittent"]) is True
    assert "intermittent" in out.loc[0, "exclusion_reason"]


def test_supply_variance_share_is_a_proportion():
    out = be.compute_buffers(*_one_series(), CFG)
    share = out.loc[0, "supply_variance_share"]
    assert 0.0 <= share <= 1.0
    assert share > 0.8          # sd_LT=1.1wk against D_bar=40 should dominate


def test_safety_stock_never_negative_on_degenerate_input():
    out = be.compute_buffers(*_one_series(demand_mean=0.0, demand_sd=0.0,
                                          zero_share=1.0, cv=np.nan,
                                          obs_mean=5.0, obs_sd=0.0,
                                          pro_mean=10.0, pro_sd=0.0, rev=0.0), CFG)
    assert out.loc[0, "ss_observed"] >= 0


def test_promised_and_observed_are_computed_separately():
    """The whole finding depends on these not collapsing into one another."""
    out = be.compute_buffers(*_one_series(), CFG)
    assert out.loc[0, "ss_observed"] != out.loc[0, "ss_promised"]
    assert out.loc[0, "cycle_promised"] > out.loc[0, "cycle_observed"]


def test_naive_baseline_is_a_total_cover_policy():
    """A flat "N weeks of cover" rule is TOTAL on-hand, so its like-for-like
    counterpart is the model's total target (cycle + safety), not safety alone.
    Comparing it against safety stock alone understated how short the flat rule
    is, and made C-class look within 0.3% when it is 38% short."""
    out = be.compute_buffers(*_one_series(), CFG)
    assert out.loc[0, "naive_total_cover"] == pytest.approx(40.0 * 2.0)
    assert out.loc[0, "model_total_target"] == pytest.approx(out.loc[0, "rop_observed"])
    assert out.loc[0, "model_total_target"] > out.loc[0, "ss_observed"]


def test_stale_series_is_flagged_not_published():
    """A series that stopped selling must not be published as a current
    parameter. 12% of an earlier population had not sold in six months."""
    stats, fulfil, rev = _one_series()
    activity = pd.DataFrame([{"category": "a", "seller_id": "s",
                              "last_sale_week": pd.Timestamp("2018-01-01")}])
    out = be.compute_buffers(stats, fulfil, rev, CFG,
                             activity=activity, calc_date="2018-08-31")
    assert bool(out.loc[0, "is_stale"]) is True
    assert bool(out.loc[0, "is_publishable"]) is False
    assert "stale" in out.loc[0, "exclusion_reason"]


def test_recent_series_is_publishable():
    stats, fulfil, rev = _one_series()
    activity = pd.DataFrame([{"category": "a", "seller_id": "s",
                              "last_sale_week": pd.Timestamp("2018-08-20")}])
    out = be.compute_buffers(stats, fulfil, rev, CFG,
                             activity=activity, calc_date="2018-08-31")
    assert bool(out.loc[0, "is_stale"]) is False
    assert bool(out.loc[0, "is_publishable"]) is True
