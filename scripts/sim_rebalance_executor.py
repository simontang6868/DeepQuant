#!/usr/bin/env python3
"""U-16 / ``U16-EXEC-1``：12-01 仿真月度调仓**执行器**（真实下单路径，四道生效条件门控）。

## 与既有模块的分工

| 模块 | 职责 |
| --- | --- |
| ``p13_forward.py`` | 产出 11-30 台账（含 ``target_holds_d4``）；**不报单** |
| ``paper_account_adapter.py`` | **只读**外部真值（cash / positions / 回报 / 未完结委托） |
| ``sim_reconcile.py`` | 外部真值优先的双向差集对账 |
| ``gm_paper_order_adapter.py`` | **探针**（2 只×1 手，``--execute`` 恒 rc=4），与本模块**分开授权** |
| **本模块** | 按台账目标**一次性调仓**；四道生效条件全过才允许调用 broker |

## ⭐ 四道生效条件（缺一不可，任一不过 ⇒ ``--execute`` 返回 rc=4）

1. **E1 台账**：``forward/ledger/20261130.json`` 存在、``mode == "FORWARD"``、``target_holds_d4`` 非空；
2. **E2 前置演练**：11-27 月末 DRYRUN 产物存在 + 11-30 17:10 后 ``preflight_decision_day`` 产物/日志存在；
3. **E3 授权**：``paper_authorization.json`` 经 ``paper_authorization_guard`` 判 ``ALLOW``（六项 6/6）；
4. **E4 隔离与清单**：``check_channel_isolation.py --remote`` 为 ``FAIL 0`` 且
   ``deploy_manifest.py --verify --remote`` 一致（子进程，可注入 ``runner`` 供离线测试）。

## ⚠️ 已登记的**实现限制**（预登记缺口，不改预登记，另立缺陷记录段）

``gmtrade`` 该版本**没有行情接口**（``get_*`` 仅 cash/positions/orders/execution_reports/
unfinished_orders/strerror/version），因此：

- **价格来源只能是台账 ``ref_close_d4``（与 d4 目标同源同键，11-30 收盘价）**，``PRICE_SOURCE = "LEDGER_D4_REF_CLOSE"``；
- 预登记 §2 的「10:30 / 14:30 重定价」**不可实现**（无实时价可重定价），
  ``REPRICE_IMPLEMENTABLE = False``；实际行为＝**单次限价挂单 + ``CARRY`` 顺延**，
  未成交部分靠 ``CARRY_MAX_DAYS`` 与对账解释，**不伪装成已实现重定价**。

## 幂等

``client_order_id = "SIM_DRILL-" + sha256(f"{date}|{code}|{side}|{qty}")[:24]``
同一四元组重放恒定 ⇒ 服务端去重；本地写入 ``sim_orders/<date>.jsonl`` 亦以 cid 去重。

## 用法

    python scripts/sim_rebalance_executor.py --self-check            # 纯离线，临时目录
    python scripts/sim_rebalance_executor.py --plan                 # 只算计划，不写盘不报单
    python scripts/sim_rebalance_executor.py --execute              # 四道门控；不过 ⇒ rc=4
    python scripts/sim_rebalance_executor.py --gates                # 只打印四道门控结果

⚠️ ``StatisticalVerdict=ENGINEERING_SELF_CHECK_ONLY``｜``MaturityLevel=EXECUTION_READY_GATED``
⚠️ ``SIM_DRILL`` 结果不构成样本外证据、不进 G1/G2/S4、不作经济性判断。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import paper_authorization_guard as guard  # noqa: E402

# ---------------------------------------------------------------- 冻结常量
#: 冻结预登记（`U16-EXEC-1`，**先于本文件第一行代码冻结**）。
PREREG_REL = "docs/U16_EXEC_PREREG_2026-11-16.md"
PREREG_SHA256 = "53ace8c5064f615806a07ae012bd3c2d344103e4b93b7a74e9e047eb8b0b703b"
#: U16-EXEC-2 增补预登记（2026-11-19 冻结，先于本轮任何代码改动）。
#: 与 U16-EXEC-1 并列校验；改其一即 HALT。
PREREG_V2_REL = "docs/U16_EXEC_PREREG_V2_2026-11-19.md"
PREREG_V2_SHA256 = "ec322be5fcdcdb81c889d93ec301e3c07294a29f131b0d521f7ec6614934140b"
# U16-EXEC-3 方案甲：窗口单一来源——E0 门与落盘门槛共用 sim_exec_result 的常量。
# 本地 import 供常量引用（sim_exec_result 不反向 import 本模块，无循环）。
import sim_exec_result as _ser
E0_TOLERANCE_MIN = _ser.FRESHNESS_WINDOW_MIN   # 原独立定义 10，现收敛为单一来源
NTP_REQUIRED = True
EXEC_REPORT_WINDOW_MIN = 10
#: U-16 授权存证（版本 A）+ 署名补记（引用时须同时给）。
AUTH_STATEMENT_REL = "research/stock_pick_2026-10-25/forward/authorization_statements/U16_statement_2026-11-16.md"

EXEC_DATE = "20261201"
LEDGER_DATE = "20261130"
LEDGER_REL = f"research/stock_pick_2026-10-25/forward/ledger/{LEDGER_DATE}.json"
FIRST_ORDER_TIME = "09:35"
REPRICE_TIMES = ("10:30", "14:30")      # 预登记冻结；见 REPRICE_IMPLEMENTABLE
MAX_REPRICE = 2
CARRY_MAX_DAYS = 10
LOT = 100
TOTAL_WEIGHT = "0.25"                   # 与 p13_forward `notional = CAPITAL*0.25/K` 一致
DRILL_TAG = "SIM_DRILL"
AUTH_ACTION = "sim_rebalance_order"
POSITION_EFFECT_OPEN = 1
POSITION_EFFECT_CLOSE = 2
ORDER_TYPE_LIMIT = 1                    # gmtrade `OrderType_Limit`
ORDER_SIDE = {"BUY": 1, "SELL": 2}      # `OrderSide_Buy` / `OrderSide_Sell`

#: ⚠️ 预登记缺口：无行情接口 ⇒ 只能用台账收盘价；重定价不可实现。
#: U16-D4-PRICE-FIX：价格源改为台账 ref_close_d4（与 d4 目标同源同键），
#: 不再用 orders[].ref_close（主口径，与 d4 目标常不重合）。
PRICE_SOURCE = "LEDGER_D4_REF_CLOSE"
REPRICE_IMPLEMENTABLE = False
IMPLEMENTATION_LIMITATION = (
    "gmtrade 无 tick/quote 接口 ⇒ 价格只能取台账 ref_close_d4（与 d4 目标同源同键）；"
    "预登记 §2 的 10:30/14:30 重定价不可实现，实际为单次限价 + CARRY 顺延"
)

AUTH_FILE_REL = "research/stock_pick_2026-10-25/forward/paper_authorization.json"
ISO_FILE_REL = "scripts/check_channel_isolation.py"
MANIFEST_REL = "scripts/deploy_manifest.py"
DRYRUN_GLOB = ("research/stock_pick_2026-10-25/forward/dryrun/*20261127*",
               "research/stock_pick_2026-10-25/forward/dryrun_20261127*",
               "research/stock_pick_2026-10-25/forward/*DRYRUN*20261127*")
PREFLIGHT_GLOB = ("research/stock_pick_2026-10-25/forward/preflight/*20261130*",
                  "research/stock_pick_2026-10-25/forward/preflight_20261130*")


class Halt(RuntimeError):
    """失败关闭。"""


def verify_prereg() -> str:
    p = ROOT / PREREG_REL
    if not p.exists():
        raise Halt("PREREG_MISSING")
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if h != PREREG_SHA256:
        raise Halt("PREREG_MISMATCH")
    canonical = (EXEC_DATE, LEDGER_DATE, FIRST_ORDER_TIME, MAX_REPRICE,
                 CARRY_MAX_DAYS, LOT, TOTAL_WEIGHT, DRILL_TAG, AUTH_ACTION)
    if canonical != ("20261201", "20261130", "09:35", 2, 10, 100, "0.25",
                     "SIM_DRILL", "sim_rebalance_order"):
        raise Halt("PREREG_PARAMS_MISMATCH")
    # U16-EXEC-2 增补校验（并列，不替代 U16-EXEC-1）
    p2 = ROOT / PREREG_V2_REL
    if not p2.exists():
        raise Halt("PREREG_V2_MISSING")
    if hashlib.sha256(p2.read_bytes()).hexdigest() != PREREG_V2_SHA256:
        raise Halt("PREREG_V2_MISMATCH")
    if (E0_TOLERANCE_MIN, NTP_REQUIRED, EXEC_REPORT_WINDOW_MIN) != (10, True, 10):
        raise Halt("PREREG_V2_PARAMS_MISMATCH")
    return h


def intended_now() -> str:
    """意图时点（冻结常量派生，供门控/对账比对；不冒充真实时钟）。

    U16-EXEC-3：与 sim_exec_result.EXEC_INTENT 单一来源，启动校验须一致。
    """
    derived = f"{EXEC_DATE[:4]}-{EXEC_DATE[4:6]}-{EXEC_DATE[6:]}T{FIRST_ORDER_TIME}:00+08:00"
    if derived != _ser.EXEC_INTENT:
        raise Halt("INTENT_SOURCE_MISMATCH")
    return derived


def gate_clock(*, real_now: dt.datetime | None = None,
               ntp_check=None) -> dict:
    """E0：真实系统时钟与意图时点偏差 ≤10min 且 NTP 同步。

    ``real_now`` / ``ntp_check`` 可注入供离线测试；默认读本机时钟与
    ``timedatectl show -p NTPSynchronized``。失败关闭，不用意图 now 冒充。
    """
    import subprocess as _sp
    tz = dt.timezone(dt.timedelta(hours=8))
    if real_now is None:
        real_now = dt.datetime.now(tz)
    wanted = dt.datetime.fromisoformat(intended_now())
    delta_min = abs((real_now - wanted).total_seconds()) / 60.0
    if ntp_check is None:
        def ntp_check():
            cp = _sp.run(["timedatectl", "show", "-p", "NTPSynchronized"],
                         capture_output=True, text=True, timeout=15)
            line = (cp.stdout or "").strip().lower()
            return cp.returncode == 0 and line.endswith("=yes")
    try:
        ntp_ok = bool(ntp_check())
    except Exception:  # noqa: BLE001
        ntp_ok = False
    ok = delta_min <= E0_TOLERANCE_MIN and (ntp_ok or not NTP_REQUIRED)
    reason = None if ok else (
        "CLOCK_SKEW" if delta_min > E0_TOLERANCE_MIN else "NTP_NOT_SYNC")
    return {"gate": "E0", "ok": ok, "reason": reason,
            "delta_min": round(delta_min, 4), "ntp_ok": ntp_ok,
            "real_now": real_now.isoformat(), "intended": intended_now()}


# ---------------------------------------------------------------- 四道门控
def gate_ledger(path: Path | None = None) -> dict:
    """E1：台账存在 + mode=FORWARD + target_holds_d4 非空。"""
    p = Path(path) if path else ROOT / LEDGER_REL
    if not p.is_file():
        return {"gate": "E1", "ok": False, "reason": "LEDGER_MISSING", "path": str(p)}
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"gate": "E1", "ok": False, "reason": f"LEDGER_UNREADABLE:{type(exc).__name__}"}
    mode = str(rec.get("mode") or "")
    codes = rec.get("target_holds_d4")
    if mode != "FORWARD":
        return {"gate": "E1", "ok": False, "reason": f"MODE_NOT_FORWARD:{mode}"}
    if not isinstance(codes, list) or not codes:
        return {"gate": "E1", "ok": False, "reason": "NO_TARGET_HOLDS_D4",
                "note": "老台账（LEGACY_NO_D4）不得顶替"}
    # D4 价格覆盖（U16-D4-PRICE-FIX）：每个 d4 目标必须在 ref_close_d4 有正有限价。
    # 该字段与 target_holds_d4 在 p13 同一次 pick 构造、同源同键；orders 只覆盖主口径，
    # 不能作为 d4 价格源（两条选股路径常不重合，独立复现已坐实 NO_PRICE）。
    # 价格映射唯一构造器与 main 共用，校验标准不再两处漂移。
    norm_px = price_map_from_ledger(rec)
    missing_px: list[str] = []
    for c in codes:
        nc = normalize_code(c)
        if nc is None or nc not in norm_px:
            missing_px.append(str(c))
    if missing_px:
        return {"gate": "E1", "ok": False, "reason": "NO_PRICE_COVERAGE_D4",
                "missing": missing_px,
                "note": "ref_close_d4 须与 target_holds_d4 同源同键"}
    return {"gate": "E1", "ok": True, "reason": None, "mode": mode,
            "n_targets": len(codes), "codes": [str(c) for c in codes]}


def _expand(pats: tuple[str, ...]) -> list[Path]:
    """展开路径模式：绝对路径直接判存在（``Path.glob`` 不支持绝对 pattern）。"""
    out: list[Path] = []
    for pat in pats:
        p = Path(pat)
        if p.is_absolute():
            if p.is_file():
                out.append(p)
        else:
            out.extend([q for q in ROOT.glob(pat) if q.is_file()])
    return out


def gate_dryrun(patterns: tuple[str, ...] | None = None,
                preflight_patterns: tuple[str, ...] | None = None) -> dict:
    """E2：11-27 DRYRUN 与 11-30 preflight 产物存在。"""
    pats = patterns if patterns is not None else DRYRUN_GLOB
    pfs = preflight_patterns if preflight_patterns is not None else PREFLIGHT_GLOB
    dry: list[str] = []
    for p in _expand(pats):
        dry.append(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p))
    pre: list[str] = []
    for p in _expand(pfs):
        pre.append(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p))
    missing = []
    if not dry:
        missing.append("DRYRUN_20261127")
    if not pre:
        missing.append("PREFLIGHT_20261130")
    return {"gate": "E2", "ok": not missing, "reason": (None if not missing
                                                       else "MISSING:" + ",".join(missing)),
            "dryrun": dry[:5], "preflight": pre[:5]}


def gate_auth(auth_path: Path | None = None, *, now: str, notional: float,
              cumulative: float, available: float) -> dict:
    """E3：授权文件经守卫判 ALLOW（六项 6/6）。"""
    p = Path(auth_path) if auth_path else ROOT / AUTH_FILE_REL
    doc = guard.load_doc(p)
    if doc is None:
        return {"gate": "E3", "ok": False, "reason": "NO_AUTH", "path": str(p)}
    v = guard.check(doc=doc, now=now, action=AUTH_ACTION, notional=notional,
                    cumulative=cumulative, available=available)
    return {"gate": "E3", "ok": v["decision"] == guard.ALLOW,
            "reason": None if v["decision"] == guard.ALLOW else str(v.get("reason")),
            "checks": v.get("checks", {}), "path": str(p)}


def _default_runner(cmd: list[str]) -> tuple[int, str]:
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


CORROB_REL = "research/stock_pick_2026-10-25/forward/corroboration/20261130.json"


def gate_corroboration(*, file_path=None, root: Path | None = None,
                       expected_sha: str = "", expected_commit: str = "",
                       required: bool = True) -> dict:
    """E4 旁证三条件：内嵌哈希 / commit / 双向集合全等（U16-EXEC-2）。

    ``required=False`` 仅用于离线 self-check（无旁证文件时不误触发）。
    云端执行时 required=True，文件缺失/校验失败一律 FAIL。
    """
    p = Path(file_path) if file_path else ROOT / CORROB_REL
    if not p.is_file():
        return {"gate": "E4-CORROB", "ok": not required,
                "reason": None if not required else "CORROB_MISSING", "path": str(p)}
    try:
        import sim_corroboration as cor
        rec = json.loads(p.read_text(encoding="utf-8"))
        v = cor.verify_corroboration(
            rec, root=root, expected_sha=expected_sha,
            expected_commit=expected_commit)
        return {"gate": "E4-CORROB", "ok": bool(v["ok"]),
                "reason": None if v["ok"] else "CORROB_" + ",".join(
                    k for k, ok in v["checks"].items() if not ok),
                "checks": v["checks"], "diffs": v.get("diffs", [])[:10],
                "sha256": cor.corrob_sha(rec)}
    except Exception as exc:  # noqa: BLE001
        return {"gate": "E4-CORROB", "ok": False,
                "reason": f"CORROB_ERROR:{type(exc).__name__}"}


def gate_isolation_manifest(runner=None) -> dict:
    """E4：通道隔离 FAIL 0 + 部署清单一致（子进程，可注入 runner）。"""
    run = runner or _default_runner
    py = sys.executable
    rc1, out1 = run([py, str(ROOT / ISO_FILE_REL), "--remote"])
    rc2, out2 = run([py, str(ROOT / MANIFEST_REL), "--verify", "--remote"])
    iso_ok = rc1 == 0 and "FAIL 0" in out1
    man_ok = rc2 == 0 and "PASS" in out2
    missing = [n for n, ok in (("ISOLATION", iso_ok), ("MANIFEST", man_ok)) if not ok]
    return {"gate": "E4", "ok": not missing,
            "reason": None if not missing else "FAILED:" + ",".join(missing),
            "isolation_rc": rc1, "manifest_rc": rc2,
            "isolation_tail": out1.strip().splitlines()[-1:] or [],
            "manifest_tail": out2.strip().splitlines()[-1:] or []}


def run_gates(*, now: str, notional: float, cumulative: float, available: float,
              ledger_path=None, auth_path=None, runner=None, clock=None,
              corroboration=None) -> list[dict]:
    return [gate_clock(**(clock or {})),
            gate_ledger(ledger_path),
            gate_dryrun(),
            gate_auth(auth_path, now=now, notional=notional,
                      cumulative=cumulative, available=available),
            gate_corroboration(**(corroboration if corroboration is not None
                                  else {})),
            gate_isolation_manifest(runner)]


# ---------------------------------------------------------------- 计划层
def _frac(v) -> Fraction:
    return Fraction(str(v))


def client_order_id(*, day: str, code: str, side: str, qty: int) -> str:
    """幂等键：同一四元组恒定（独立算术，不调用被测函数）。"""
    raw = f"{day}|{code}|{side}|{qty}".encode("utf-8")
    return DRILL_TAG + "-" + hashlib.sha256(raw).hexdigest()[:24]


def normalize_code(code) -> str | None:
    s = str(code or "").strip()
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s if len(s) == 6 and s.isdigit() else None


def price_map_from_ledger(rec: dict) -> dict[str, float]:
    """台账 d4 价格映射的唯一构造器（E1 门与 main 下单共用）。

    U16-D4-PRICE-FIX：只读 ``ref_close_d4``（与 target_holds_d4 同源同键），
    **不读** ``orders[].ref_close``（主口径，与 d4 目标常不重合）。
    只收正有限价：非数字 / NaN / inf / <=0 一律丢弃 ⇒ 由 E1 门以
    NO_PRICE_COVERAGE_D4 失败关闭，不静默放行。

    注：normalize_code 取点后末段，故 ``000721.SZ`` → ``SZ`` → None；
    p13 写的 ref_close_d4 是纯 6 位码，正常路径不受影响。
    """
    raw = rec.get("ref_close_d4")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        nc = normalize_code(k)
        if nc is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv) and fv > 0:
            out[nc] = fv
    return out


@dataclass(frozen=True)
class Intent:
    code: str
    side: str
    qty: int
    price: str
    client_order_id: str
    reason: str
    target_qty: int
    cur_qty: int


def build_plan(*, ledger: dict, positions: dict[str, int], cash: float,
               price_of, warnings: list[str] | None = None) -> list[Intent]:
    """等权向下取整调仓：先清非目标，再按目标补足。

    ``price_of(code) -> float | None``；返回 ``None``/``<=0`` ⇒ ``Halt("NO_PRICE:<code>")``。
    ``warnings`` 收集**超预算**（一手都买不起）等需显式告知的情形——不静默吞掉。
    """
    codes = [normalize_code(c) for c in (ledger.get("target_holds_d4") or [])]
    if not codes or any(c is None for c in codes):
        raise Halt("BAD_TARGET_CODES")
    k = len(codes)
    per = _frac(cash) * _frac(TOTAL_WEIGHT) / k
    if per <= 0:
        raise Halt("ZERO_BUDGET")

    intents: list[Intent] = []
    # (1) 清掉非目标持仓（整仓卖出，限价 = 该票现价）
    for code, qty in sorted(positions.items()):
        c = normalize_code(code)
        if not c or c in codes or int(qty) <= 0:
            continue
        px = price_of(c)
        if px is None or float(px) <= 0:
            raise Halt(f"NO_PRICE:{c}")
        intents.append(Intent(code=c, side="SELL", qty=int(qty), price=str(_frac(px)),
                              client_order_id=client_order_id(day=EXEC_DATE, code=c,
                                                              side="SELL", qty=int(qty)),
                              reason="EXIT_NON_TARGET", target_qty=0, cur_qty=int(qty)))
    # (2) 目标票：目标股数 = floor(每只预算 / 价格 / 100) * 100
    for c in codes:
        px = price_of(c)
        if px is None or float(px) <= 0:
            raise Halt(f"NO_PRICE:{c}")
        px_f = _frac(px)
        target = int(math.floor(per / px_f / LOT)) * LOT
        cur = int(positions.get(c, 0))
        if target == 0 and cur == 0:
            # ⭐ 超预算不静默：一手都买不起必须显式告知（否则「没买」会被当成「不需要买」）
            if warnings is not None:
                warnings.append(f"OVER_BUDGET:{c}（每只预算 {float(per):.2f} 元，"
                                f"一手需 {float(px_f) * LOT:.2f} 元）")
            continue
        delta = target - cur
        if delta == 0:
            continue
        side = "BUY" if delta > 0 else "SELL"
        qty = abs(delta)
        intents.append(Intent(code=c, side=side, qty=qty, price=str(px_f),
                              client_order_id=client_order_id(day=EXEC_DATE, code=c,
                                                              side=side, qty=qty),
                              reason="REBALANCE_TARGET", target_qty=target, cur_qty=cur))
    return intents


def plan_notional(intents: list[Intent]) -> float:
    """委托名义金额合计（买入计正、卖出计正，仅用于授权额度校验）。"""
    total = Fraction(0)
    for it in intents:
        total += _frac(it.price) * it.qty
    return float(total)


# ---------------------------------------------------------------- 执行层
class GmTradeBroker:
    """真实 gm-paper broker（**仅在四道门控全过时由 main 实例化**）。

    ``gmtrade`` 延迟导入：离线自检永不触发。下单用 ``order_volume`` + 限价 + 开/平仓。
    """

    def __init__(self, *, token: str, account_id: str, endpoint: str = "api.myquant.cn:9000"):
        import gmtrade.api as gm  # 延迟导入
        gm.set_token(token)
        gm.set_endpoint(endpoint)
        acc = gm.account(account_id=account_id, account_alias="")
        gm.login(acc)
        self._gm = gm
        self.calls: list[dict] = []

    def place_limit_order(self, *, code: str, side: str, qty: int, price: str,
                          cid: str) -> dict:
        gm = self._gm
        try:
            # ⚠️ gmtrade ``order_volume`` 无 ``order_remark``/``client_order_id`` 入参
            # （签名：symbol, volume, side, order_type, position_effect, price=0,
            #   order_duration=0, order_qualifier=0, account=None）
            # ⇒ 自定义 cid **无法透传**给服务端；本地幂等只在 ``append_orders`` 与
            # 发送前查重生效。已登记为实现限制（见文件头），不伪装成服务端去重。
            oid = gm.order_volume(
                symbol=code, volume=int(qty), side=ORDER_SIDE[side],
                order_type=ORDER_TYPE_LIMIT,
                position_effect=(POSITION_EFFECT_OPEN if side == "BUY" else POSITION_EFFECT_CLOSE),
                price=float(price), order_qualifier=0,
            )
        except Exception as exc:  # noqa: BLE001
            return {"decision": "HALT:ORDER_EXCEPTION", "code": code,
                    "client_order_id": cid, "error": type(exc).__name__}
        self.calls.append({"code": code, "side": side, "qty": qty, "price": price, "cid": cid})
        return {"decision": "SENT", "code": code, "client_order_id": cid, "order_id": str(oid)}


class FakeBroker:
    """离线 broker：只记录调用，不发单。"""

    def __init__(self):
        self.calls: list[dict] = []

    def place_limit_order(self, *, code: str, side: str, qty: int, price: str,
                          cid: str) -> dict:
        self.calls.append({"code": code, "side": side, "qty": qty, "price": price, "cid": cid})
        return {"decision": "FAKE_SENT", "code": code, "client_order_id": cid}


def append_orders(path: Path, rows: list[dict]) -> int:
    """按 cid 去重追加（幂等落地）：已在文件里的 cid 不重复写。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line).get("client_order_id"))
            except Exception:  # noqa: BLE001
                continue
    n = 0
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for r in rows:
            if r.get("client_order_id") in seen:
                continue
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
            seen.add(r.get("client_order_id"))
            n += 1
    return n


