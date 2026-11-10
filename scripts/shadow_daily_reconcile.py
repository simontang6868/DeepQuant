"""SHADOW 每日对账 —— 接进日更链的**只读**管道（2.9 / G2）。

为什么需要这个脚本
------------------
``backend/src/deepquant/execution/shadow.py`` 的 2.9-a 机制早已 PASS，但它
**只被测试引用**，日更链里没有任何调用。所以 STATUS 里 G2 记的「0 天」是
真实的：不是"跑了没算"，是"根本没接进来"。本脚本把这一步接上。

⭐ 最重要的一条：不许自证
------------------------
G2 判据是「连续 20 个交易日无未解释差异」。但当前 ``LIVE_ENABLED=false``、
没有券商账户、也没有仿真账户 ⇒ **没有真实的「实际持仓与资金」**。
如果此时自己造一个账户快照、跑出「无差异」然后计入 G2，就是拿自己的输出
证明自己正确——正是本项目反复踩过的「没检出 ≠ 没问题」。

因此本脚本把每天的结果分三档，只有第一档能计入 G2：

``REAL_PAPER``       ``forward/account_snapshot.json`` 存在、``asof == 当日``、
                     ``source`` 非空、结构合法 ⇒ 用**真实仿真账户**的
                     资金/持仓做快照，diff 才构成证据。
``SELF_CHECK_ONLY``  没有上述快照 ⇒ 用合成快照只验证「管道没坏」
                     （planner 还能算、quote 还能读、记录还能写），
                     diff **只记录、不判定、不计天**。
``NO_INPUTS``        台账或当日 universe 快照缺失 ⇒ **失败关闭**，非零退出。

⇒ **接入管道 ≠ G2 开始计数**。20 天倒计时从「有真实仿真账户快照」的
  第一个交易日起；在此之前 ``--summary`` 会如实报 0/20。

写出的记录在 ``forward/shadow_reconcile/YYYYMMDD.json``，并已加入
``backup_offsite.sh`` 的每日机外备份（时点证据不能只留在本机）。

本脚本**绝不下单**：只 import ``execution.shadow``（其函数签名里根本没有
broker/adapter），不 import 任何券商适配器，不写正式台账，不发单、不发邮件。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deepquant.broker.models import AccountSnapshot, Cash, Position, Quote  # noqa: E402
from deepquant.execution.shadow import (  # noqa: E402
    ExplanationContext, build_shadow_plan, compare_with_ledger,
)

FWD = ROOT / "research" / "stock_pick_2026-10-25" / "forward"
LEDGER_DIR = FWD / "ledger"
UNIVERSE_DIR = FWD / "universe_state"
OUT_DIR = FWD / "shadow_reconcile"
ACCOUNT_SNAPSHOT = FWD / "account_snapshot.json"   # 旧单文件：ECR 后冻结，仅兼容旧调用
ACCOUNT_DIR = FWD                                   # 新布局：按 day 解析 rev 文件
CONFIG = FWD / "sp4_config_v2.json"
CALENDAR = FWD / "trade_calendar.json"

REAL_PAPER = "REAL_PAPER"
SELF_CHECK_ONLY = "SELF_CHECK_ONLY"
NO_INPUTS = "NO_INPUTS"
REV_CONFLICT = "REV_CONFLICT"   # R7 2e：同日多 rev 账户内容不一致 ⇒ 不计 G2

EXIT_OK = 0
EXIT_INPUT = 2          # 输入缺失/损坏：失败关闭
EXIT_UNEXPLAINED = 3    # 真实账户下出现未解释差异
EXIT_CONFLICT = 4       # 同日记录已存在且内容不同：不静默覆盖


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    """读冻结配置。缺字段一律抛错——参数不许靠猜。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    s = d["strategy"]
    n_hold = int(s["n_hold"])
    capital = float(d["deployment"]["initial_capital"])
    weight = float(s["stock_weight"])
    if n_hold <= 0 or capital <= 0 or not (0 < weight <= 1):
        raise ValueError(f"配置参数非法：n_hold={n_hold} capital={capital} weight={weight}")
    return {"n_hold": n_hold, "capital": capital, "weight": weight,
            "per_name": 1.0 / n_hold, "sleeve": capital * weight,
            "path": str(path), "sha256": sha256_file(path)}


