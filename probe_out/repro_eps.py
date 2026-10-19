import sys, tempfile
from pathlib import Path
sys.path.insert(0,"backend/src")
import pandas as pd
from deepquant.data.pipeline import ingest_daily_csv
import deepquant.backtest.portfolio as P
from deepquant.api.config import get_data_root
from deepquant.api.schemas import PortfolioBacktestRequest
from deepquant.api.portfolio import run_portfolio

# 关闭一致性检查，先拿到 result 看真实数值
orig = P.PortfolioBacktestResult.consistency_issues
P.PortfolioBacktestResult.consistency_issues = lambda self: []

tmp = Path(tempfile.mkdtemp())
root = tmp/"data"; root.mkdir()
n=320
dates = pd.date_range("2021-01-04", periods=n, freq="B")
for sym, drift in {"AAA01":0.02,"AAA02":0.01,"AAA03":-0.005}.items():
    closes=[10.0*(1.0+drift)**(i/20.0) for i in range(n)]
    lines=["date,open,high,low,close,volume,amount"]
    for d,c in zip(dates,closes):
        lines.append(f"{d:%Y-%m-%d},{c*0.999:.4f},{c*1.01:.4f},{c*0.99:.4f},{c:.4f},10000.0,{c*10000:.2f}")
    src=tmp/f"{sym}.csv"; src.write_text("\n".join(lines)+"\n",encoding="utf-8")
    ingest_daily_csv(src, symbol=sym, data_root=root)

req=PortfolioBacktestRequest(dataset_symbols=["AAA01","AAA02","AAA03"], initial_cash=1_000_000,
    min_assets=3, lookback=60, skip=5, selection="equal_weight_all")
r=run_portfolio(req, root.resolve())
bad=[]
for s in r["states"]:
    if s["total_equity"] is None: continue
    d=abs(s["total_equity"]-(s["cash"]+s["market_value"]))
    if d>0: bad.append((s["date"],s["cash"],s["market_value"],s["total_equity"],d))
print("n_diff",len(bad))
for b in bad[:6]: print(b)
print("max diff", max((b[4] for b in bad), default=0))
