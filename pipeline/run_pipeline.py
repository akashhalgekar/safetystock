"""Wire the stages. Each logs; failures raise and stop the run."""
import argparse, datetime as dt, logging, sys, yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.db import get_conn, get_engine
from pipeline import buffer_engine as be
from pipeline.quality import run_checks
from pipeline.load import load_params

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
log = logging.getLogger("pipeline")

CFG = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())


def main(calc_date):
    conn, eng = get_conn(), get_engine()
    log.info("EXTRACT")
    w = CFG["demand_window_weeks"]
    demand   = be.extract_demand(eng, w, calc_date)
    activity = be.extract_activity(eng, calc_date)
    fulfil   = be.extract_fulfilment(eng)
    rev      = be.extract_revenue(eng, w, calc_date)
    if demand.empty or fulfil.empty:
        raise ValueError("empty source data; refusing to publish parameters")
    log.info("  demand rows=%d (trailing %dw to %s)  fulfilment series=%d",
             len(demand), w, calc_date, len(fulfil))

    log.info("TRANSFORM")
    params = be.compute_buffers(be.demand_stats(demand), fulfil, rev, CFG,
                                activity=activity, calc_date=calc_date)
    log.info("  computed %d series: %d publishable, %d intermittent, %d stale",
             len(params), int(params["is_publishable"].sum()),
             int(params["is_intermittent"].sum()), int(params["is_stale"].sum()))

    log.info("VALIDATE")
    run_checks(params, conn)

    log.info("LOAD")
    n = load_params(conn, params, calc_date)
    conn.close()
    return n


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    # The last delivered order in this dataset is 2018-08-29, so a calc_date
    # later than that claims a freshness the data does not have.
    p.add_argument("--calc-date", default="2018-08-31",
                   help="Logical date the parameters are computed AS OF. "
                        "Defaults to the dataset's last order week, not today.")
    main(dt.date.fromisoformat(p.parse_args().calc_date))