def latest_ledger(day: str, base: Path = LEDGER_DIR) -> tuple[Path, dict[str, Any]] | None:
    """取 ``date <= day`` 的最近一条台账。不用目录 mtime，按文件名里的日期排。"""
    rows: list[tuple[str, Path]] = []
    for p in sorted(base.glob("*.json")):
        if p.stem.isdigit() and len(p.stem) == 8 and p.stem <= day:
            rows.append((p.stem, p))
    if not rows:
        return None
    _, p = max(rows)
    return p, json.loads(p.read_text(encoding="utf-8"))


def ledger_targets(rec: dict[str, Any]) -> tuple[str, ...]:
    """v2 口径优先用 ``target_holds_d4``；老台账退回 ``target_holds``（LEGACY_NO_D4）。"""
    for key in ("target_holds_d4", "target_holds"):
        v = rec.get(key)
        if isinstance(v, list) and v:
            return tuple(str(x) for x in v)
    return ()


def load_universe(day: str, base: Path = UNIVERSE_DIR) -> tuple[Path, dict[str, dict[str, Any]]] | None:
    """读当日 universe_state 快照（已有 sha256、已进镜像校验）。"""
    p = base / f"{day}.csv"
    if not p.is_file():
        return None
    out: dict[str, dict[str, Any]] = {}
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            if not code:
                continue
            try:
                px = float(row.get("last_price") or 0)
            except ValueError:
                px = 0.0
            out[code] = {
                "last": px,
                "board": (row.get("board") or "").strip(),
                "is_st": str(row.get("is_st", "")).strip().lower() == "true",
                "no_quote": str(row.get("no_quote_today", "")).strip().lower() == "true",
            }
    return (p, out) if out else None


def load_account_snapshot(day: str, path: Path = ACCOUNT_SNAPSHOT) -> tuple[str, dict[str, Any] | None]:
    """返回 ``(mode_reason, payload)``。任何不合格都退回 None，绝不凑一个快照。

    合格条件：文件存在、可解析、``asof == day``、``source`` 非空、
    cash/positions 结构完整。**陈旧快照必须拒绝**——否则 11-30 的
    "对账无差异"可能用的是三天前的持仓，那就成了时点造假。
    """
    if not path.is_file():
        return "no_account_snapshot_file", None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"account_snapshot_unparsable:{type(e).__name__}", None
    if str(d.get("asof", "")) != day:
        return f"account_snapshot_stale:asof={d.get('asof')}", None
    if not str(d.get("source", "")).strip():
        return "account_snapshot_missing_source", None
    if not isinstance(d.get("cash"), dict) or not isinstance(d.get("positions"), list):
        return "account_snapshot_bad_structure", None
    return "ok", d