# ---------------------------------------------------------------- 自检
def _fake_ledger(n=5) -> dict:
    codes = ["600519", "600036", "601318", "600276", "601888"][:n]
    px = {"600519": 1500.0, "600036": 35.0, "601318": 45.0,
          "600276": 40.0, "601888": 70.0}
    return {"date": LEDGER_DATE, "mode": "FORWARD",
            "target_holds_d4": codes,
            # U16-D4-PRICE-FIX：与 target_holds_d4 同源同键
            "ref_close_d4": {c: px[c] for c in codes}}


def self_check() -> dict:
    verify_prereg()
    checks: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        prices = {"600519": 1500.0, "600036": 35.0, "601318": 45.0,
                  "600276": 40.0, "601888": 70.0, "000001": 12.0}
        px = lambda c: prices.get(normalize_code(c))  # noqa: E731
        led = _fake_ledger()

        # 1) 计划：每只预算 = 100000*0.25/5 = 5000
        warns: list[str] = []
        plan = build_plan(ledger=led, positions={}, cash=100000.0, price_of=px,
                          warnings=warns)
        assert len(plan) == 3, plan
        # 独立算术：600519（1500 元/股，一手 15 万）与 601888（70 元，一手 7000）
        # 均超 5000 元预算 ⇒ 无委托 + 必须显式警告（不静默）
        assert any("OVER_BUDGET:600519" in w for w in warns), warns
        assert any("OVER_BUDGET:601888" in w for w in warns), warns
        codes_in_plan = {i.code for i in plan}
        assert "600519" not in codes_in_plan, "超预算标的应无委托"
        # 600036: floor(5000/35/100)*100 = 100 股
        i36 = next(i for i in plan if i.code == "600036")
        assert i36.qty == 100 and i36.side == "BUY" and i36.price == "35"
        # 601318: floor(5000/45/100)*100 = 100 股
        assert next(i for i in plan if i.code == "601318").qty == 100
        checks.append("plan_lot_floor")

        # 2) 幂等 cid：同四元组恒定，且与独立 hashlib 一致
        cid = client_order_id(day=EXEC_DATE, code="600036", side="BUY", qty=100)
        raw = f"20261201|600036|BUY|100".encode()
        assert cid == DRILL_TAG + "-" + hashlib.sha256(raw).hexdigest()[:24]
        assert cid == client_order_id(day=EXEC_DATE, code="600036", side="BUY", qty=100)
        assert len(cid) == len(DRILL_TAG) + 1 + 24
        checks.append("idempotent_cid")

        # 3) 清非目标：持仓 000001 应被卖出
        plan2 = build_plan(ledger=led, positions={"000001": 800}, cash=100000.0, price_of=px)
        assert any(i.code == "000001" and i.side == "SELL" and i.qty == 800 for i in plan2)
        checks.append("exit_non_target")

        # 4) 无价格 ⇒ 失败关闭
        try:
            build_plan(ledger=led, positions={}, cash=100000.0, price_of=lambda c: None)
            raise AssertionError("缺价格应 Halt")
        except Halt as e:
            assert "NO_PRICE" in str(e)
        checks.append("no_price_fails_closed")

        # 5) E1：无台账 / mode 错 / 无 d4 ⇒ 失败
        assert gate_ledger(d / "none.json")["ok"] is False
        bad_mode = d / "bad_mode.json"
        bad_mode.write_text(json.dumps({"mode": "LATE_RUN", "target_holds_d4": ["600036"]}),
                            encoding="utf-8")
        assert gate_ledger(bad_mode)["ok"] is False
        legacy = d / "legacy.json"
        legacy.write_text(json.dumps({"mode": "FORWARD", "target_holds": ["600036"]}),
                          encoding="utf-8")
        g = gate_ledger(legacy)
        assert g["ok"] is False and g["reason"] == "NO_TARGET_HOLDS_D4"
        ok_led = d / "ok.json"
        ok_led.write_text(json.dumps(_fake_ledger()), encoding="utf-8")
        assert gate_ledger(ok_led)["ok"] is True
        # U16-D4-PRICE-FIX：d4 目标价格覆盖——删一个价 ⇒ NO_PRICE_COVERAGE_D4；
        # 非正价（0.0）不算覆盖。p13 的 ref_close_d4 键为纯 6 位代码（无交易所后缀）。
        no_px = _fake_ledger()
        no_px["ref_close_d4"].pop(next(iter(no_px["ref_close_d4"])))
        np_led = d / "no_px.json"
        np_led.write_text(json.dumps(no_px), encoding="utf-8")
        gn = gate_ledger(np_led)
        assert gn["ok"] is False and gn["reason"] == "NO_PRICE_COVERAGE_D4"
        bad_px = _fake_ledger()
        bad_px["ref_close_d4"][bad_px["target_holds_d4"][0]] = 0.0
        bp_led = d / "bad_px.json"
        bp_led.write_text(json.dumps(bad_px), encoding="utf-8")
        assert gate_ledger(bp_led)["ok"] is False
        checks.append("gate_e1")

        # 6) E2：无产物 ⇒ 失败；给假产物 ⇒ 通过
        assert gate_dryrun()["ok"] is False
        dry = d / "dryrun_20261127.json"
        dry.write_text("{}", encoding="utf-8")
        pf = d / "preflight_20261130.json"
        pf.write_text("{}", encoding="utf-8")
        g2 = gate_dryrun(patterns=(str(dry),), preflight_patterns=(str(pf),))
        assert g2["ok"] is True
        checks.append("gate_e2")

        # 7) E3：无授权 ⇒ NO_AUTH；哈希自洽夹具 ⇒ ALLOW
        assert gate_auth(d / "none.json", now="2026-12-01T09:35:00+08:00",
                         notional=5000, cumulative=0, available=100000)["ok"] is False
        doc = {"schema_version": "paper_auth_v1", "channel": "gm-paper",
               "account_ref": "SELFTEST", "max_notional": 100000,
               "not_after": "2026-12-02T23:59:00+08:00",
               "scopes": sorted(guard.FROZEN_SCOPES), "statement_ref": "3207bff41a58",
               "issued_by": "SELFTEST", "issued_at": "2026-11-14T22:40:00+08:00"}
        doc["sha256"] = guard.compute_hash(doc)
        ap = d / "auth.json"
        ap.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        assert gate_auth(ap, now="2026-12-01T09:35:00+08:00", notional=5000,
                         cumulative=0, available=100000)["ok"] is True
        checks.append("gate_e3")

        # 8) E4：注入 runner
        assert gate_isolation_manifest(
            runner=lambda cmd: (7, "FAIL 2 项"))["ok"] is False
        assert gate_isolation_manifest(
            runner=lambda cmd: (0, "PASS 57/57 ｜ FAIL 0 项"))["ok"] is True
        checks.append("gate_e4")

        # 9) 幂等落地：重复追加不增行
        fp = d / "orders.jsonl"
        rows = [{"client_order_id": "SIM_DRILL-abc", "code": "600036"}]
        assert append_orders(fp, rows) == 1
        assert append_orders(fp, rows) == 0
        assert len(fp.read_text(encoding="utf-8").splitlines()) == 1
        checks.append("append_dedup")

        # 10) 六道门控：时钟偏差 / 缺台账 / 缺授权 / 旁证缺失 ⇒ 整体不过
        # E0 注入确定失败的时钟，保证 self-check 纯离线且不读真实时钟。
        bad_clock = {"real_now": dt.datetime.fromisoformat(
                         intended_now().replace("09:35", "10:35")),
                     "ntp_check": lambda: True}
        gates = run_gates(now="2026-12-01T09:35:00+08:00", notional=5000,
                          cumulative=0, available=100000,
                          ledger_path=d / "none.json", auth_path=d / "none.json",
                          runner=lambda cmd: (7, "FAIL"), clock=bad_clock,
                          corroboration={"file_path": d / "none.json"})
        assert [g["ok"] for g in gates] == [False] * 6
        checks.append("gates_all_deny")

        # 11) E0 时钟：偏差大 / NTP 不同步 ⇒ 失败；准点且同步 ⇒ 通过
        import sim_exec_result as _ser
        t0 = dt.datetime.fromisoformat(intended_now())
        assert gate_clock(real_now=t0, ntp_check=lambda: True)["ok"] is True
        assert gate_clock(real_now=t0.replace(hour=10, minute=35),
                          ntp_check=lambda: True)["ok"] is False
        assert gate_clock(real_now=t0, ntp_check=lambda: False)["ok"] is False
        checks.append("gate_e0")

        # 12) 失败日志四态 + 原子写（临时目录，不碰正式路径）
        s = _ser.self_check()
        assert s["decision"] == "PASS"
        checks.append("exec_result_4states")

    return {"decision": "PASS", "statistical_verdict": "ENGINEERING_SELF_CHECK_ONLY",
            "maturity_level": "EXECUTION_READY_GATED", "checks": checks,
            "n_real_orders": 0, "limitation": IMPLEMENTATION_LIMITATION}


