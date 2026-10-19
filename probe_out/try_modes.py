import sys, json
sys.path.insert(0,"backend/src")
from deepquant.api.config import get_data_root
from deepquant.api.schemas import PortfolioBacktestRequest
from deepquant.api.portfolio import run_portfolio
syms=json.load(open('probe_out/ds.json',encoding='utf-8'))
syms=[x['symbol'] for x in syms['datasets']]
for label, kw in [("equal_weight_all", dict(selection="equal_weight_all")),
                  ("lots_allocation", dict(allocation="lots"))]:
    req=PortfolioBacktestRequest(dataset_symbols=syms, initial_cash=1_000_000,
        start_date="2019-01-01", end_date="2024-12-31", **kw)
    r=run_portfolio(req, get_data_root())
    p=r["performance"]
    from collections import Counter
    print(label, "| cum", round(p["cumulative_return"]*100,2), "| ann", round(p["annualized_return"]*100,2),
          "| mdd", round(p["max_drawdown_ratio"]*100,2), "| fills", len(r["fills"]),
          "| top_frac", r["config"]["top_fraction"], "| avg_hold", round(r["selection"]["avg_holdings"],2))
    print("   blocked", Counter(b["reason"] for b in r["blocked"]).most_common())
