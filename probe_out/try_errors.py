import sys
sys.path.insert(0, "backend/src")
from pydantic import ValidationError
from deepquant.api.schemas import PortfolioBacktestRequest
from deepquant.api.config import get_data_root
from deepquant.api.portfolio import run_portfolio

def show(label, fn):
    try:
        fn(); print(label, "-> OK (no error)")
    except ValidationError as e:
        print(label, "-> 422:", e.errors()[0]["msg"][:90])
    except Exception as e:
        print(label, "->", type(e).__name__, str(e)[:110], "status", getattr(e,"status_code",None))

base = dict(dataset_symbols=["600519.SH","000858.SZ"], initial_cash=1_000_000)
show("min_assets>len", lambda: PortfolioBacktestRequest(**base, min_assets=5))
show("dup symbol", lambda: PortfolioBacktestRequest(dataset_symbols=["600519.SH","600519.SH"], initial_cash=1e6))
show("one symbol", lambda: PortfolioBacktestRequest(dataset_symbols=["600519.SH"], initial_cash=1e6))
show("skip>=lookback", lambda: PortfolioBacktestRequest(**base, min_assets=2, lookback=20, skip=20))
show("bad date", lambda: PortfolioBacktestRequest(**base, min_assets=2, start_date="2024-01-01", end_date="2020-01-01"))
show("unknown field", lambda: PortfolioBacktestRequest(**base, min_assets=2, foo=1))
show("not registered", lambda: run_portfolio(PortfolioBacktestRequest(dataset_symbols=["600519.SH","999999.SH"], initial_cash=1e6, min_assets=2), get_data_root()))
show("range out", lambda: run_portfolio(PortfolioBacktestRequest(dataset_symbols=["600519.SH","000858.SZ"], initial_cash=1e6, min_assets=2, start_date="2030-01-01", end_date="2031-01-01"), get_data_root()))
