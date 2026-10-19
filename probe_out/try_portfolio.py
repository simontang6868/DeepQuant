import sys, json, time
from pathlib import Path
sys.path.insert(0, "backend/src")
from deepquant.api.config import get_data_root
from deepquant.api.schemas import PortfolioBacktestRequest
from deepquant.api.portfolio import run_portfolio

syms = ["600519.SH","000858.SZ","601318.SH","000001.SZ","002415.SZ","601398.SH"]
req = PortfolioBacktestRequest(
    dataset_symbols=syms,
    initial_cash=1_000_000,
    min_assets=5,
    start_date="2019-01-01",
    end_date="2024-12-31",
)
t=time.perf_counter()
r = run_portfolio(req, get_data_root())
print("elapsed", round(time.perf_counter()-t,2), "s")
print("period", r["period"])
print("selection", {k:r["selection"][k] for k in ("n_signal_dates","n_empty_periods","avg_holdings")})
p=r["performance"]
print("perf", {k:p[k] for k in ("cumulative_return","annualized_return","max_drawdown_ratio","annualized_volatility")})
print("sharpe", p["sharpe"]["value"], "turnover", p["turnover"]["one_way_annualized"], "cost", p["cost"]["cost_to_gross_ratio"])
print("evidence", p["evidence"])
print("fills", len(r["fills"]), "blocked", len(r["blocked"]), "states", len(r["states"]))
print("blocked reasons", {})
from collections import Counter
print(Counter(b["reason"] for b in r["blocked"]))
print("consistency", r["consistency_issues"][:3])
print("limitations", len(r["limitations"]))
json.dumps(r, ensure_ascii=False)
print("json ok, bytes", len(json.dumps(r, ensure_ascii=False)))