def load_daily_snapshot(day: str, directory: Path = ACCOUNT_DIR
                        ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    """R7 2a/2e：**按 day 直接解析 rev 文件**，完全不依赖 ``latest.json``。

    返回 ``(reason, payload, meta)``。只有所有 rev 的**账户内容**
    （asof/source/cash/positions）完全一致时才取 rev 号最大者；
    否则返回 ``REV_CONFLICT``、payload=None ⇒ 该日 ``SELF_CHECK_ONLY``、
    ``counts_toward_g2=false``，待人工复核。
    """
    meta: dict[str, Any] = {"n_revs": 0, "revs": [], "sha256": None, "rev": None,
                            "path": None}
    paths: list[tuple[int, Path]] = []
    for p in directory.glob(f"account_snapshot_{day}_rev*.json"):
        m = re.search(r"_rev(\d+)\.json$", p.name)
        if m and p.is_file():
            paths.append((int(m.group(1)), p))
    paths.sort()
    meta["n_revs"] = len(paths)
    meta["revs"] = [n for n, _ in paths]
    if not paths:
        return "no_daily_snapshot_file", None, meta
    docs: list[tuple[int, Path, dict[str, Any]]] = []
    for n, p in paths:
        try:
            docs.append((n, p, json.loads(p.read_text(encoding="utf-8"))))
        except Exception as exc:
            return f"account_snapshot_unparsable:{type(exc).__name__}", None, meta
    for _, _, d in docs:
        if str(d.get("asof", "")) != day:
            return f"account_snapshot_stale:asof={d.get('asof')}", None, meta
        if not str(d.get("source", "")).strip():
            return "account_snapshot_missing_source", None, meta
        if not isinstance(d.get("cash"), dict) or not isinstance(d.get("positions"), list):
            return "account_snapshot_bad_structure", None, meta
    bodies = {json.dumps({k: d.get(k) for k in ("asof", "source", "cash", "positions")},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
              for _, _, d in docs}
    if len(bodies) != 1:
        return REV_CONFLICT, None, meta
    n, p, d = docs[-1]
    meta.update({"rev": n, "sha256": sha256_file(p), "path": str(p)})
    return "ok", d, meta


def make_snapshot(payload: dict[str, Any] | None, day: str, cfg: dict[str, Any]
                  ) -> tuple[AccountSnapshot, str]:
    """真实快照直接用；没有则合成一个**仅供自检**的空仓快照。"""
    if payload is not None:
        c = payload["cash"]
        cash = Cash(available=float(c.get("available", 0)),
                    frozen=float(c.get("frozen", 0)),
                    total=float(c.get("total", 0)))
        poses = tuple(Position(code=str(p["code"]),
                               quantity=int(p.get("quantity", 0)),
                               available=int(p.get("available", 0)),
                               avg_cost=float(p.get("avg_cost", 0)))
                      for p in payload["positions"])
        return AccountSnapshot(cash=cash, positions=poses, ts=int(day)), REAL_PAPER
    sleeve = cfg["sleeve"]
    return AccountSnapshot(cash=Cash(available=sleeve, frozen=0.0, total=sleeve),
                           positions=(), ts=int(day)), SELF_CHECK_ONLY


def make_quotes(universe: dict[str, dict[str, Any]], day: str) -> dict[str, Quote]:
    return {c: Quote(code=c, last=float(v["last"]), ts=int(day),
                     stale=bool(v["no_quote"]) or float(v["last"]) <= 0)
            for c, v in universe.items()}


def make_ctx(universe: dict[str, dict[str, Any]],
             skipped: tuple[Any, ...]) -> ExplanationContext:
    """只按**事前定义**的原因分类。⭐ 匹配不上的一律不归类，留 UNEXPLAINED。

    把一条不认识的真差异塞进"资金不足"等于自己把闸门关了
    （``shadow.py`` 模块文档的原话）。宁可每天报一次未解释差异让人工核对，
    也不能为了报告好看而硬凑理由。

    ``last_price`` 只收**有效报价**：无报价的标的必须留 ``None``，
    ``_explain_missing`` 才会把它归到 ``EXPLAINED_NO_QUOTE``；
    若把停牌股的价格也填进去，它会一路掉到 UNEXPLAINED 造成假告警。
    """
    lot_short: set[str] = set()
    for s in skipped:
        why = str(getattr(s, "reason", ""))
        code = str(getattr(s, "code", ""))
        if "不足一手" in why or "资金不足" in why:
            lot_short.add(code)
        # "无有效行情" / 未知原因：都不归类，交给 last_price / UNEXPLAINED
    return ExplanationContext(
        price_cap=None, allowed_boards=None,
        board_of={c: v["board"] for c, v in universe.items()},
        last_price={c: v["last"] for c, v in universe.items()
                    if v["last"] > 0 and not v["no_quote"]},
        st_codes=frozenset(c for c, v in universe.items() if v["is_st"]),
        cash_short_codes=frozenset(),
        lot_short_codes=frozenset(lot_short),
    )


def build_record(day: str, *, config_path: Path = CONFIG,
                 ledger_dir: Path = LEDGER_DIR,
                 universe_dir: Path = UNIVERSE_DIR,
                 account_path: Path = ACCOUNT_SNAPSHOT,
                 account_dir: Path | None = None,
                 today: str | None = None) -> tuple[dict[str, Any], int]:
    """核心逻辑，供日更链与测试共用。返回 ``(记录, 退出码)``。

    路径全部可注入：测试用 tmp 目录构造最小输入，不碰真实台账。
    ``today`` 只用于测试的「事后补跑」场景；生产不传，取真实当天。
    """
    today = today or dt.date.today().strftime("%Y%m%d")
    is_backfill = (today != day)
    cfg = load_config(config_path)
    led = latest_ledger(day, ledger_dir)
    uni = load_universe(day, universe_dir)

    if led is None or uni is None:
        rec = {
            "date": day, "mode": NO_INPUTS, "counts_toward_g2": False,
            "note": ("缺少台账或当日 universe_state 快照；失败关闭，不写 diff、不计 G2。"
                     "注意：p17 快照失败不中断日更链，但当日对账必须判 NO_INPUTS。"),
            "has_ledger": led is not None, "has_universe": uni is not None,
            "inputs": {"config": {"path": cfg["path"], "sha256": cfg["sha256"]}},
            "orders_sent": False,
        }
        return rec, EXIT_INPUT

    led_path, led_rec = led
    uni_path, universe = uni
    targets = ledger_targets(led_rec)
    if not targets:
        rec = {
            "date": day, "mode": NO_INPUTS, "counts_toward_g2": False,
            "note": "台账里没有可用的目标持仓（target_holds_d4 / target_holds 均空）",
            "inputs": {"ledger": {"path": str(led_path), "sha256": sha256_file(led_path)}},
            "orders_sent": False,
        }
        return rec, EXIT_INPUT

    legacy = "target_holds_d4" not in led_rec
    acct_meta: dict[str, Any] = {}
    if account_dir is not None:
        # ECR 后的运行路径：按 day 解析 rev 文件，不依赖 latest.json
        snap_reason, snap_payload, acct_meta = load_daily_snapshot(day, account_dir)
    else:
        # 兼容旧调用（既有测试注入单文件路径）
        snap_reason, snap_payload = load_account_snapshot(day, account_path)
    snapshot, mode = make_snapshot(snap_payload, day, cfg)
    quotes = make_quotes(universe, day)

    record = {"decision_date": int(day), "target_holds": list(targets)}
    plan = build_shadow_plan(record, snapshot, quotes,
                             target_weight=cfg["per_name"])
    # 差异归因要用「本次规划实际跳过了什么」，所以先建计划、再构造 ctx、再比对。
    diffs_raw = compare_with_ledger(plan, record,
                                    make_ctx(universe, plan.skipped))
    diffs = [{"code": d.code, "reason": d.reason.value,
              "detail": d.detail, "blocks_g2": d.blocks_g2}
             for d in diffs_raw]
    n_unexplained = sum(1 for d in diffs if d["blocks_g2"])

    # 只有「真实仿真账户 + 无未解释差异 + 当天生成」才构成 G2 的一天。
    # ⚠️ is_backfill 这条不能少：否则事后补跑一堆历史日期的 REAL_PAPER
    # 就能把 20 天凑出来，把「回顾」包装成「连续前瞻观测」。
    constitutes = (mode == REAL_PAPER and n_unexplained == 0
                   and not is_backfill)
    notes = []
    if is_backfill:
        notes.append(f"事后补跑（生成日 {today} ≠ 交易日 {day}）：不计入 G2，"
                     "仅可用于管道复核")
    if legacy:
        notes.append("LEGACY_NO_D4：台账缺 d4 字段（11-30 前的老记录），本日不计入 G2")
    if mode == SELF_CHECK_ONLY and snap_reason != REV_CONFLICT:
        notes.append(f"管道自检（{snap_reason}）：没有真实仿真账户快照，"
                     "diff 只记录不判定、不计入 G2")
    if snap_reason == REV_CONFLICT:
        notes.append(f"REV_CONFLICT：当日 {acct_meta.get('n_revs')} 个 rev 的账户内容不一致；"
                     "mode=SELF_CHECK_ONLY、不计 G2，待人工复核")
    if constitutes and legacy:
        constitutes = False
    if n_unexplained and mode == REAL_PAPER:
        notes.append("真实账户下出现未解释差异：必须人工核对，不得自行归类")

    rec = {
        "date": day,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "run_date": today,
        "is_backfill": is_backfill,
        "mode": mode,
        "counts_toward_g2": constitutes,
        "legacy_no_d4": legacy,
        "ledger_date": str(led_rec.get("date", "")),
        "account_snapshot_reason": snap_reason,
        "account_rev": acct_meta.get("rev"),
        "n_account_revs": acct_meta.get("n_revs"),
        "account_snapshot_sha256": acct_meta.get("sha256"),
        "inputs": {
            "config": {"path": cfg["path"], "sha256": cfg["sha256"]},
            "ledger": {"path": str(led_path), "sha256": sha256_file(led_path)},
            "universe": {"path": str(uni_path), "sha256": sha256_file(uni_path)},
            "account": ({"path": acct_meta.get("path") or str(account_path),
                         "sha256": acct_meta.get("sha256") or sha256_file(account_path),
                         "source": snap_payload.get("source"),
                         "asof": snap_payload.get("asof"),
                         "rev": acct_meta.get("rev"),
                         "n_revs": acct_meta.get("n_revs")}
                        if snap_payload is not None else None),
        },
        "params": {"n_hold": cfg["n_hold"], "per_name_weight": cfg["per_name"],
                   "sleeve": cfg["sleeve"]},
        "n_targets": len(targets),
        "targets": list(targets),
        "n_orders": len(plan.would_be_orders),
        "order_codes": [o.code for o in plan.would_be_orders],
        "orders_sent": False,
        "diffs": diffs,
        "n_unexplained": n_unexplained,
        "note": "；".join(notes) or "真实仿真账户对账无未解释差异",
        "code_sha256": sha256_file(Path(__file__).resolve()),
    }
    rc = EXIT_UNEXPLAINED if (mode == REAL_PAPER and n_unexplained) else EXIT_OK
    return rec, rc


def write_record(rec: dict[str, Any], day: str, *, out_dir: Path = OUT_DIR,
                 write: bool = True) -> tuple[Path, int]:
    """不静默覆盖：同日已有不同内容 ⇒ 冲突退出。``--no-write`` 零副作用。"""
    p = out_dir / f"{day}.json"
    body = json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if not write:
        return p, EXIT_OK
    out_dir.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        if p.read_text(encoding="utf-8") != body:
            return p, EXIT_CONFLICT
        return p, EXIT_OK
    p.write_text(body, encoding="utf-8", newline="\n")
    return p, EXIT_OK


def g2_summary(base: Path = OUT_DIR, cal: Path = CALENDAR,
               today: str | None = None) -> dict[str, Any]:
    """按独立交易日历统计连续 G2 天数。只读，不生成证据。"""
    today = today or dt.date.today().strftime("%Y%m%d")
    days = [str(x) for x in json.loads(cal.read_text(encoding="utf-8"))["dates"]]
    good: set[str] = set()
    for p in base.glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("counts_toward_g2") is True:
            good.add(str(d.get("date", "")))
    streak: list[str] = []
    broken: str | None = None
    for d in reversed([x for x in days if x <= today]):
        if d not in good:
            broken = d
            break
        streak.append(d)
    streak.reverse()
    return {"n_qualified_days": len(good), "streak": len(streak),
            "streak_days": streak, "first_break": broken,
            "gate": "G2", "need": 20, "passed": len(streak) >= 20}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SHADOW 每日只读对账（不发单）")
    ap.add_argument("--date", default=dt.date.today().strftime("%Y%m%d"),
                    help="交易日 YYYYMMDD；默认今天")
    ap.add_argument("--no-write", action="store_true", help="只算不写（演练用）")
    ap.add_argument("--summary", action="store_true", help="只打印 G2 连续天数统计")
    args = ap.parse_args(argv)

    if args.summary:
        s = g2_summary()
        print(f"[G2] 合格日 {s['n_qualified_days']} 天｜连续段 {s['streak']}/20"
              f"｜断点 {s['first_break']}｜{'PASS' if s['passed'] else '未通过'}")
        return EXIT_OK

    # ECR 后的运行路径：按 day 解析 rev 文件；不依赖 latest.json、不读冻结的旧单文件
    rec, rc = build_record(args.date, account_dir=FWD)
    p, wrc = write_record(rec, args.date, write=not args.no_write)
    if wrc == EXIT_CONFLICT:
        print(f"[shadow] ✗ 同日记录已存在且内容不同，拒绝覆盖：{p}", file=sys.stderr)
        return EXIT_CONFLICT

    tag = {"REAL_PAPER": "真实仿真账户", "SELF_CHECK_ONLY": "管道自检",
           "NO_INPUTS": "输入缺失"}[rec["mode"]]
    print(f"[shadow] {args.date} {tag}｜diff {len(rec.get('diffs', []))} 条"
          f"｜未解释 {rec.get('n_unexplained', 0)} 条"
          f"｜计入G2={'是' if rec['counts_toward_g2'] else '否'}"
          f"｜本应下单 {rec.get('n_orders', 0)} 笔（未发送）"
          f"｜rc={rc}｜{p.name}{' [no-write]' if args.no_write else ''}")
    if rec.get("note"):
        print(f"[shadow] note: {rec['note']}")
    if rec["mode"] == NO_INPUTS:
        print("[shadow] ✗ 输入缺失：失败关闭，日更链须记录告警（不阻断链路）",
              file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
