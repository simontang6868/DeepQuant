import sys, json, time
sys.path.insert(0, "backend/src")
from deepquant.api.config import get_data_root
from deepquant.api.schemas import PortfolioBacktestRequest
from deepquant.api.portfolio import run_portfolio
codes = json.load(open('probe_out/ds.json',encoding='utf-8'))['datasets']
syms=[x['symbol'] for x in codes]
req = PortfolioBacktestRequest(dataset_symbols=syms, initial_cash=1_000_000,
    start_date="2019-01-01", end_date="2024-12-31")
t=time.perf_counter(); r=run_portfolio(req, get_data_root()); el=time.perf_counter()-t
print("n_symbols",len(syms),"elapsed",round(el,2),"s","bytes",len(json.dumps(r,ensure_ascii=False)))
print("period",r["period"])
print("selection",{k:r["selection"][k] for k in ("n_signal_dates","n_empty_periods","avg_holdings","min_holdings","max_holdings")})
p=r["performance"]
print("cum",round(p["cumulative_return"]*100,2),"% ann",round(p["annualized_return"]*100,2),"% mdd",round(p["max_drawdown_ratio"]*100,2),"% vol",round(p["annualized_volatility"]*100,2))
print("sharpe",round(p["sharpe"]["value"],3),"turnover",round(p["turnover"]["one_way_annualized"],2),"cost",round(p["cost"]["cost_to_gross_ratio"],3))
print("evidence",p["evidence"])
print("fills",len(r["fills"]),"blocked",len(r["blocked"]),"consistency",len(r["consistency_issues"]))
from collections import Counter
print(Counter(b["reason"] for b in r["blocked"]))
