"""决策日 / 月末校验的测试（2026-11-03，R2 第 2 项）。

覆盖 R2 明确要求的四类时点：**跨月、跨年、停牌月末、缺数据月末**，
外加两条失败关闭路径（日历缺失、日历被截断在月中）。

## 这组测试要钉住的到底是什么

原实现里「数据末日」来自**抽样 40 只**股票文件日期的并集。它的问题不是"可能不准"，
而是**错的时候不报错**：

- 若这 40 只在月末停牌 → 并集最大日期停在月末前一天 → 决策日提前一天；
- 若数据更新少拉了一天 → 并集同样停在月末前一天 → 表现完全一样。

两种情况都返回一个**格式合法**的决策日，台账上看不出异常。所以测试里有一条专门
用「前 40 只停在 09-30、第 45 只到 10-30」的构造文件，同时断言旧的抽样口径会得到
错的日期——**如果哪天有人把全池扫描改回抽样，这条测试会红**。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import p13_forward as pf  # noqa: E402
import trade_calendar as tcal  # noqa: E402


# ---------------------------------------------------------------- 公用

@pytest.fixture
def real_cal() -> list[str]:
    """仓库里的真实日历缓存。缺失就跳过——本机没有缓存时不该让测试假绿。"""
    try:
        return tcal.load()
    except tcal.CalendarUnavailable as e:  # pragma: no cover
        pytest.skip(f"无日历缓存：{e}")


@pytest.fixture(autouse=True)
def _clear_cache():
    pf._LAST_DATE_CACHE.clear()
    yield
    pf._LAST_DATE_CACHE.clear()


def _write_qfq(dirpath: pathlib.Path, code: str, last_date: str,
               tail: str = "\n") -> pathlib.Path:
    p = dirpath / f"{code}.qfq.csv"
    p.write_text("date,open,high,low,close,volume\n"
                 f"2026-09-01,1,1,1,1,100\n"
                 f"{last_date[:4]}-{last_date[4:6]}-{last_date[6:]},1,1,1,1,100{tail}",
                 encoding="utf-8")
    return p


# ---------------------------------------------------------------- 跨月 / 跨年

def test_cross_month_october(real_cal):
    """10-30 是 2026-10 的最后一个交易日；11-03 运行 ⇒ 决策日 20261030。"""
    d, why = pf.latest_closed_month(cal=["20261029", "20261030"],
                                    today="20261103", official=real_cal)
    assert d == "20261030"
    assert "独立日历" in why, "说明里必须体现月末是核对过的，而不是默认"


def test_cross_year_december_to_january(real_cal):
    """跨年：数据止于 2026-12-31，2027-01-04 运行 ⇒ 决策日 20261231。

    跨年是最容易出错的一类：`today[:6] > last[:6]` 的字符串比较在跨年时仍然正确，
    但如果有实现去算「月份差」而忘了年份，这里就会红。
    """
    d, why = pf.latest_closed_month(cal=["20261230", "20261231"],
                                    today="20270104", official=real_cal)
    assert d == "20261231"


def test_november_month_end_is_1130(real_cal):
    """为一个还没到来的月末先钉死答案：2026-11-30（周一）。"""
    assert tcal.last_trading_day("202611", real_cal) == "20261130"


# ---------------------------------------------------------------- 停牌月末

def test_month_end_suspension_does_not_pin_decision_day(real_cal):
    """⭐⭐ 停牌月末：抽样股在月末停牌，不得把月末前一天当月末。

    构造的是原实现的真实失效路径：若 40 只抽样股在 09-30 全部停牌，
    并集最大日期 = 09-29，旧实现会返回 20260929 并且**认为一切正常**。
    现在必须抛 `MonthEndUnverified`，且消息里点名正确的月末。
    """
    # today 取 10-05（国庆假期内，gap=6 天）以确保**过期闸门放行**，
    # 这样被触发的只能是月末校验本身——否则这条测试会因为"数据过期"而绿，
    # 看起来在验月末，实际验的是另一条规则（本项目已犯过多次的错法）。
    with pytest.raises(pf.MonthEndUnverified) as ei:
        pf.latest_closed_month(cal=["20260929"], today="20261005", official=real_cal)
    msg = str(ei.value)
    assert "20260930" in msg, "错误消息必须给出真正的月末，否则运维不知道该等哪一天"
    assert "20260929" in msg


# ---------------------------------------------------------------- 缺数据月末

def test_missing_last_day_data_is_rejected(real_cal):
    """缺数据月末：真实月末是 10-30，但数据只到 10-29 ⇒ 必须拒绝。

    这一条和上一条的**现象相同、原因不同**（一个是停牌、一个是没拉到），
    处置也不同（等复牌 vs 重跑更新）。所以两条都要有，且都不能靠"gap 太大"来兜底
    ——gap 只有 1 天，`MAX_DECISION_AGE_DAYS=7` 根本拦不住。
    """
    with pytest.raises(pf.MonthEndUnverified) as ei:
        pf.latest_closed_month(cal=["20261028", "20261029"],
                               today="20261103", official=real_cal)
    assert "20261030" in str(ei.value)


def test_month_end_gap_guard_does_not_mask_incomplete_data(real_cal):
    """反证：这条路径上 gap=1 天，过期闸门放行，唯一拦截者就是月末校验。

    没有这条，"缺数据月末被拦住了"可能只是因为碰巧过期了，而不是月末校验起了作用。
    """
    today = "20261103"
    gap = (pf._parse(today) - pf._parse("20261029")).days
    assert gap <= pf.MAX_DECISION_AGE_DAYS, "前提变了：这个用例不再是'非过期'场景"


# ---------------------------------------------------------------- 失败关闭

def test_calendar_missing_fails_closed(tmp_path):
    """日历文件不存在 ⇒ 抛异常，**不得**退回按星期几猜。

    猜的代价：春节/国庆所在月份的"最后一个工作日"根本不是交易日，
    而这类错误一年只发生几次，恰好每次都在月末。
    """
    with pytest.raises(tcal.CalendarUnavailable):
        tcal.load(tmp_path / "nope.json")


def test_calendar_truncated_mid_month_is_not_covered():
    """日历被截断在月中 ⇒ `CalendarNotCovered`（不能把月中那天当月末）。"""
    days = ["20261008", "20261009", "20261012", "20261013", "20261014", "20261015"]
    with pytest.raises(tcal.CalendarNotCovered):
        tcal.ensure_covered(days, "202610")
    with pytest.raises(tcal.CalendarNotCovered):
        tcal.last_trading_day("202610", days)


def test_calendar_covering_whole_month_is_accepted():
    """本月有交易日且日历已越过月末 ⇒ 覆盖成立（正例，防止上一条写成了恒假）。"""
    days = ["20261029", "20261030", "20261102"]
    tcal.ensure_covered(days, "202610")
    assert tcal.last_trading_day("202610", days) == "20261030"


def test_calendar_tamper_is_detected(tmp_path):
    """日历被手工改过 ⇒ 读取时报错（sha256 自校验）。

    为什么值得一条测试：日历决定月末，改它等于改所有决策日，
    而它是一份纯文本 JSON，用记事本就能改。
    """
    days = ["20261029", "20261030", "20261102", "20261103"]
    p = tcal.save(days, tmp_path / "cal.json")
    payload = json.loads(p.read_text(encoding="utf-8"))
    payload["dates"][1] = "20261031"          # 伪造一个周六为交易日
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(tcal.CalendarUnavailable, match="sha256"):
        tcal.load(p)


def test_calendar_staleness_is_detected(tmp_path):
    """日历过旧 ⇒ 报错。新年假日表发布后不刷新，1 月的月末判断会用去年的假日表。"""
    p = tcal.save(["20261029", "20261030", "20261102"], tmp_path / "cal.json")
    payload = json.loads(p.read_text(encoding="utf-8"))
    payload["fetch_ts"] = "2025-01-01T00:00:00"
    # 重写后 sha256 只覆盖 dates，仍然自洽 ⇒ 这条测的确实是"陈旧"而不是"被改"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(tcal.CalendarUnavailable, match="陈旧"):
        tcal.load(p, today="20261103", max_age_days=400)


# ---------------------------------------------------------------- 全池扫描

def test_tail_scan_sees_dates_beyond_first_40_files(tmp_path, monkeypatch):
    """⭐⭐ 核心回归：数据末日不得由「前 40 个文件」决定。

    构造：`000001`–`000040` 的最后一行停在 20260930（模拟月末停牌/未更新），
    `000045` 停在 20261030。旧的 `fast_calendar()[-1]` 只会看到前者。
    """
    for i in range(1, 41):
        _write_qfq(tmp_path, f"{i:06d}", "20260930")
    _write_qfq(tmp_path, "000045", "20261030")

    monkeypatch.setattr(pf.bp, "QFQ", str(tmp_path))
    assert pf.data_last_date(refresh=True) == "20261030"

    # 反证：旧口径（抽样前 40）得到的是 20260930 —— 与真实值不同。
    old = max(pf._tail_date(str(p)) for p in sorted(tmp_path.glob("*.qfq.csv"))[:40])
    assert old == "20260930"
    assert old != pf.data_last_date(refresh=True), (
        "抽样口径与全池口径结论一致，这条测试就失去意义了")


def test_tail_scan_handles_crlf_and_missing_trailing_newline(tmp_path, monkeypatch):
    """Windows 产出的 CRLF、以及文件末尾没有换行，都必须能解析。

    本项目在换行上出过一次事故（`\\n` 被写成 `\\r\\n` 导致产物字节不一致），
    这里顺手钉住解析端不会因为 CRLF 读不到日期。
    """
    _write_qfq(tmp_path, "000001", "20261029", tail="\r\n")
    _write_qfq(tmp_path, "000002", "20261030", tail="")
    monkeypatch.setattr(pf.bp, "QFQ", str(tmp_path))
    assert pf.data_last_date(refresh=True) == "20261030"


def test_unsorted_input_uses_max_not_last(real_cal):
    """调用方传无序列表时取 max 而不是 `[-1]`。

    原实现用 `me[-1]` 并默认入参有序；一旦有人传进来未排序的集合，决策日会变成
    其中一个随机元素——而它照样能通过后面的所有检查。
    """
    days = ["20261030", "20260930", "20261029"]
    d, _ = pf.latest_closed_month(cal=days, today="20261103", official=real_cal)
    assert d == "20261030"


def test_official_calendar_must_cover_the_whole_month(real_cal):
    """⚠️ 只给月份片段不能当"完整日历"用——这条钉住上一个用例依赖的性质。

    片段 ``["20261029","20261030"]`` 与「日历只更新到 10-30」在字节上无法区分，
    所以必须报未覆盖。理解这一点才能理解为什么生产环境要用整份日历缓存。
    """
    with pytest.raises(tcal.CalendarNotCovered):
        pf.latest_closed_month(cal=["20261030"], today="20261103",
                               official=["20261029", "20261030"])


def test_empty_calendar_raises():
    with pytest.raises(pf.DataStaleError):
        pf.latest_closed_month(cal=[], today="20261103")


# ---------------------------------------------------------------- is_month_end 边界

@pytest.mark.parametrize("day,expected", [
    ("20260930", True),    # 国庆前最后一个交易日
    ("20260929", False),
    ("20261030", True),
    ("20261029", False),
    ("20261130", True),    # 2026-11-30 周一
    ("20261231", True),    # 跨年前最后一个交易日
])
def test_is_month_end_table(real_cal, day, expected):
    assert tcal.is_month_end(day, real_cal) is expected


# ---------------------------------------------------------------- --if-due（日更链入口）
#
# R2 要求「每日任务链」。日更跑 p13 时，一个月里 30 天有 29 天不在决策窗口：
# 没有 --if-due，那 29 天会反复撞上「台账已存在」(rc=5)，看起来像故障，
# 实际是正常。用退出码区分「没事发生」与「出事」，是这条开关存在的全部理由。

import pandas as pd  # noqa: E402


def _stub_snapshot(me: str, close_hi: float | None = None) -> pd.DataFrame:
    rows = [{
        "date": me, "code": f"{300000 + k:06d}", "close": 10.0 + k * 0.1,
        "f2": 1.0, "vol20": 0.02, "f3": 1.0 + k,
        "amt20": 1e7 * (1 + k), "stale_days": 0, "next_td": None,
    } for k in range(30)]
    s = pd.DataFrame(rows)
    base = pd.Series([True] * len(rows))
    s.attrs["base_filter"] = base
    s["ok_buy"] = base
    return s


@pytest.fixture
def fwd_env(tmp_path, monkeypatch):
    fwd = tmp_path / "forward"
    (fwd / "ledger").mkdir(parents=True)
    cfg = fwd / "sp4_config_v1.json"
    cfg.write_text('{"version":"sp4-v1"}', encoding="utf-8")
    import hashlib as _h
    (fwd / "sp4_config_v1.sha256").write_text(
        _h.sha256(cfg.read_bytes()).hexdigest() + "  sp4_config_v1.json\n", encoding="utf-8")
    monkeypatch.setattr(pf, "FWD", fwd)
    monkeypatch.setattr(pf, "LEDGER", fwd / "ledger")
    monkeypatch.setattr(pf, "HB", fwd / "heartbeat.json")
    monkeypatch.setattr(pf, "CORRECTIONS", fwd / "corrections.jsonl")
    monkeypatch.setattr(pf, "KF", cfg)
    monkeypatch.setattr(pf, "build_snapshot", _stub_snapshot)
    # side 列必须给：台账要按买卖方向拆分，缺列会 KeyError（这是真实字段要求，
    # 不是测试桩的灵活性——桩要模拟的是接口形状，不是替实现省略参数）。
    monkeypatch.setattr(pf, "judge_fills", lambda t, d=None: t.assign(
        status="PENDING_NO_CALENDAR", side="BUY"))
    return fwd


def _run(monkeypatch, *argv) -> int:
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["p13_forward.py", *argv])
    return pf.main()


def test_if_due_skips_when_ledger_exists(fwd_env, monkeypatch):
    """台账已存在 ⇒ 非窗口，退出 0 且写 NOT_DUE 心跳（不是故障）。"""
    (fwd_env / "ledger" / "20261030.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pf, "data_last_date", lambda **k: "20261030")
    monkeypatch.setattr(pf, "_today_str", lambda: "20261102")
    assert _run(monkeypatch, "--if-due") == 0
    hb = json.loads((fwd_env / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["mode"] == "NOT_DUE" and hb["ok"] is True


def test_if_due_reports_missed_month_not_silent_skip(fwd_env, monkeypatch):
    """台账不存在 + 已超补跑窗口 ⇒ `MISSED`（rc=8），**不是静默跳过**。

    这条是本轮修出来的语义缺口：日更链下"距月末 11 天且没有台账"有两种解释——
    ① 这期早就跑过了（正常）② 真的漏了（证据链缺一期）。只看 gap 分不开，
    必须**先看台账在不在**。分不开的后果是漏跑被当成正常，一个月一个月地缺下去。
    """
    monkeypatch.setattr(pf, "data_last_date", lambda **k: "20261110")
    monkeypatch.setattr(pf, "_today_str", lambda: "20261110")
    assert _run(monkeypatch, "--if-due") == 8
    hb = json.loads((fwd_env / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["mode"] == "MISSED" and hb["ok"] is False
    assert not (fwd_env / "ledger" / "20261030.json").exists(), "漏跑不得补写台账"


def test_if_due_runs_inside_late_window(fwd_env, monkeypatch):
    """距月末 1 天、台账不存在 ⇒ 应当真的跑，并落 LATE_RUN 台账。"""
    monkeypatch.setattr(pf, "data_last_date", lambda **k: "20261030")
    monkeypatch.setattr(pf, "_today_str", lambda: "20261031")
    assert _run(monkeypatch, "--if-due") == 0
    rec = json.loads((fwd_env / "ledger" / "20261030.json").read_text(encoding="utf-8"))
    assert rec["date"] == "20261030", "台账的决策日字段名是 date"
    assert rec["mode"] == "LATE_RUN"
    assert rec["counts_toward_observation"] is True


def test_if_due_aborts_when_data_does_not_cover_month_end(fwd_env, monkeypatch):
    """数据没盖住月末 ⇒ 不是"跳过"，而是**真问题**，必须非零退出（触发告警）。"""
    monkeypatch.setattr(pf, "data_last_date", lambda **k: "20261029")
    monkeypatch.setattr(pf, "_today_str", lambda: "20261031")
    assert _run(monkeypatch, "--if-due") == 3
    hb = json.loads((fwd_env / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["mode"] == "ABORT_STALE" and hb["ok"] is False


# ---------------------------------------------------------------- 月末当天 = 唯一 FORWARD 窗口

def test_today_is_month_end_with_data_is_gap_zero(real_cal):
    """月末当天收盘后跑 ⇒ 决策日 = 今天，gap=0。

    **这是唯一能产生真正 FORWARD 记录的窗口**，也是长假前那几个月末
    （如 2026-09-30，次一交易日是 10-09）唯一可行的窗口。
    """
    d, why, err = pf.resolve_decision_day("20261030", real_cal, "20261030")
    assert err is None and d == "20261030"
    assert "今日" in why


def test_today_is_month_end_without_data_refuses(real_cal):
    """月末当天但数据还没更新（只到 10-29）⇒ 拒绝，且**不退回上个月**。

    退回上个月会拿一个已有台账的月份反复重算，错误信息也会指向错的月份。
    """
    d, why, err = pf.resolve_decision_day("20261030", real_cal, "20261029")
    assert d is None
    assert isinstance(err, pf.MonthEndUnverified)
    assert "20261030" in str(err)


