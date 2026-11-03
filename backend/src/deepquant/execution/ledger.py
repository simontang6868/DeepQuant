"""执行台账：只追加、带哈希链、成本分项强制来自 ``compute_fees``。

三件事这个模块必须做到，且都是可验证的
------------------------------------
1. **只追加**（2.6-c）。不是"我承诺不改"，而是**物理上**只以 ``"a"`` 模式打开，
   并且每行带 ``prev_sha256`` 形成哈希链。历史行被改一个字，链就断，
   ``verify_chain()`` 立刻失败。项目里出现过归档件被重跑脚本改掉时间戳
   （L3af D8），所以"只追加"必须是可检测的，不能是约定。
2. **成本只能来自 compute_fees**（2.6-b）。做法不是"别手写费率"这句劝告，而是：
   ``FillCost.from_fill`` 用 ``fill.fees``（由券商/Mock 经 ``compute_fees`` 算出）
   填分项，然后**断言三项之和等于** ``fill.fees.total``。任何自己另算一份的路径
   都会在这里对不上而抛。本项目在手写费率上错过四次，光靠注释拦不住。
3. **决策快照带数据哈希**（2.6-a）。``data_sha256`` 与 ``config_sha256`` 必填且
   格式校验：没有哈希，事后无法证明"这一笔是用哪份数据算出来的"，
   整套台账就退化成日记。

⚠️ 一个刻意的设计：台账**不**参与任何计算
--------------------------------------
它只记录发生过什么，不推导该做什么。把"账户权益"这类派生量塞进台账，
下次就会有人从台账读它当输入——于是台账从"记录"变成"账本"，而一个
只能追加、不能更正的账本当输入源，错了就永远改不回来。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from deepquant.broker.models import Fill

__all__ = [
    "LedgerError",
    "LedgerChainBroken",
    "DecisionSnapshot",
    "FillCost",
    "LedgerEntry",
    "Ledger",
    "MODE_PAPER",
    "MODE_SHADOW",
    "MODE_LIVE",
]

MODE_PAPER = "PAPER"
MODE_SHADOW = "SHADOW"
MODE_LIVE = "LIVE"
_VALID_MODES = frozenset({MODE_PAPER, MODE_SHADOW, MODE_LIVE})

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class LedgerError(RuntimeError):
    """台账不可用（格式错、写失败、被篡改）。"""


class LedgerChainBroken(LedgerError):
    """哈希链断裂 —— 历史行被改过。"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(payload: Any) -> str:
    """规范化 JSON：键排序、无空格、ASCII 转义关闭。

    规范化是哈希链的前提：同一份内容必须序列化成同一个字符串，
    否则 ``sort_keys`` 顺序一变，链就莫名其妙断了。
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


# --------------------------------------------------------------------------- 决策快照


@dataclass(frozen=True)
class DecisionSnapshot:
    """一次决策的完整输入证据（2.6-a）。

    ``data_sha256``/``config_sha256`` 必填：没有它们，事后无法回答
    "这一笔是用哪份数据、哪份配置算出来的"。这两个字段一旦缺失，
    台账就只是日记。
    """

    decision_date: int
    mode: str
    data_sha256: str
    config_sha256: str
    targets: tuple[str, ...]
    #: ``(code, 信号值)``。信号值是 f3 之类的原始分数，不是排名。
    signals: tuple[tuple[str, float], ...] = ()
    pool_size: int = 0
    note: str = ""

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"mode 必须是 {sorted(_VALID_MODES)}，收到 {self.mode!r}")
        for name in ("data_sha256", "config_sha256"):
            v = getattr(self, name)
            if not _HEX64.match(v):
                raise ValueError(
                    f"{name} 必须是 64 位小写十六进制 sha256，收到 {v!r}——"
                    "决策快照没有数据哈希就等于没有证据")
        if self.pool_size < 0:
            raise ValueError("pool_size 不得为负")

    def to_payload(self) -> dict[str, Any]:
        return {
            "decision_date": self.decision_date,
            "mode": self.mode,
            "data_sha256": self.data_sha256,
            "config_sha256": self.config_sha256,
            "targets": list(self.targets),
            "signals": [[c, v] for c, v in self.signals],
            "pool_size": self.pool_size,
            "note": self.note,
        }


# --------------------------------------------------------------------------- 成本分项


@dataclass(frozen=True)
class FillCost:
    """一笔成交的成本四分项。**佣金/印花/过户来自 compute_fees，滑点单列。**

    ⚠️ 滑点**不是费率**：它是"成交价与参考价的差 × 股数"，属于执行结果，
    不属于交易所/券商收费。把它混进 ``fees.total`` 会让"成本偏高"无法归因——
    到底是费率档不对，还是下单方式不好，两件事的处理完全不同。
    """

    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage: float
    reference_price: float

    @property
    def fees_total(self) -> float:
        """三项之和。不含滑点。"""
        return self.commission + self.stamp_duty + self.transfer_fee

    @classmethod
    def from_fill(cls, fill: Fill, *, reference_price: float) -> "FillCost":
        """从成交回报构造。**唯一**的成本来源是 ``fill.fees``。

        ⭐ 这里断言 ``三项之和 == fill.fees.total``（精确到分）。这不是多余的
        自检：它的作用是让"在别处手写费率"的实现在第一笔成交上就炸，
        而不是等到三个月后对不上账才发现。
        """
        f = fill.fees
        got = cls(commission=f.commission, stamp_duty=f.stamp_duty,
                  transfer_fee=f.transfer_fee,
                  slippage=round((fill.price - reference_price) * fill.shares, 2),
                  reference_price=reference_price)
        a = int(round(got.fees_total * 100))
        b = int(round(f.total * 100))
        if a != b:
            raise LedgerError(
                f"成本分项之和 {got.fees_total} 与 compute_fees 的 total "
                f"{f.total} 对不上（{fill.fill_id}）——有人在这条路径上手写了费率")
        return got

    def to_payload(self) -> dict[str, Any]:
        return {
            "commission": self.commission,
            "stamp_duty": self.stamp_duty,
            "transfer_fee": self.transfer_fee,
            "slippage": self.slippage,
            "reference_price": self.reference_price,
            "fees_total": round(self.fees_total, 2),
        }


# --------------------------------------------------------------------------- 条目


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    kind: str
    ts: int
    payload: Mapping[str, Any]
    prev_sha256: str
    entry_sha256: str

    def to_line(self) -> str:
        return _canon({
            "seq": self.seq, "kind": self.kind, "ts": self.ts,
            "payload": dict(self.payload),
            "prev_sha256": self.prev_sha256,
            "entry_sha256": self.entry_sha256,
        })

    @staticmethod
    def compute_sha(seq: int, kind: str, ts: int, payload: Mapping[str, Any],
                    prev_sha256: str) -> str:
        return _sha(_canon({"seq": seq, "kind": kind, "ts": ts,
                            "payload": dict(payload),
                            "prev_sha256": prev_sha256}))


# --------------------------------------------------------------------------- 台账


class Ledger:
    """JSONL 追加式台账。

    ``path`` 可以不存在，首次 ``append`` 时创建。每行一个 JSON 对象。
    """

    KIND_DECISION = "decision"
    KIND_ORDER = "order"
    KIND_FILL = "fill"
    KIND_CORRECTION = "correction"
    KIND_RECONCILE = "reconcile"

    def __init__(self, path: str | pathlib.Path) -> None:
        self._path = pathlib.Path(path)

    # ---------------------------------------------------------------- 只读
    @property
    def path(self) -> pathlib.Path:
        return self._path

    def entries(self) -> tuple[dict[str, Any], ...]:
        if not self._path.exists():
            return ()
        out: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8", newline="") as fh:
            for i, line in enumerate(fh, 1):
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise LedgerError(f"{self._path} 第 {i} 行不是合法 JSON：{exc}") from exc
        return tuple(out)

    def verify_chain(self) -> None:
        """校验前缀哈希与每行自哈希。断链即抛。"""
        expect = ""
        for e in self.entries():
            for k in ("seq", "kind", "ts", "payload", "prev_sha256", "entry_sha256"):
                if k not in e:
                    raise LedgerChainBroken(f"第 {e.get('seq')} 条缺字段 {k}")
            if e["prev_sha256"] != expect:
                raise LedgerChainBroken(
                    f"第 {e['seq']} 条的 prev_sha256 与上一条不符："
                    f"{e['prev_sha256'][:8]} != {expect[:8]}——历史行被改过")
            real = LedgerEntry.compute_sha(e["seq"], e["kind"], e["ts"],
                                           e["payload"], e["prev_sha256"])
            if real != e["entry_sha256"]:
                raise LedgerChainBroken(
                    f"第 {e['seq']} 条内容与自哈希不符——该行被改过")
            expect = real

    def last_sha(self) -> str:
        es = self.entries()
        return es[-1]["entry_sha256"] if es else ""

    def next_seq(self) -> int:
        es = self.entries()
        return (es[-1]["seq"] + 1) if es else 1

    # ---------------------------------------------------------------- 写
    def append(self, kind: str, payload: Mapping[str, Any], *, ts: int = 0,
               fsync: bool = True) -> LedgerEntry:
        """追加一条。**只以 ``"a"`` 模式打开**——物理上没有覆写路径。

        ⚠️ ``prev_sha256`` 从**文件**读，不从内存读。从内存读的话，进程重启后
        会从空链继续，于是"链断裂"这个信号永远不会出现。
        """
        if not kind:
            raise LedgerError("kind 不得为空")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        prev = self.last_sha()
        seq = self.next_seq()
        sha = LedgerEntry.compute_sha(seq, kind, ts, payload, prev)
        entry = LedgerEntry(seq=seq, kind=kind, ts=ts, payload=dict(payload),
                            prev_sha256=prev, entry_sha256=sha)
        with self._path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(entry.to_line() + "\n")
            fh.flush()
            if fsync:
                import os
                os.fsync(fh.fileno())
        return entry

    # ---- 语义化封装（都只是 append 的壳，不做计算） ----
    def record_decision(self, snap: DecisionSnapshot, *, ts: int = 0) -> LedgerEntry:
        return self.append(self.KIND_DECISION, snap.to_payload(), ts=ts)

    def record_order(self, order, *, ts: int = 0) -> LedgerEntry:
        return self.append(self.KIND_ORDER, {
            "client_order_id": order.client_order_id,
            "broker_order_id": order.broker_order_id,
            "code": order.code,
            "side": order.side.value,
            "shares": order.shares,
            "limit_price": order.limit_price,
            "status": order.status.value,
            "filled_shares": order.filled_shares,
            "reject_code": order.reject_code,
        }, ts=ts)

    def record_fill(self, fill: Fill, *, reference_price: float,
                    ts: int = 0) -> LedgerEntry:
        cost = FillCost.from_fill(fill, reference_price=reference_price)
        return self.append(self.KIND_FILL, {
            "fill_id": fill.fill_id,
            "client_order_id": fill.client_order_id,
            "code": fill.code,
            "side": fill.side.value,
            "shares": fill.shares,
            "price": fill.price,
            "cost": cost.to_payload(),
        }, ts=ts)

    def record_correction(self, *, target_seq: int, reason: str,
                          corrected: Mapping[str, Any], ts: int = 0) -> LedgerEntry:
        """更正**另立条目**，绝不改历史行（2.6-c）。

        ⚠️ ``target_seq`` 必须真的存在。允许指向不存在的行，就等于允许在台账里
        写无源之词——那种更正比不改更糟。
        """
        if not reason:
            raise LedgerError("更正必须写理由")
        seqs = {e["seq"] for e in self.entries()}
        if target_seq not in seqs:
            raise LedgerError(f"target_seq={target_seq} 不存在，无法更正")
        return self.append(self.KIND_CORRECTION, {
            "target_seq": target_seq,
            "reason": reason,
            "corrected": dict(corrected),
        }, ts=ts)

    def record_reconcile(self, result, *, ts: int = 0) -> LedgerEntry:
        return self.append(self.KIND_RECONCILE, {
            "ok": result.ok,
            "n_diff": len(result.diffs),
            "diffs": [{"object": d.object, "kind": d.kind.value, "code": d.code,
                       "field": d.field, "local": d.local, "broker": d.broker,
                       "delta": d.delta, "detail": d.detail}
                      for d in result.diffs],
            "duplicate_fill_ids": list(result.duplicate_fill_ids),
        }, ts=ts)
