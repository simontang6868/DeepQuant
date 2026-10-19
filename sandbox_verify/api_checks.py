#!/usr/bin/env python
"""自检清单 §4 #2~#5、#8 的自动化版本：只用标准库，直接打 HTTP，不依赖 PYTHONPATH。

用法（服务已在跑的前提下）：
    python sandbox_verify/api_checks.py                       # 默认 http://127.0.0.1:8123
    python sandbox_verify/api_checks.py --base http://127.0.0.1:8000

它做的事（逐条打印"期望 / 实际 / 结果"，不做静默断言）：
    #2  GET  /health                        -> {"status":"ok"}
    #3  GET  /api/datasets                  -> datasets 51 条、invalid 0 条
    #4  POST /api/backtests                 -> final_equity=1413448.06、fills=109
    #8  POST /api/backtests（同参数再来一次） -> 与 #4 逐位一致
    #5  POST /api/portfolio-backtests       -> cumulative_return=-0.4272、fills=120
    附加 GET  /api/strategies               -> 至少含 ma_cross

退出码：全部通过 = 0；任何一条不符 = 1（便于脚本化）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

SINGLE_BODY = {
    "dataset_symbol": "600519.SH",
    "strategy_id": "ma_cross",
    "strategy_params": {"fast_window": 5, "slow_window": 20},
    "initial_cash": 1000000,
    "start_date": "2018-01-01",
    "end_date": "2024-12-31",
    "signal_lag": 1,
    "save": True,
}

# 注意：组合接口的字段名是 dataset_symbols / top_fraction；
# 传 symbols / top_pct 会被 extra="forbid" 直接判 422 —— 这是设计如此，不是缺陷。
PORTFOLIO_BODY = {
    "dataset_symbols": ["600519.SH", "000858.SZ", "000001.SZ", "601398.SH", "601318.SH"],
    "initial_cash": 1000000,
    "selection": "momentum_top",
    "top_fraction": 0.4,
    "lookback": 60,
    "skip": 5,
    "min_assets": 2,
    "start_date": "2021-01-01",
    "end_date": "2024-12-31",
}


def _request(base: str, path: str, body: dict | None = None, timeout: float = 180.0):
    url = base.rstrip("/") + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    # 显式绕过系统代理：本机回环地址不该走 HTTP_PROXY，
    # 否则在设了代理的环境里会收到 502 "upstream connect failed"（curl 通常自动绕过，urllib 不会）。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return 0, f"连接失败：{exc.reason}"
    except json.JSONDecodeError:
        return -1, "响应不是合法 JSON"


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def report(self, label: str, expected: str, actual: str, ok: bool) -> None:
        print(f"{label:<38}{expected:<30}{actual:<32}{'PASS' if ok else 'FAIL'}")
        if not ok:
            self.failures.append(f"{label}：期望 {expected}，实际 {actual}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8123", help="服务地址")
    args = parser.parse_args()
    base = args.base
    c = Checker()

    print("=" * 108)
    print(f"DeepQuant 自检 §4 #2~#5 / #8   base={base}")
    print("=" * 108)
    print(f"{'检查项':<38}{'期望':<30}{'实际':<32}{'结果'}")
    print("-" * 108)

    # #2 /health
    status, body = _request(base, "/health")
    ok = status == 200 and isinstance(body, dict) and body.get("status") == "ok"
    c.report("#2 GET /health", '{"status":"ok"}', f"HTTP {status} {json.dumps(body, ensure_ascii=False)}", ok)

    # #3 /api/datasets
    status, body = _request(base, "/api/datasets")
    n_ds = len(body.get("datasets", [])) if isinstance(body, dict) else -1
    n_bad = len(body.get("invalid_datasets", [])) if isinstance(body, dict) else -1
    ok = status == 200 and n_ds == 51 and n_bad == 0
    c.report("#3 GET /api/datasets", "datasets=51, invalid=0", f"HTTP {status} datasets={n_ds}, invalid={n_bad}", ok)

    # 附加 /api/strategies
    status, body = _request(base, "/api/strategies")
    ids = []
    if isinstance(body, dict):
        items = body.get("strategies") or body.get("items") or []
        ids = [s.get("id") or s.get("strategy_id") for s in items if isinstance(s, dict)]
    elif isinstance(body, list):
        ids = [s.get("id") or s.get("strategy_id") for s in body if isinstance(s, dict)]
    c.report("附加 GET /api/strategies", "含 ma_cross", f"HTTP {status} ids={ids}", "ma_cross" in ids)

    # #4 单标的
    status, body = _request(base, "/api/backtests", SINGLE_BODY)
    if status != 200 or not isinstance(body, dict):
        c.report("#4 POST /api/backtests", "HTTP 200", f"HTTP {status} {str(body)[:60]}", False)
        equity_1 = fills_1 = None
    else:
        perf = body["result"]["performance"]
        equity_1 = perf["final_equity"]
        fills_1 = len(body["result"]["fills"])
        ok = abs(equity_1 - 1413448.06) < 0.01 and fills_1 == 109
        c.report(
            "#4 POST /api/backtests",
            "final_equity=1413448.06, fills=109",
            f"final_equity={equity_1:.2f}, fills={fills_1}",
            ok,
        )
        print(f"     run_id={body.get('run_id')}  cumulative_return={perf.get('cumulative_return')}")

    # #8 同参数二次（逐位一致）
    status, body = _request(base, "/api/backtests", SINGLE_BODY)
    if status != 200 or not isinstance(body, dict) or equity_1 is None:
        c.report("#8 同参数二次请求", "与 #4 完全一致", f"HTTP {status}", False)
    else:
        perf = body["result"]["performance"]
        equity_2 = perf["final_equity"]
        fills_2 = len(body["result"]["fills"])
        same = equity_2 == equity_1 and fills_2 == fills_1
        c.report(
            "#8 同参数二次请求",
            "与 #4 逐位一致",
            f"final_equity={equity_2:.2f}, fills={fills_2}",
            same,
        )
        print(f"     两个 run_id 不同是正常的（每次落库一条记录）；比的是数值不是 id")

    # #5 组合
    status, body = _request(base, "/api/portfolio-backtests", PORTFOLIO_BODY)
    if status != 200 or not isinstance(body, dict):
        c.report("#5 POST /api/portfolio-backtests", "HTTP 200", f"HTTP {status} {str(body)[:80]}", False)
    else:
        perf = body["result"]["performance"]
        cum = perf["cumulative_return"]
        n_fills = len(body["result"]["fills"])
        ok = abs(cum - (-0.4272)) < 0.00005 and n_fills == 120
        c.report(
            "#5 POST /api/portfolio-backtests",
            "cumulative_return=-0.4272, fills=120",
            f"cumulative_return={cum:.4f}, fills={n_fills}",
            ok,
        )
        fee_source = body["result"]["config"].get("fee_source")
        fee_note = "占位参数" if fee_source == "simulation_placeholder" else str(fee_source)
        print(
            f"     final_value={perf.get('final_value')}  "
            f"max_drawdown_ratio={perf.get('max_drawdown_ratio')}  "
            f"fee_source={fee_source}（{fee_note}）"
        )

    print("-" * 108)
    print("=" * 108)
    if c.failures:
        print(f"总计：FAIL（{len(c.failures)} 项）")
        for f in c.failures:
            print(f"  - {f}")
        return 1
    print("总计：PASS —— 服务可起、数据集 51、单标的与组合回测数值与文档一致、结果可复现")
    return 0


if __name__ == "__main__":
    sys.exit(main())