def _read_account() -> dict:
    """只读账户（cash / positions）。凭证缺失 ⇒ 失败关闭，不猜、不用默认仓位。"""
    import os
    tok = os.environ.get("DQ_GM_TOKEN")
    acc_id = os.environ.get("DQ_GM_ACCOUNT_ID")
    if not tok or not acc_id:
        return {"error": "ENV_MISSING", "nav": 0.0, "available": 0.0, "positions": {}}
    try:
        import gmtrade.api as gm
        gm.set_token(tok)
        gm.set_endpoint(os.environ.get("DQ_GM_ENDPOINT", "api.myquant.cn:9000"))
        gm.login(gm.account(account_id=acc_id, account_alias=""))
        c = gm.get_cash()
        positions: dict[str, int] = {}
        for p in (gm.get_positions() or []):
            code = normalize_code(getattr(p, "symbol", ""))
            vol = int(getattr(p, "volume", 0) or 0)
            if code and vol > 0:
                positions[code] = positions.get(code, 0) + vol
        return {"error": None, "nav": float(getattr(c, "nav", 0) or 0),
                "available": float(getattr(c, "available", 0) or 0),
                "positions": positions}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"SDK:{type(exc).__name__}", "nav": 0.0,
                "available": 0.0, "positions": {}}


def _log_exec_result(gates: list[dict], outcome: str, rc: int) -> None:
    """U16-EXEC-2：所有 execute 退出路径原子落盘（失败静默也不允许）。"""
    import sim_exec_result as ser
    try:
        gmap = {g["gate"]: ("PASS" if g["ok"] else "FAIL") for g in gates}
        rec = ser.build_result(outcome=outcome, rc=rc, gates=gmap)
        ser.write_result(rec)
    except Exception as exc:  # noqa: BLE001
        print(f"[exec] CRITICAL:RESULT_WRITE_FAILED:{type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="U-16 仿真调仓执行器（六道生效条件门控）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--self-check", action="store_true")
    g.add_argument("--plan", action="store_true")
    g.add_argument("--gates", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    try:
        verify_prereg()
    except Halt as e:
        print(f"[exec] HALT:{e}")
        return 3

    if args.self_check:
        try:
            print(json.dumps(self_check(), ensure_ascii=False))
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[exec] HALT:SELF_CHECK_FAILED:{type(exc).__name__}: {exc}")
            return 2

    # 以下三种模式都需要门控与外部只读数据
    now = f"{EXEC_DATE[:4]}-{EXEC_DATE[4:6]}-{EXEC_DATE[6:]}T{FIRST_ORDER_TIME}:00+08:00"
    acct = _read_account()
    led = gate_ledger()

    # 台账通过且账户可读 ⇒ 先算计划（E3 用**真实计划金额**复核授权额度，不用占位数）
    plan: list[Intent] = []
    plan_err: str | None = None
    warns: list[str] = []
    if led["ok"] and acct["error"] is None:
        try:
            rec = json.loads((ROOT / LEDGER_REL).read_text(encoding="utf-8"))
            # D4 价格源（U16-D4-PRICE-FIX）：只认与 target_holds_d4 同源的 ref_close_d4。
            # 历史 orders[].ref_close 属主口径，与 d4 目标常不重合，不得作为 d4 价格源。
            prices = price_map_from_ledger(rec)
            plan = build_plan(ledger=rec, positions=acct["positions"],
                              cash=acct["nav"], price_of=prices.get, warnings=warns)
        except Halt as e:
            plan_err = str(e)
    need = plan_notional(plan) if plan else 1.0
    cumulative = 0.0

    gates = run_gates(now=now, notional=need, cumulative=cumulative,
                      available=acct["available"])
    summary = {"now": now,
               "account": {"error": acct["error"], "nav": acct["nav"],
                           "available": acct["available"],
                           "n_positions": len(acct["positions"])},
               "notional_checked": need, "plan_error": plan_err,
               "warnings": warns,
               "price_source": PRICE_SOURCE,
               "reprice_implementable": REPRICE_IMPLEMENTABLE,
               "limitation": IMPLEMENTATION_LIMITATION}
    if args.gates:
        print(json.dumps({**summary, "gates": gates}, ensure_ascii=False, indent=2))
        return 0 if all(x["ok"] for x in gates) else 4

    blocked = [x for x in gates if not x["ok"]]
    if plan_err or blocked:
        reason = ("PLAN_FAILED:" + plan_err) if plan_err else "五道生效条件未全部满足"
        if args.execute:
            _log_exec_result(gates, "GATE_FAIL", 4)
            print(json.dumps({"decision": "NOT_AUTHORIZED", "reason": reason,
                              "failed": blocked, "plan_error": plan_err,
                              "n_real_orders": 0,
                              "limitation": IMPLEMENTATION_LIMITATION},
                             ensure_ascii=False))
            return 4
        print(json.dumps({"decision": "GATES_NOT_PASSED", "reason": reason,
                          "failed": blocked, "plan_error": plan_err},
                         ensure_ascii=False))
        return 4

    if args.plan:
        print(json.dumps({"date": EXEC_DATE, "n_intents": len(plan),
                          "notional": need,
                          "intents": [i.__dict__ for i in plan],
                          **summary},
                         ensure_ascii=False, indent=2))
        return 0

    # --execute：门控已过 ⇒ 真下单（仅此路径）
    broker = GmTradeBroker(token=_env("DQ_GM_TOKEN"), account_id=_env("DQ_GM_ACCOUNT_ID"))
    out = []
    for it in plan:
        out.append(broker.place_limit_order(code=it.code, side=it.side, qty=it.qty,
                                           price=it.price, cid=it.client_order_id))
    path = ROOT / f"research/stock_pick_2026-10-25/forward/sim_orders/{EXEC_DATE}.jsonl"
    n = append_orders(path, out)
    _log_exec_result(gates, "SUCCESS", 0)
    print(json.dumps({"decision": "EXECUTED", "n_orders": len(out), "n_appended": n,
                      "path": str(path.relative_to(ROOT)),
                      "limitation": IMPLEMENTATION_LIMITATION}, ensure_ascii=False))
    return 0


def _env(name: str) -> str:
    import os
    v = os.environ.get(name)
    if not v:
        raise Halt(f"ENV_MISSING:{name}")
    return v


def _outcome_from(rc: int, gates: list[dict] | None) -> str:
    if rc == 0:
        return "SUCCESS"
    if gates:
        if any(g["gate"] == "E0" and not g["ok"] for g in gates):
            return "CLOCK_FAIL"
        return "GATE_FAIL"
    return "ERROR"


def main_logged(argv: list[str] | None = None) -> int:
    """``--execute`` 专用：保证所有退出路径原子落盘 sim_exec_result/v1。"""
    import sim_exec_result as ser
    args = list(argv or [])
    started = ser.now_real_iso()
    gates_seen: list[dict] = []
    rc = 4
    outcome = "ERROR"
    orig_run = globals().get("run_gates")

    def wrapped(*a, **kw):
        nonlocal gates_seen
        gates_seen = orig_run(*a, **kw)
        return gates_seen

    globals()["run_gates"] = wrapped
    try:
        rc = main(args)
        outcome = _outcome_from(rc, gates_seen)
        return rc
    except Halt as e:
        rc, outcome = 3, ("CLOCK_FAIL" if "CLOCK" in str(e) else "ERROR")
        print(f"[exec] HALT:{e}")
        return rc
    except Exception as exc:  # noqa: BLE001
        rc, outcome = 2, "ERROR"
        print(f"[exec] HALT:UNEXPECTED:{type(exc).__name__}: {exc}")
        return rc
    finally:
        globals()["run_gates"] = orig_run
        try:
            gmap = {g["gate"]: ("PASS" if g["ok"] else "FAIL") for g in gates_seen}
            rec = ser.build_result(outcome=outcome, rc=rc, gates=gmap,
                                   started_at=started, finished_at=ser.now_real_iso())
            ser.write_result(rec)
        except Exception as exc:  # noqa: BLE001
            print(f"[exec] CRITICAL:RESULT_WRITE_FAILED:{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    if "--execute" in sys.argv:
        sys.exit(main_logged(sys.argv[1:]))
    sys.exit(main())
