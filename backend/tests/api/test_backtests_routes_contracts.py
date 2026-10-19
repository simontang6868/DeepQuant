"""api/routes/backtests.py 契约测试（A13+，覆盖率补缺：142 stmts / 7 miss → 100%）。

既有 ``test_backtests_api.py`` / ``test_backtest_compare_api.py`` 覆盖了正常路径与参数边界，
但**四个错误/降级分支从未被触发**（coverage term-missing：行 77-78、103-104、350、410-411）。
这些恰好是最容易悄悄坏掉、且坏了以后最容易被误判成「数据有问题」的地方，所以单独补一层契约测试：

| 缺口行 | 分支 | 契约 |
|---|---|---|
| 77-78 | ``_load_dataset_frame`` 读取失败 | 任何读取异常都翻译成 ``DatasetUnavailableError``（404），**且消息不得泄露文件系统路径**（模块 docstring 明文承诺） |
| 103-104 | ``_build_config`` 配置校验失败 | ``ValueError`` 必须翻译成 ``ConfigInvalidError``（422），不得裸奔成 500 |
| 350 | ``_extract_metrics`` 的 ``trade_stats`` 非 dict | 五项交易统计显式为 ``None``，**绝不伪造 0**（docstring 明文承诺） |
| 410-411 | ``compare_backtests`` 结果文档无法还原 | 翻译成 ``RunIntegrityError``，且**必须指认是哪一条 run 坏了** |

另外补三条与红线直接相关的自检：
- ``_build_config`` 产出的 ``fee_source`` 恒为 ``simulation_placeholder``（R4：费用是模拟占位，不得伪装真实券商费率）；
- 日期边界 ``None`` 必须原样传递为 ``None``，而不是退化成 ``NaT``；
- ``compare_backtests`` **只读**，比较过程中不得写库。

全部为语义断言：检验被违反的具体不变量与返回值本身，不是「先跑一遍再把结果写死成断言」。
纯加法，**未改 ``src/`` 任何一行**。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from deepquant.api import routes
from deepquant.api.errors import ConfigInvalidError, DatasetUnavailableError, RunIntegrityError
from deepquant.api.routes import backtests as bt
from deepquant.api.schemas import BacktestCompareRequest, BacktestRequest
from deepquant.backtest.config import FEE_SOURCE_SIMULATION
from deepquant.persistence import count_runs, get_db_path, get_run_with_document

# 故意做成「绝不该出现在客户端响应里」的样子
_SECRET_PATH = "C:\\srv\\internal\\datasets\\secret-2026.parquet"


# --------------------------------------------------------------------------- #
# 1. _load_dataset_frame：读取失败 → DatasetUnavailableError（行 77-78）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "boom",
    [
        FileNotFoundError(_SECRET_PATH),
        PermissionError("permission denied"),
        ValueError("parquet footer is corrupt"),
        RuntimeError("arrow reader crashed"),
    ],
    ids=["FileNotFoundError", "PermissionError", "ValueError", "RuntimeError"],
)
def test_read_failure_becomes_dataset_unavailable(monkeypatch, boom):
    """任何读取异常都必须翻译成 DatasetUnavailableError ——『读不了』是数据集不可用，不是服务端炸了。

    断言要点：异常类型、**原始异常被挂在 __cause__ 上**（不得吞掉现场）、消息里带上出错的数据集标识。
    """

    def _boom(_path):
        raise boom

    monkeypatch.setattr(bt, "read_daily_parquet", _boom)

    with pytest.raises(DatasetUnavailableError) as excinfo:
        bt._load_dataset_frame(Path(_SECRET_PATH), "TEST01")

    err = excinfo.value
    assert err.status_code == 404
    assert err.code == "dataset_unavailable"
    assert "TEST01" in str(err)
    assert err.__cause__ is boom, "必须保留原始异常现场，否则线上无法定位根因"


def test_read_failure_message_does_not_leak_filesystem_path(monkeypatch):
    """安全契约：模块 docstring 承诺『不向客户端暴露文件系统路径』。

    这是可被外部观察到的行为，必须钉死 —— 否则某天有人把 exc 拼进 message，
    内部结构就顺着 404 响应泄漏出去了。
    """

    def _boom(_path):
        raise FileNotFoundError(_SECRET_PATH)

    monkeypatch.setattr(bt, "read_daily_parquet", _boom)

    with pytest.raises(DatasetUnavailableError) as excinfo:
        bt._load_dataset_frame(Path(_SECRET_PATH), "TEST01")

    message = str(excinfo.value)
    assert _SECRET_PATH not in message
    assert "secret-2026" not in message
    assert ".parquet" not in message


def test_load_dataset_frame_success_returns_frame_unchanged(monkeypatch):
    """成功路径不得被包装改变：返回的必须就是读取到的那个对象本身（不是副本、不是重排）。"""
    frame = pd.DataFrame({"close": [1.0, 2.0]})
    monkeypatch.setattr(bt, "read_daily_parquet", lambda _path: frame)

    result = bt._load_dataset_frame(Path("no-such-file.parquet"), "TEST01")

    assert result is frame


# --------------------------------------------------------------------------- #
# 2. _build_config：ValueError → ConfigInvalidError（行 103-104）
# --------------------------------------------------------------------------- #


def _request_bypassing_pydantic(**overrides) -> BacktestRequest:
    """构造绕过 Pydantic 运行时校验的请求对象。

    用途：HTTP 层已经由 Pydantic 拦过一道，但 ``_build_config`` 的 try/except 是**第二道防线**
    （防御内部调用方 / Pydantic 规则未来放宽）。要验证这道防线存在，就必须构造出违反校验的输入。
    使用 ``model_construct`` 而不是 monkeypatch 或修改模型，属于纯测试侧行为。
    """
    base = {
        "dataset_symbol": "TEST01",
        "strategy_id": "ma_cross",
        "strategy_params": {},
        "initial_cash": 100000.0,
        "start_date": None,
        "end_date": None,
        "signal_lag": 1,
        "save": True,
    }
    base.update(overrides)
    return BacktestRequest.model_construct(**base)


def test_non_positive_initial_cash_becomes_config_invalid():
    """initial_cash 非正 → ConfigInvalidError(422)，不得是裸 ValueError（那会变成 500 内部错误）。"""
    req = _request_bypassing_pydantic(initial_cash=-1.0)

    with pytest.raises(ConfigInvalidError) as excinfo:
        bt._build_config(req)

    err = excinfo.value
    assert type(err) is ConfigInvalidError
    assert err.status_code == 422
    assert err.code == "invalid_config"
    assert "initial_cash" in str(err)
    # 原始 ValueError 必须被保留为 __cause__，便于服务端记录细节
    assert isinstance(err.__cause__, ValueError)


def test_reversed_date_range_becomes_config_invalid():
    """start_date 晚于 end_date → ConfigInvalidError，且消息保留原始不变量描述。"""
    req = _request_bypassing_pydantic(start_date=date(2021, 1, 1), end_date=date(2020, 1, 1))

    with pytest.raises(ConfigInvalidError) as excinfo:
        bt._build_config(req)

    assert "start_date" in str(excinfo.value)
    assert "end_date" in str(excinfo.value)


def test_negative_fee_rate_becomes_config_invalid():
    """负费率同样落在第二道防线上（Pydantic 之外的一致性由 BacktestConfig 兜底）。"""
    req = _request_bypassing_pydantic(commission_rate=-0.001)

    with pytest.raises(ConfigInvalidError) as excinfo:
        bt._build_config(req)

    assert "commission_rate" in str(excinfo.value)


def test_invalid_signal_lag_becomes_config_invalid():
    """signal_lag < 1 也必须被翻译成 422，而不是让引擎在半路炸掉。"""
    req = _request_bypassing_pydantic(signal_lag=0)

    with pytest.raises(ConfigInvalidError) as excinfo:
        bt._build_config(req)

    assert "signal_lag" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 3. _build_config 字段映射契约
# --------------------------------------------------------------------------- #


def test_field_mapping_is_faithful():
    """请求字段必须一一映射到 BacktestConfig，不重排、不改语义。"""
    req = BacktestRequest(
        dataset_symbol="TEST01",
        strategy_id="ma_cross",
        strategy_params={"fast_window": 5, "slow_window": 20},
        initial_cash=250000.0,
        start_date=date(2020, 1, 2),
        end_date=date(2024, 12, 31),
        signal_lag=2,
        commission_rate=0.0004,
        min_commission=5.0,
        stamp_duty_rate=0.001,
        transfer_fee_rate=0.00002,
        slippage_buy=0.002,
        slippage_sell=0.003,
    )

    cfg = bt._build_config(req)

    assert cfg.symbol == "TEST01"
    assert cfg.initial_cash == 250000.0
    assert cfg.signal_lag == 2
    assert cfg.commission_rate == 0.0004
    assert cfg.min_commission == 5.0
    assert cfg.stamp_duty_rate == 0.001
    assert cfg.transfer_fee_rate == 0.00002
    assert cfg.slippage_buy == 0.002
    assert cfg.slippage_sell == 0.003


def test_date_becomes_naive_timestamp():
    """date 必须转成**无时区**的 pd.Timestamp；带时区的时戳会在下游比较大小时报错。"""
    req = BacktestRequest(
        dataset_symbol="TEST01",
        strategy_id="ma_cross",
        initial_cash=100000.0,
        start_date=date(2020, 3, 4),
        end_date=date(2021, 5, 6),
    )

    cfg = bt._build_config(req)

    assert cfg.start_date == pd.Timestamp("2020-03-04")
    assert cfg.end_date == pd.Timestamp("2021-05-06")
    assert cfg.start_date.tzinfo is None
    assert cfg.end_date.tzinfo is None


def test_absent_dates_stay_none_not_nat():
    """未给日期必须保持 None，不能退化成 NaT —— NaT 会让『是否设了边界』的语义消失。"""
    req = BacktestRequest(dataset_symbol="TEST01", strategy_id="ma_cross", initial_cash=100000.0)

    cfg = bt._build_config(req)

    assert cfg.start_date is None
    assert cfg.end_date is None


def test_fee_source_is_always_simulation_placeholder():
    """R4 红线：API 层请求的费用参数再怎么填，产出的配置都必须标注为**模拟占位费率**。

    把这条做成断言，等于把「不许把模拟费率说成真实券商费率」变成可执行检查 ——
    哪天有人让客户端能指定真实 broker 费率而不改这条来源标识，测试会立刻红。
    """
    req = BacktestRequest(
        dataset_symbol="TEST01",
        strategy_id="ma_cross",
        initial_cash=100000.0,
        commission_rate=0.000000001,  # 故意填一个「看起来像真实低佣」的值
    )

    cfg = bt._build_config(req)

    assert cfg.fee_source == FEE_SOURCE_SIMULATION == "simulation_placeholder"
    # 请求里显式覆盖的值必须照实传递（API 层不替用户改写费率），但来源标识恒为占位
    assert cfg.commission_rate == 0.000000001
    assert cfg.assumptions, "配置必须携带假设说明，不得默默省略（R4 可追溯）"
    assert isinstance(cfg.assumptions, tuple)
    assert all(isinstance(a, str) for a in cfg.assumptions)


# --------------------------------------------------------------------------- #
# 4. _extract_metrics：trade_stats 非 dict → 显式 None（行 350）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bogus", [None, [], "not-a-dict", 42, {"unexpected": 1}])
def test_non_dict_trade_stats_yields_none_not_zero(bogus):
    """docstring 契约：『缺失指标显式为 null；绝不伪造 0』。

    一旦这里回填 0，下游就会把「没有交易」当成「交易 0 次且胜率 0%」，属于凭空造数据。
    """
    document = {
        "performance": {
            "initial_equity": 100000.0,
            "final_equity": 123456.0,
            "trade_stats": bogus,
        }
    }

    metrics = bt._extract_metrics(document)

    assert metrics.n_orders is None
    assert metrics.n_filled_orders is None
    assert metrics.n_fills is None
    assert metrics.n_closed_trades is None
    assert metrics.win_rate is None
    # 同一文档里其它真实存在的指标仍须照常提取 —— 不能因为 trade_stats 坏了就整体作废
    assert metrics.initial_equity == 100000.0
    assert metrics.final_equity == 123456.0


@pytest.mark.parametrize("bogus", [None, [], "NaN", True])
def test_missing_or_non_dict_performance_yields_all_none(bogus):
    """performance 缺失或非 dict 时，全部指标为 None（而不是抛异常让整个 compare 挂掉）。"""
    metrics = bt._extract_metrics({"performance": bogus})

    for field in (
        "initial_equity",
        "final_equity",
        "total_pnl",
        "cumulative_return",
        "annualized_return",
        "annualized_volatility",
        "max_drawdown_amount",
        "max_drawdown_ratio",
        "n_orders",
        "n_filled_orders",
        "n_fills",
        "n_closed_trades",
        "win_rate",
    ):
        assert getattr(metrics, field) is None, f"{field} 应当为 None"


def test_non_numeric_metrics_are_dropped_not_coerced():
    """非数值一律降级为 None —— 字符串等非数值不得被强转成数字补位。"""
    document = {
        "performance": {
            "final_equity": -0.0,
            "trade_stats": {"n_orders": "12", "win_rate": 0.5},
        }
    }

    metrics = bt._extract_metrics(document)

    assert metrics.n_orders is None, "'12' 是字符串，不得被强转成 12"
    assert metrics.final_equity == -0.0, "真实的 -0.0 必须照原值保留，不得被抹成 None"
    assert metrics.win_rate == 0.5, "真实数值必须从 trade_stats 照原值提取"


def test_bool_metrics_are_rejected_not_coerced_to_one():
    """布尔不得被当成数值：bool 是 int 的子类，`win_rate: true` 被当成 1 等于凭空捏造 100% 胜率。"""
    document = {"performance": {"trade_stats": {"win_rate": True, "n_orders": True}}}

    metrics = bt._extract_metrics(document)

    assert metrics.win_rate is None, "bool 必须被拒绝，不能被当成数值 1"
    assert metrics.n_orders is None


# --------------------------------------------------------------------------- #
# 5. compare_backtests：结果文档无法还原 → RunIntegrityError（行 410-411）
# --------------------------------------------------------------------------- #


def _create_two_runs(client_with_dataset, payload) -> tuple[str, str]:
    """通过真实 API 先落两条回测，再用它们的真实 record/document 做后续隔离实验。"""
    return tuple(
        client_with_dataset.post("/backtests", json=payload).json()["run_id"] for _ in range(2)
    )


def test_unparsable_document_becomes_run_integrity_error(
    client_with_dataset, valid_backtest_payload, monkeypatch
):
    """还原失败必须翻译成 RunIntegrityError（500），不得伪装成 404 —— 记录明明存在。

    500 是刻意的：这是服务端数据完整性问题，不是客户端找错了 id。
    """
    created = _create_two_runs(client_with_dataset, valid_backtest_payload)
    db_path = get_db_path()
    docs = {rid: get_run_with_document(db_path, rid) for rid in created}

    monkeypatch.setattr(bt, "get_run_with_document", lambda db, rid: docs[rid])

    def _boom(_doc):
        raise KeyError("performance")

    monkeypatch.setattr(bt, "result_from_dict", _boom)

    with pytest.raises(RunIntegrityError) as excinfo:
        bt.compare_backtests(BacktestCompareRequest(run_ids=list(created)))

    err = excinfo.value
    assert err.status_code == 500
    assert err.code != "run_not_found", "完整性错误绝不能被降级成『找不到』"
    assert isinstance(err.__cause__, KeyError), "原始还原异常必须挂到 __cause__ 上"


def test_integrity_error_names_the_offending_run(
    client_with_dataset, valid_backtest_payload, monkeypatch
):
    """错误消息必须**指认具体是哪一条 run 坏了**，否则用户面对两条 id 只能靠猜。"""
    good, bad = _create_two_runs(client_with_dataset, valid_backtest_payload)
    db_path = get_db_path()
    docs = {rid: get_run_with_document(db_path, rid) for rid in (good, bad)}

    monkeypatch.setattr(bt, "get_run_with_document", lambda db, rid: docs[rid])

    real_from_dict = bt.result_from_dict
    restored: list[str] = []

    def _selective(doc):
        if doc is docs[bad][1]:
            raise ValueError("schema mismatch")
        restored.append("ok")
        return real_from_dict(doc)

    monkeypatch.setattr(bt, "result_from_dict", _selective)

    with pytest.raises(RunIntegrityError) as excinfo:
        bt.compare_backtests(BacktestCompareRequest(run_ids=[good, bad]))

    message = str(excinfo.value)
    assert bad in message, f"错误消息应点名损坏的 run {bad}，实际：{message}"
    assert good not in message, "不应把没坏的那条也点名，否则等于谎报"
    assert restored, "good 那条应被正常还原过（证明坏掉的确实只有 bad，变量已隔离）"


def test_compare_does_not_write_to_db(client_with_dataset, valid_backtest_payload, monkeypatch):
    """契约：比较是只读端点，比较前后历史记录条数不变，且不触发任何保存调用。"""
    created = _create_two_runs(client_with_dataset, valid_backtest_payload)

    def _forbid_write(*_a, **_k):
        raise AssertionError("compare 是只读端点，不得调用 save_backtest_result")

    monkeypatch.setattr(bt, "save_backtest_result", _forbid_write)

    before = count_runs(get_db_path())
    resp = client_with_dataset.post("/backtests/compare", json={"run_ids": list(created)})
    assert resp.status_code == 200, resp.json()
    assert count_runs(get_db_path()) == before


def test_routes_module_imports_are_stable():
    """防护：上述测试全部依赖模块级符号（可被 monkeypatch），改包名/改名时应被这里拦下。"""
    for name in (
        "read_daily_parquet",
        "result_from_dict",
        "get_run_with_document",
        "save_backtest_result",
    ):
        assert hasattr(bt, name), f"{name} 应可从 api.routes.backtests 直接替换"

    assert routes.backtests is bt
