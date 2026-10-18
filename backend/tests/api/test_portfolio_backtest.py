"""POST /portfolio-backtests 契约测试。

覆盖三类：
1. **能跑通**：多标的 → 调仓表 → 组合净值，响应结构完整且自洽；
2. **该拒绝就拒绝**：池子凑不满 min_assets、重复代码、单只、未登记、区间外——
   这些如果放行，得到的是「全程空仓但看起来正常」的结果，比报错危险得多；
3. **如实标注**：flat 10% 涨跌停、权重记账不受整手约束、min_assets 偏离预登记，
   必须出现在 limitations 里（界面靠它决定要不要给用户看警告）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from deepquant.data.pipeline import ingest_daily_csv

# lookback=60 需要至少 60 个交易日的窗口；给 320 天留出 skip 与信号日余量
DATA_ROWS = 320


def _make_pool_csv(path: Path, *, n: int, drift: float) -> Path:
    """生成带 amount 列的确定性日线（不同 drift 让各标的动量分数不同）。

    必须带 ``amount``：截面取分默认执行「成交额为正」检查（P2），
    缺列会让 CrossSectionPanel 直接拒绝——这是真实约束，不该在测试里绕开。
    """
    dates = pd.date_range("2021-01-04", periods=n, freq="B")
    closes = [10.0 * (1.0 + drift) ** (i / 20.0) for i in range(n)]
    lines = ["date,open,high,low,close,volume,amount"]
    for d, c in zip(dates, closes):
        open_ = c * 0.999
        high = c * 1.01
        low = c * 0.99
        volume = 10_000.0
        lines.append(
            f"{d:%Y-%m-%d},{open_:.4f},{high:.4f},{low:.4f},{c:.4f},{volume:.1f},{c * volume:.2f}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def ingest_pool(data_root: Path, tmp_path: Path, specs: dict[str, float], *, n: int = DATA_ROWS):
    """按 {symbol: drift} 逐个走正规导入管线登记数据集。"""
    for symbol, drift in specs.items():
        src_dir = tmp_path / f"src_{symbol}"
        src_dir.mkdir(exist_ok=True)
        src = _make_pool_csv(src_dir / f"{symbol}.csv", n=n, drift=drift)
        ingest_daily_csv(src, symbol=symbol, data_root=data_root)


@pytest.fixture
def pool_client(make_client, data_root, tmp_path):
    """已登记 4 只标的的客户端（走势互不相同，动量分数可区分）。"""
    ingest_pool(
        data_root,
        tmp_path,
        {"AAA01": 0.02, "AAA02": 0.01, "AAA03": -0.005, "AAA04": 0.03},
    )
    return make_client(data_root)


def _request(**overrides):
    payload = {
        "dataset_symbols": ["AAA01", "AAA02", "AAA03"],
        "initial_cash": 1_000_000,
        "min_assets": 3,
        "lookback": 60,
        "skip": 5,
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# 能跑通
# --------------------------------------------------------------------------- #


def test_minimal_portfolio_run_returns_complete_document(pool_client) -> None:
    response = pool_client.post("/portfolio-backtests", json=_request())
    assert response.status_code == 200

    body = response.json()
    result = body["result"]

    assert result["schema_version"] == "portfolio-v1"
    assert body["api"]["name"] == "DeepQuant"
    assert result["config"]["n_symbols"] == 3
    assert result["config"]["allocation"] == "weights"
    # 组合层单一涨跌停比例必须在响应里可核对（不是文档里的口头承诺）
    assert result["engine"]["price_limit_rate"] == pytest.approx(0.10)

    # 净值序列与信号日序列都非空：否则「跑通了」只是空壳
    assert len(result["states"]) > 0
    assert result["selection"]["n_signal_dates"] > 0
    assert len(result["targets"]) == result["selection"]["n_signal_dates"]

    # 引擎自检必须干净（权益恒等式、现金非负、成交额自洽）
    assert result["consistency_issues"] == []
    assert result["period"]["start_date"] <= result["period"]["end_date"]


def test_targets_never_exceed_full_investment(pool_client) -> None:
    result = pool_client.post("/portfolio-backtests", json=_request()).json()["result"]
    for target in result["targets"]:
        total = sum(target["weights"].values())
        assert total <= 1.0 + 1e-9
        assert all(w >= 0 for w in target["weights"].values())


def test_fills_reference_only_pool_symbols(pool_client) -> None:
    """成交里出现的标的必须都在请求的池内（否则说明选股跑到了池外）。"""
    result = pool_client.post("/portfolio-backtests", json=_request()).json()["result"]
    pool = set(result["config"]["symbols"])
    for fill in result["fills"]:
        assert fill["symbol"] in pool
    for order in result["blocked"]:
        assert order["symbol"] in pool


def test_momentum_top_picks_fewer_than_pool(pool_client) -> None:
    """top_fraction=0.2 时 3 只池 → 每期最多 ceil(3*0.2)=1 只，持仓数必须体现这一点。"""
    result = pool_client.post("/portfolio-backtests", json=_request()).json()["result"]
    assert result["selection"]["max_holdings"] <= 1
    assert result["config"]["top_fraction"] == pytest.approx(0.2)


def test_equal_weight_all_forces_top_fraction_one(pool_client) -> None:
    result = pool_client.post(
        "/portfolio-backtests",
        json=_request(selection="equal_weight_all", min_assets=3),
    ).json()["result"]
    assert result["config"]["top_fraction"] == pytest.approx(1.0)
    assert result["selection"]["max_holdings"] == 3
    # 全池等权时必须说明它放弃了动量筛选
    assert any("全池等权" in note for note in result["limitations"])


def test_lots_allocation_is_accepted_and_recorded(pool_client) -> None:
    result = pool_client.post(
        "/portfolio-backtests", json=_request(allocation="lots")
    ).json()["result"]
    assert result["config"]["allocation"] == "lots"
    assert result["config"]["lot_size"] == 100


def test_states_equity_equals_cash_plus_market_value(pool_client) -> None:
    """净值恒等式在 API 输出层再验一次：序列化不能把现金/市值搞错位。"""
    result = pool_client.post("/portfolio-backtests", json=_request()).json()["result"]
    for state in result["states"]:
        if state["total_equity"] is None:
            continue
        assert state["total_equity"] == pytest.approx(
            state["cash"] + state["market_value"], abs=1e-6
        )


# --------------------------------------------------------------------------- #
# 该拒绝就拒绝
# --------------------------------------------------------------------------- #


def test_min_assets_larger_than_pool_is_rejected(pool_client) -> None:
    """池子凑不满 min_assets 时整段会空仓——必须 422，不能返回一条现金直线。"""
    response = pool_client.post(
        "/portfolio-backtests", json=_request(min_assets=5)
    )
    assert response.status_code == 422
    detail = json.dumps(response.json(), ensure_ascii=False)
    assert "全程空仓" in detail


def test_duplicate_symbols_rejected(pool_client) -> None:
    response = pool_client.post(
        "/portfolio-backtests",
        json=_request(dataset_symbols=["AAA01", "AAA01"]),
    )
    assert response.status_code == 422
    assert "不得重复" in json.dumps(response.json(), ensure_ascii=False)


def test_single_symbol_rejected(pool_client) -> None:
    """1 只不是组合，是单标的——那应该走 /backtests。"""
    response = pool_client.post(
        "/portfolio-backtests", json=_request(dataset_symbols=["AAA01"], min_assets=1)
    )
    assert response.status_code == 422


def test_unknown_field_rejected(pool_client) -> None:
    response = pool_client.post("/portfolio-backtests", json=_request(momentum=0.1))
    assert response.status_code == 422


def test_skip_must_be_smaller_than_lookback(pool_client) -> None:
    response = pool_client.post(
        "/portfolio-backtests", json=_request(lookback=60, skip=60)
    )
    assert response.status_code == 422
    assert "skip" in json.dumps(response.json(), ensure_ascii=False)


def test_unregistered_symbol_returns_404(pool_client) -> None:
    response = pool_client.post(
        "/portfolio-backtests", json=_request(dataset_symbols=["AAA01", "NOPE99"], min_assets=2)
    )
    assert response.status_code == 404
    assert "NOPE99" in json.dumps(response.json(), ensure_ascii=False)


def test_range_outside_data_is_rejected(pool_client) -> None:
    response = pool_client.post(
        "/portfolio-backtests",
        json=_request(start_date="2030-01-01", end_date="2030-12-31"),
    )
    assert response.status_code == 400
    assert "没有任何可用行情" in json.dumps(response.json(), ensure_ascii=False)


def test_start_after_end_rejected(pool_client) -> None:
    response = pool_client.post(
        "/portfolio-backtests",
        json=_request(start_date="2022-06-01", end_date="2022-01-01"),
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# 如实标注
# --------------------------------------------------------------------------- #


def test_limitations_state_the_unfixable_parts(pool_client) -> None:
    result = pool_client.post("/portfolio-backtests", json=_request()).json()["result"]
    notes = " ".join(result["limitations"])
    assert "10%" in notes and "per-symbol" in notes  # flat 10% 涨跌停
    assert "幸存者偏差" in notes                       # 池快照偏差
    assert "整手" in notes                             # 权重记账不受整手约束


def test_min_assets_below_preregistration_is_flagged(pool_client) -> None:
    """min_assets 偏离预登记 §5 时必须在 limitations 里说明，不能悄悄降标。"""
    result = pool_client.post(
        "/portfolio-backtests", json=_request(min_assets=2)
    ).json()["result"]
    assert any("min_assets=2" in note for note in result["limitations"])
    assert result["selection"]["min_assets"] == 2


def test_small_pool_is_flagged(pool_client) -> None:
    result = pool_client.post("/portfolio-backtests", json=_request()).json()["result"]
    assert any("少于预登记" in note for note in result["limitations"])


def test_coverage_note_explains_data_end(pool_client) -> None:
    """区间上限来自数据覆盖终点，必须在响应里说清楚，不能让用户以为是接口限制。"""
    result = pool_client.post("/portfolio-backtests", json=_request()).json()["result"]
    coverage = result["coverage"]
    assert coverage["note"]
    assert "数据覆盖" in coverage["note"] or "止于" in coverage["note"]
    assert set(coverage["symbol_end_dates"]) == {"AAA01", "AAA02", "AAA03"}


def test_datasets_block_carries_fingerprints(pool_client) -> None:
    """组合结果要能追溯到「用的是哪份数据」，否则结论无法复核。"""
    result = pool_client.post("/portfolio-backtests", json=_request()).json()["result"]
    assert len(result["datasets"]) == 3
    for entry in result["datasets"]:
        assert entry["rows"] > 0
        assert entry["source_sha256"] and len(entry["source_sha256"]) == 64


def test_performance_block_has_definitions(pool_client) -> None:
    """夏普/换手/成本必须带口径定义：没有定义的指标无法被质疑。"""
    result = pool_client.post("/portfolio-backtests", json=_request()).json()["result"]
    perf = result["performance"]
    assert perf is not None
    assert perf["sharpe"]["definition"]
    assert perf["turnover"]["definition"]
    assert perf["cost"]["definition"]
    assert perf["evidence"]["level"] in ("low", "adequate")


def test_data_root_dependency_is_used(make_client, data_root) -> None:
    """空数据根目录：不是 500，而是 404（数据集未登记），且不泄露路径。"""
    client = make_client(data_root)
    response = client.post("/portfolio-backtests", json=_request())
    assert response.status_code == 404
    assert str(data_root) not in response.text


def test_engine_does_not_receive_client_paths() -> None:
    """symbol 只能是标识符：路径分隔符/盘符在参数校验阶段就被拒（400/422）。"""
    from deepquant.api.schemas import PortfolioBacktestRequest

    with pytest.raises(Exception):
        PortfolioBacktestRequest(
            dataset_symbols=["../../etc/passwd", "AAA01"],
            initial_cash=1e6,
            min_assets=2,
        )
    # 单个 `..` 也必须在 schema 层被拒（不是等到查文件才 404）
    with pytest.raises(Exception):
        PortfolioBacktestRequest(
            dataset_symbols=["..", "AAA01"], initial_cash=1e6, min_assets=2
        )
