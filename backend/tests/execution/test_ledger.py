"""Phase 2.6：执行台账的行为测试。

对应验收项 `docs/PHASE2_ACCEPTANCE_CRITERIA_2026-11-02.md` §3 的 2.6-a ~ 2.6-c。

三条被"做成可执行"的纪律
----------------------
1. **只追加**（2.6-c）：不是断言"我没写覆写代码"，而是**字节级**比较
   ——写入更正后，文件前 N 个字节必须与写入前完全相同。任何 `"w"` 模式的
   实现都会在这条上红。
2. **成本只能来自 compute_fees**（2.6-b）：两道检查。运行时
   （``FillCost.from_fill`` 断言三项之和 == fees.total）+ 静态
   （源码里不得出现费率常数）。只有运行时那道不够：它只在"有人真的走这条路径"
   时触发，静态那道在这一行代码被写下的当下就红。
3. **决策快照带哈希**（2.6-a）：SHA 格式校验。没有哈希的台账是日记。
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import compute_fees
from deepquant.broker.models import Fill
from deepquant.execution.ledger import (
    DecisionSnapshot,
    FillCost,
    Ledger,
    LedgerChainBroken,
    LedgerError,
    MODE_LIVE,
    MODE_PAPER,
    MODE_SHADOW,
)
from deepquant.market.rules import Side

SRC = (pathlib.Path(__file__).resolve().parents[2]
       / "src" / "deepquant" / "execution" / "ledger.py")

DAY = 20261030
H1 = "a" * 64
H2 = "b" * 64


def _snap(**kw) -> DecisionSnapshot:
    base = dict(decision_date=DAY, mode=MODE_SHADOW, data_sha256=H1,
                config_sha256=H2, targets=("600990", "603977"),
                signals=(("600990", 0.0123), ("603977", 0.0456)), pool_size=800)
    base.update(kw)
    return DecisionSnapshot(**base)


def _cfg() -> BacktestConfig:
    return BacktestConfig(symbol="600990", initial_cash=1_000_000.0)


def _fill(*, shares=200, price=10.0, side=Side.BUY, fid="f-1") -> Fill:
    """真实成交：费用由 ``compute_fees`` 算出，**不是我手写的**。"""
    return Fill(fill_id=fid, client_order_id="c-1", code="600990", side=side,
                shares=shares, price=price, ts=1,
                fees=compute_fees(side, price, shares, _cfg()))


# --------------------------------------------------------------------------- 2.6-a 决策快照


def test_decision_snapshot_requires_data_hash():
    """没有数据哈希 ⇒ 事后无法证明"这一笔用哪份数据算的"。"""
    with pytest.raises(ValueError, match="data_sha256"):
        _snap(data_sha256="")


def test_decision_snapshot_rejects_malformed_hash():
    with pytest.raises(ValueError, match="64 位"):
        _snap(data_sha256="abc123")
    with pytest.raises(ValueError, match="64 位"):
        _snap(config_sha256=H2.upper()), "大写 hex 也要拒——大小写不同就是两个串"


def test_decision_snapshot_rejects_unknown_mode():
    with pytest.raises(ValueError, match="mode"):
        _snap(mode="BACKTEST_FEEL")


def test_paper_and_shadow_and_live_are_all_accepted():
    for m in (MODE_PAPER, MODE_SHADOW, MODE_LIVE):
        assert _snap(mode=m).mode == m


def test_decision_ledger_roundtrip_keeps_signals_and_targets(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.record_decision(_snap(), ts=DAY)
    e = led.entries()[0]
    assert e["kind"] == "decision"
    p = e["payload"]
    assert p["data_sha256"] == H1
    assert p["config_sha256"] == H2
    assert p["targets"] == ["600990", "603977"]
    assert p["signals"] == [["600990", 0.0123], ["603977", 0.0456]]
    assert p["mode"] == MODE_SHADOW


# --------------------------------------------------------------------------- 2.6-b 成本来源


def test_fill_cost_comes_from_compute_fees_and_matches_total():
    f = _fill()
    c = FillCost.from_fill(f, reference_price=10.0)
    assert c.commission == f.fees.commission
    assert c.stamp_duty == f.fees.stamp_duty
    assert c.transfer_fee == f.fees.transfer_fee
    assert c.fees_total == pytest.approx(f.fees.total, abs=1e-9)


def test_buy_has_no_stamp_duty_but_sell_does():
    """印花税只卖出收——这是 compute_fees 的规则，台账只负责如实记。"""
    buy = FillCost.from_fill(_fill(side=Side.BUY), reference_price=10.0)
    sell = FillCost.from_fill(_fill(side=Side.SELL), reference_price=10.0)
    assert buy.stamp_duty == 0.0
    assert sell.stamp_duty > 0.0


def test_handwritten_fee_that_disagrees_with_compute_fees_is_rejected():
    """⭐ 2.6-b 的运行时证据：分项之和与 ``fees.total`` 不符 ⇒ 直接抛。

    构造方式：假装有人在别处手写了一份费率，算出 total 与 compute_fees 不同。
    这条测试的作用范围不止这一次——任何绕过 ``compute_fees`` 的路径都会炸。
    """
    good = _fill()
    broken = Fill(fill_id="f-bad", client_order_id="c-1", code="600990",
                  side=Side.BUY, shares=good.shares, price=good.price, ts=1,
                  fees=type(good.fees)(commission=99.0, stamp_duty=0.0,
                                       transfer_fee=0.0))
    # 分项之和(99.00) 与 total(99.00) 一致，所以上面这条还不会炸——
    # 真正会炸的是"分项被篡改但 total 没跟着改"。
    broken2 = Fill(fill_id="f-bad2", client_order_id="c-1", code="600990",
                   side=Side.BUY, shares=good.shares, price=good.price, ts=1,
                   fees=_PatchedFees(commission=1.23, stamp_duty=0.0,
                                     transfer_fee=0.0, claimed_total=99.0))
    with pytest.raises(LedgerError, match="手写了费率"):
        FillCost.from_fill(broken2, reference_price=10.0)
    # 而"分项与 total 自洽"的（哪怕数值很怪）不该被误杀
    assert FillCost.from_fill(broken, reference_price=10.0).fees_total == 99.0


class _PatchedFees:
    """分项之和 ≠ total 的费用对象——模拟"另算了一份费率"。"""

    def __init__(self, *, commission, stamp_duty, transfer_fee, claimed_total):
        self.commission = commission
        self.stamp_duty = stamp_duty
        self.transfer_fee = transfer_fee
        self._total = claimed_total

    @property
    def total(self):
        return self._total


def test_slippage_is_separate_from_fees():
    """滑点属于执行结果，不属于收费。混在一起就无法归因"成本为什么高"。"""
    f = _fill(price=10.05)
    c = FillCost.from_fill(f, reference_price=10.00)
    assert c.slippage == pytest.approx(10.0)        # (10.05-10.00) × 200
    assert c.fees_total == pytest.approx(f.fees.total, abs=1e-9)
    assert c.slippage != 0.0
    p = c.to_payload()
    assert set(p) == {"commission", "stamp_duty", "transfer_fee", "slippage",
                      "reference_price", "fees_total"}


def _code_ast(src: str):
    """去掉 docstring 后的 AST。注释本来就不进 AST。"""
    import ast

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return tree


def _called_names(src: str) -> set[str]:
    import ast

    out: set[str] = set()
    for node in ast.walk(_code_ast(src)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _numeric_literals(src: str) -> list[float]:
    import ast

    return [n.value for n in ast.walk(_code_ast(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)]


def test_ledger_does_not_compute_fees_itself():
    """⭐ 2.6-b 的静态证据：台账**不调用** ``compute_fees``，也不导入 costs。

    ⚠️ 用 AST 扫**调用与导入**，不是扫字符串。第一版写成 ``"compute_fees" in src``
    时，被 `FillCost.from_fill` 里那句错误消息（"...与 compute_fees 的 total 对不上"）
    误报——这正是本项目反复吃的「检查器分不清提到与使用」（2.1-b 同一个坑）。
    """
    import ast

    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "costs" in mod.split("."):
                imported.append(f"from {mod} import ...")
            imported += [a.name for a in node.names if a.name == "compute_fees"]
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names
                         if "costs" in a.name.split(".")]

    assert imported == [], (
        f"台账开始自己取成本计算能力了：{imported}——它应当只接收已算好的 Fill.fees")
    assert "compute_fees" not in _called_names(src), (
        "台账调用了 compute_fees：它参与计算后，'成本是谁算的'重新变得模糊")


def test_ledger_source_contains_no_fee_rate_constants():
    """静态第二道：源码里不得出现费率量级的数字字面量。

    运行时那道（``from_fill`` 的断言）只在有人真走这条路径时触发；
    静态这道在这行代码被写下的当下就红。
    """
    lits = _numeric_literals(SRC.read_text(encoding="utf-8"))
    # 费率都在 1e-5 ~ 1e-3 量级；台账里合法的数字只有 0/1/2/100 之类。
    suspects = [x for x in lits
                if isinstance(x, float) and 0 < abs(x) < 1e-2 and abs(x) != 0.5]
    assert suspects == [], (
        f"ledger.py 里出现疑似费率的小数 {suspects}——成本必须来自 compute_fees（2.6-b）")


def test_the_static_scanners_actually_catch_a_real_violation():
    """⭐ 检查器自己也要被检查：故意塞一段违规代码，确认会被抓到。

    没有这条，上面两个扫描可能因为写错（比如 AST 遍历漏了某个节点类型）
    而永远返回空——那时它们绿得毫无意义。
    """
    bad_rates = "X = 0.00025\n"
    assert [x for x in _numeric_literals(bad_rates)
            if isinstance(x, float) and 0 < abs(x) < 1e-2], "费率扫描器抓不到 0.00025"

    bad_call = "from deepquant.backtest.costs import compute_fees\ny = compute_fees(1,2,3,4)"
    names = _called_names(bad_call)
    assert "compute_fees" in names, "调用扫描器抓不到 compute_fees"


def test_scanner_ignores_the_same_words_in_prose():
    """反例：错误消息里提到 ``compute_fees`` 不算违规（这正是第一版的误报）。"""
    prose = 'def f():\n    raise ValueError("与 compute_fees 的 total 对不上")\n'
    assert "compute_fees" not in _called_names(prose)


# --------------------------------------------------------------------------- 2.6-c 只追加


def test_appending_does_not_change_any_existing_byte(tmp_path):
    """⭐⭐ 2.6-c 的字节级证明。

    写入第二条、第三条之后，文件前 N 个字节必须与写入前**完全相同**。
    ``"w"`` 模式的实现、或"读全文再整体重写"的实现，都会在这条上红。
    """
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append("decision", {"x": 1}, ts=1)
    before = led.path.read_bytes()

    led.append("order", {"y": 2}, ts=2)
    after = led.path.read_bytes()

    assert after[:len(before)] == before, "追加时改动了已有内容"
    assert len(after) > len(before)
    # 只多了"一行"
    assert after[len(before):].count(b"\n") == 1


def test_correction_is_a_new_entry_not_an_edit(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    e1 = led.append("fill", {"shares": 200}, ts=1)
    before = led.path.read_bytes()

    led.record_correction(target_seq=e1.seq, reason="股数录错，应为 100",
                          corrected={"shares": 100}, ts=2)
    after = led.path.read_bytes()

    assert after[:len(before)] == before, "更正改动了历史行"
    entries = led.entries()
    assert entries[0]["payload"]["shares"] == 200, "历史行被更正覆盖了"
    assert entries[1]["kind"] == "correction"
    assert entries[1]["payload"]["target_seq"] == e1.seq
    assert "录错" in entries[1]["payload"]["reason"]


def test_correction_must_target_an_existing_entry(tmp_path):
    """允许指向不存在的行 = 允许在台账里写无源之词。"""
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append("fill", {"shares": 200}, ts=1)
    with pytest.raises(LedgerError, match="不存在"):
        led.record_correction(target_seq=99, reason="x", corrected={})


def test_correction_requires_reason(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    e = led.append("fill", {"shares": 200}, ts=1)
    with pytest.raises(LedgerError, match="理由"):
        led.record_correction(target_seq=e.seq, reason="", corrected={})


# --------------------------------------------------------------------------- 哈希链


def test_chain_is_valid_after_normal_appends(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    for i in range(5):
        led.append("fill", {"i": i}, ts=i)
    led.verify_chain()
    assert led.last_sha() != ""


def test_chain_detects_edited_history_line(tmp_path):
    """把中间一行的内容改掉、**不改**它的自哈希 ⇒ 自哈希校验失败。"""
    led = Ledger(tmp_path / "ledger.jsonl")
    for i in range(3):
        led.append("fill", {"shares": 100 * (i + 1)}, ts=i)

    lines = led.path.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[1])
    obj["payload"]["shares"] = 999            # 偷偷改股数
    lines[1] = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    led.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(LedgerChainBroken, match="自哈希"):
        led.verify_chain()


def test_chain_detects_edited_line_with_recomputed_self_hash(tmp_path):
    """⭐ 改内容 + **重算自哈希**（更勤奋的篡改）⇒ 由前缀链抓住。

    只校验"每行自洽"是不够的：篡改者可以顺手把自哈希也算了。
    必须同时校验 ``prev_sha256`` 与上一行的 ``entry_sha256`` 相连。
    """
    from deepquant.execution.ledger import LedgerEntry

    led = Ledger(tmp_path / "ledger.jsonl")
    for i in range(3):
        led.append("fill", {"shares": 100 * (i + 1)}, ts=i)

    lines = led.path.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[1])
    obj["payload"]["shares"] = 999
    obj["entry_sha256"] = LedgerEntry.compute_sha(
        obj["seq"], obj["kind"], obj["ts"], obj["payload"], obj["prev_sha256"])
    lines[1] = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    led.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(LedgerChainBroken, match="prev_sha256"):
        led.verify_chain()


def test_chain_detects_deleted_middle_line(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    for i in range(4):
        led.append("fill", {"i": i}, ts=i)
    lines = led.path.read_text(encoding="utf-8").splitlines()
    led.path.write_text("\n".join(lines[:1] + lines[2:]) + "\n",
                        encoding="utf-8", newline="\n")
    with pytest.raises(LedgerChainBroken):
        led.verify_chain()


def test_prev_hash_is_read_from_file_not_memory(tmp_path):
    """⭐ 重启后必须接着文件里的链，不能从空链继续。

    从内存读 ``prev_sha256`` 的实现，重启后会写一条 ``prev=""`` 的行，
    于是"断链"这个信号永远不出现——而它恰恰是唯一能发现篡改的信号。
    """
    p = tmp_path / "ledger.jsonl"
    led1 = Ledger(p)
    led1.append("fill", {"i": 1}, ts=1)
    last = led1.last_sha()

    led2 = Ledger(p)                 # 模拟进程重启
    e = led2.append("fill", {"i": 2}, ts=2)
    assert e.prev_sha256 == last, "重启后没有接上原来的链"
    led2.verify_chain()


def test_seq_is_monotonic_across_restart(tmp_path):
    p = tmp_path / "ledger.jsonl"
    Ledger(p).append("fill", {}, ts=1)
    Ledger(p).append("fill", {}, ts=2)
    seqs = [e["seq"] for e in Ledger(p).entries()]
    assert seqs == [1, 2]


# --------------------------------------------------------------------------- 其它


def test_fill_entry_separates_four_cost_items(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.record_fill(_fill(side=Side.SELL), reference_price=9.98, ts=DAY)
    cost = led.entries()[0]["payload"]["cost"]
    for k in ("commission", "stamp_duty", "transfer_fee", "slippage"):
        assert k in cost, f"成本分项 {k} 必须独立记录（2.6-a）"
    assert cost["slippage"] == pytest.approx((10.0 - 9.98) * 200, abs=0.01)


def test_order_entry_records_reject_code(tmp_path):
    from deepquant.broker.models import BrokerOrder, BrokerOrderStatus

    led = Ledger(tmp_path / "ledger.jsonl")
    o = BrokerOrder(client_order_id="c-1", broker_order_id=None, code="600990",
                    side=Side.BUY, shares=200, limit_price=10.0,
                    status=BrokerOrderStatus.REJECTED, reject_code="limit_up",
                    updated_ts=1)
    led.record_order(o, ts=DAY)
    p = led.entries()[0]["payload"]
    assert p["reject_code"] == "limit_up"
    assert p["status"] == "rejected"


def test_reconcile_entry_records_every_difference(tmp_path):
    from deepquant.broker.models import Cash, Position
    from deepquant.execution.reconciler import reconcile

    r = reconcile(local_positions=[Position("600990", 1000, 1000)],
                  broker_positions=[Position("600990", 800, 800)],
                  local_cash=Cash(100.0, 0.0, 100.0),
                  broker_cash=Cash(100.0, 0.0, 100.0), ts=DAY)
    led = Ledger(tmp_path / "ledger.jsonl")
    led.record_reconcile(r, ts=DAY)
    p = led.entries()[0]["payload"]
    assert p["ok"] is False
    # quantity 与 available 同时差 200 ⇒ 两条。只报"不一致"的实现会合并成一条。
    assert p["n_diff"] == 2
    fields = sorted(d["field"] for d in p["diffs"])
    assert fields == ["available", "quantity"]
    assert {d["delta"] for d in p["diffs"]} == {-200}


def test_empty_ledger_has_valid_chain(tmp_path):
    led = Ledger(tmp_path / "nope.jsonl")
    assert led.entries() == ()
    led.verify_chain()
    assert led.last_sha() == ""
    assert led.next_seq() == 1
