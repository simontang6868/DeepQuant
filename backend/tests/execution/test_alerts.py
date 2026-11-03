"""Phase 2.7：监控告警的行为测试。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.7-a ~ 2.7-b。

五类告警各一条主线测试 + 边界测试，**不合并**。合并成"跑一遍看有没有告警"
只能证明函数被调到了，证明不了"这一类异常触发时它会不会响"。

⭐ 两条容易写成恒真的断言，单独列在这里
1. **"不发邮件"不是"我没写发信代码"**。`test_alerts_module_has_no_mail_capability`
   用 AST 扫 import 与调用，任何人加 `smtplib` 都会红。
2. **"接近熔断"必须有常量定义**。`warn_ratio=0.8` 写死在签名里，
   测试同时验证预警线与熔断线之间的那一段——只测"熔断了会报"是不够的，
   那时已经晚了。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from deepquant.broker.models import BrokerOrder, BrokerOrderStatus
from deepquant.broker.models import Cash, Position
from deepquant.execution.alerts import (
    Alert,
    AlertKind,
    FileSink,
    HeartbeatState,
    MemorySink,
    MultiSink,
    PrintSink,
    Severity,
    check_data_freshness,
    check_drawdown,
    check_heartbeat,
    check_order_anomalies,
    check_reconcile,
    run_all_checks,
)
from deepquant.execution.reconciler import reconcile
from deepquant.market.rules import Side

SRC = (pathlib.Path(__file__).resolve().parents[2]
       / "src" / "deepquant" / "execution" / "alerts.py")

DAY = 20261030


def _order(status, *, coid="c-1", code="600990", filled=0, ts=1,
           reject_code="") -> BrokerOrder:
    return BrokerOrder(client_order_id=coid, broker_order_id="B-" + coid,
                       code=code, side=Side.BUY, shares=200, limit_price=10.0,
                       status=status, filled_shares=filled,
                       reject_code=reject_code, updated_ts=ts)


# --------------------------------------------------------------------------- 1. 心跳


def test_never_ran_is_critical_not_silence():
    """从未成功运行过必须报 CRITICAL。

    ⚠️ 这一条最容易写错：``last_success_day is None`` 时若"没有可比对象就跳过"，
    一个从未启动过的系统会永远安静——而它恰恰是最该被报的状态。
    """
    alerts = check_heartbeat(HeartbeatState(last_success_day=None), ts=DAY)
    assert len(alerts) == 1
    assert alerts[0].severity is Severity.CRITICAL
    assert alerts[0].kind is AlertKind.HEARTBEAT_MISSING


def test_no_missed_days_is_silent():
    assert check_heartbeat(HeartbeatState(DAY, 0), ts=DAY) == []


def test_single_missed_day_is_warn():
    a = check_heartbeat(HeartbeatState(20261029, 1), ts=DAY)[0]
    assert a.severity is Severity.WARN
    assert a.evidence["consecutive_missed"] == 1
    assert "容忍" in a.message


def test_three_missed_days_escalates_to_critical():
    """⭐ 3 天与前瞻台账的补跑容忍 ``MAX_LATE_RUN_DAYS=3`` 对齐。"""
    a = check_heartbeat(HeartbeatState(20261028, 3), ts=DAY)[0]
    assert a.severity is Severity.CRITICAL
    assert "作废" in a.message


# --------------------------------------------------------------------------- 2. 数据新鲜度


TRADING_DAYS = [20261019, 20261020, 20261021, 20261022, 20261023,
                20261026, 20261027, 20261028, 20261029, 20261030]


def test_fresh_data_is_silent():
    assert check_data_freshness(data_last_day=DAY, asof_day=DAY,
                                trading_days=TRADING_DAYS, ts=DAY) == []


def test_one_day_lag_is_within_tolerance():
    assert check_data_freshness(data_last_day=20261029, asof_day=DAY,
                                trading_days=TRADING_DAYS,
                                max_lag_trading_days=1, ts=DAY) == []


def test_lag_uses_trading_days_not_calendar_days():
    """⭐ 数据停在 10-23，今天 10-30：自然日 7 天，交易日 5 天。

    用自然日判会在大长假上误报（休市 9 天不是故障），用交易日才和
    「漏了几次运行」对得上。
    """
    a = check_data_freshness(data_last_day=20261023, asof_day=20261030,
                             trading_days=TRADING_DAYS, ts=DAY)[0]
    assert a.evidence["lag_trading_days"] == 5, (
        "算成 7 就是按自然日算的——周末两天被算进了滞后")
    # 容忍 1 个交易日、3 倍容忍=3；5 > 3 ⇒ CRITICAL
    assert a.severity is Severity.CRITICAL


def test_long_lag_becomes_critical():
    a = check_data_freshness(data_last_day=20261019, asof_day=DAY,
                             trading_days=TRADING_DAYS, ts=DAY)[0]
    assert a.evidence["lag_trading_days"] == 9
    assert a.severity is Severity.CRITICAL


def test_future_data_is_not_a_staleness_alert():
    """数据比 asof 还新（跨日跑）不该报"陈旧"——报的是别的问题。"""
    assert check_data_freshness(data_last_day=20261102, asof_day=DAY,
                                trading_days=TRADING_DAYS, ts=DAY) == []


# --------------------------------------------------------------------------- 3. 订单异常


def test_rejected_order_is_warn_with_reject_code():
    a = check_order_anomalies(
        [_order(BrokerOrderStatus.REJECTED, reject_code="limit_up")], ts=DAY)[0]
    assert a.severity is Severity.WARN
    assert a.evidence["reject_code"] == "limit_up"
    assert a.code == "600990"


def test_unknown_order_is_critical_because_it_blocks_new_orders():
    """⭐ UNKNOWN 判 CRITICAL 而非 WARN：闸门会拒绝所有新单，系统已停摆。"""
    a = check_order_anomalies([_order(BrokerOrderStatus.UNKNOWN)], ts=DAY)[0]
    assert a.severity is Severity.CRITICAL
    assert "阻断" in a.message


def test_stale_pending_order_is_reported_only_when_threshold_given():
    o = _order(BrokerOrderStatus.ACCEPTED, ts=20261020)
    assert check_order_anomalies([o], ts=DAY) == [], "未给阈值就不该报"
    a = check_order_anomalies([o], ts=DAY, stale_order_days=3,
                              current_day=DAY)[0]
    assert a.severity is Severity.WARN
    assert a.evidence["status"] == "accepted"


def test_normal_orders_produce_no_alerts():
    orders = [_order(BrokerOrderStatus.FILLED, filled=200),
              _order(BrokerOrderStatus.ACCEPTED, coid="c-2")]
    assert check_order_anomalies(orders, ts=DAY) == []


# --------------------------------------------------------------------------- 4. 对账差异


def _diffs():
    return reconcile(local_positions=[Position("600990", 1000, 1000)],
                     broker_positions=[Position("600990", 800, 800)],
                     local_cash=Cash(100.0, 0.0, 100.0),
                     broker_cash=Cash(100.0, 0.0, 100.0), ts=DAY)


def test_each_difference_becomes_an_alert_with_field_and_values():
    alerts = check_reconcile(_diffs(), ts=DAY)
    assert len(alerts) == 2, "两条差异（quantity + available）应各报一条"
    for a in alerts:
        assert a.kind is AlertKind.RECONCILE_DIFF
        assert a.severity is Severity.CRITICAL
        assert "field" in a.evidence
        assert a.code == "600990"
    msgs = " ".join(a.message for a in alerts)
    assert "quantity" in msgs and "available" in msgs


def test_clean_reconcile_produces_no_alert():
    r = reconcile(local_positions=[Position("600990", 1000, 1000)],
                  broker_positions=[Position("600990", 1000, 1000)],
                  local_cash=Cash(100.0, 0.0, 100.0),
                  broker_cash=Cash(100.0, 0.0, 100.0), ts=DAY)
    assert check_reconcile(r, ts=DAY) == []


def test_duplicate_fill_reports_a_separate_warning():
    """重复回报去重后账是一致的（所以不是 RECONCILE_DIFF），但仍是故障信号。"""
    from deepquant.broker.models import Fill

    f = Fill(fill_id="f-1", client_order_id="c-1", code="600990",
             side=Side.BUY, shares=200, price=10.0, ts=1)
    r = reconcile(local_positions=[], broker_positions=[],
                  local_cash=Cash(100.0, 0.0, 100.0),
                  broker_cash=Cash(100.0, 0.0, 100.0),
                  local_fills=[f], broker_fills=[f, f], ts=DAY)
    assert r.ok is True
    alerts = check_reconcile(r, ts=DAY)
    assert len(alerts) == 1
    assert alerts[0].severity is Severity.WARN
    assert alerts[0].evidence["fill_id"] == "f-1"


# --------------------------------------------------------------------------- 5. 回撤


def test_drawdown_below_warn_line_is_silent():
    # 峰值 100，当前 98 ⇒ 回撤 2%；熔断 2.5%，预警线 2.0% ⇒ 恰好到线
    assert check_drawdown(equity=98.1, peak_equity=100.0, breaker_dd=0.025,
                          ts=DAY) == []


def test_drawdown_at_warn_line_is_warn():
    """⭐ 预警线的定义必须写死：``0.8 × 熔断线``。"""
    a = check_drawdown(equity=98.0, peak_equity=100.0, breaker_dd=0.025, ts=DAY)[0]
    assert a.severity is Severity.WARN
    assert a.evidence["warn_line"] == pytest.approx(0.02)
    assert a.evidence["drawdown"] == pytest.approx(0.02)


def test_drawdown_at_breaker_line_is_critical():
    a = check_drawdown(equity=97.5, peak_equity=100.0, breaker_dd=0.025, ts=DAY)[0]
    assert a.severity is Severity.CRITICAL
    assert "熔断线" in a.message


def test_missing_peak_is_critical_not_silently_zero():
    """⭐⭐ P5 的告警侧：峰值丢失时回撤无法计算，必须报出来。

    若实现写成 ``dd = 1 - equity/peak`` 而不检查 peak，``peak=0`` 会 ZeroDivisionError；
    若写成 ``if not peak: return []``，熔断失效却无人知道。
    """
    a = check_drawdown(equity=100.0, peak_equity=0.0, breaker_dd=0.025, ts=DAY)[0]
    assert a.severity is Severity.CRITICAL
    assert "熔断等于失效" in a.message


# --------------------------------------------------------------------------- 投递


def test_sinks_receive_every_alert_without_dedup():
    """⭐ 刻意不做去重：同一异常连报 3 轮就该有 3 条。"""
    sink = MemorySink()
    orders = [_order(BrokerOrderStatus.UNKNOWN)]
    for _ in range(3):
        run_all_checks(ts=DAY, orders=orders, sink=sink)
    assert len(sink.alerts) == 3
    assert sink.n_critical == 3


def test_file_sink_appends_only(tmp_path):
    p = tmp_path / "alerts.jsonl"
    s = FileSink(p)
    s.emit(Alert(AlertKind.DATA_STALE, Severity.WARN, 1, "first"))
    before = p.read_bytes()
    s.emit(Alert(AlertKind.DATA_STALE, Severity.WARN, 2, "second"))
    after = p.read_bytes()
    assert after[:len(before)] == before
    assert [r["message"] for r in s.read()] == ["first", "second"]


def test_multi_sink_fans_out(tmp_path):
    mem = MemorySink()
    fs = FileSink(tmp_path / "a.jsonl")
    a = Alert(AlertKind.DATA_STALE, Severity.WARN, 1, "x")
    MultiSink(mem, fs).emit(a)
    assert len(mem.alerts) == 1
    assert len(fs.read()) == 1


def test_print_sink_is_readable():
    import io as _io
    buf = _io.StringIO()
    PrintSink(buf).emit(Alert(AlertKind.ORDER_ANOMALY, Severity.CRITICAL, 1,
                              "委托状态未知", code="600990"))
    out = buf.getvalue()
    assert "CRITICAL" in out and "order_anomaly" in out and "600990" in out


def test_run_all_checks_returns_and_delivers_same_set(tmp_path):
    sink = MemorySink()
    alerts = run_all_checks(
        ts=DAY,
        heartbeat=HeartbeatState(20261029, 1),
        data=dict(data_last_day=20261019, asof_day=DAY,
                  trading_days=TRADING_DAYS),
        orders=[_order(BrokerOrderStatus.UNKNOWN)],
        reconcile_result=_diffs(),
        drawdown=dict(equity=98.0, peak_equity=100.0, breaker_dd=0.025),
        sink=sink,
    )
    assert [a.to_line() for a in alerts] == [a.to_line() for a in sink.alerts]
    kinds = {a.kind for a in alerts}
    assert kinds == {AlertKind.HEARTBEAT_MISSING, AlertKind.DATA_STALE,
                     AlertKind.ORDER_ANOMALY, AlertKind.RECONCILE_DIFF,
                     AlertKind.DRAWDOWN_NEAR_BREAKER}


# --------------------------------------------------------------------------- 2.7-b 不发邮件


def _called(src: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_alerts_module_has_no_mail_capability():
    """⭐ 2.7-b 的可执行形式：**不发送邮件**。

    扫的是 import 与调用，不是字符串——文档里写「不发邮件」不该被误判。
    """
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
    banned_mods = [m for m in mods
                   if m.split(".")[0] in {"smtplib", "imaplib", "email", "poplib"}]
    assert banned_mods == [], f"alerts.py 引入了发信相关模块：{banned_mods}"

    banned_calls = {c for c in _called(src)
                    if c in {"sendmail", "send_message", "smtp_send", "SendMessage"}}
    assert banned_calls == set(), f"alerts.py 出现发信调用：{banned_calls}"


def test_mail_scanner_can_actually_catch_smtplib():
    """检查器自检：塞一段用 smtplib 的代码，确认扫得出来。"""
    bad = "import smtplib\ns = smtplib.SMTP('x')\ns.sendmail('a','b','c')\n"
    tree = ast.parse(bad)
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
    assert "smtplib" in mods
    assert "sendmail" in _called(bad)


# --------------------------------------------------------------------------- Alert 本身


def test_alert_requires_message():
    with pytest.raises(ValueError, match="message"):
        Alert(AlertKind.DATA_STALE, Severity.WARN, 1, "")


def test_alert_kind_and_severity_must_be_enums():
    with pytest.raises(TypeError):
        Alert("data_stale", Severity.WARN, 1, "x")   # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Alert(AlertKind.DATA_STALE, "warn", 1, "x")  # type: ignore[arg-type]


def test_alert_evidence_is_serialisable():
    a = Alert(AlertKind.DATA_STALE, Severity.WARN, 1, "x",
              evidence={"n": 3, "s": "ok", "l": [1, 2]})
    import json
    assert json.loads(a.to_line())["evidence"] == {"n": 3, "s": "ok", "l": [1, 2]}
