"""11-30 首个 FORWARD 决策日的**只读**前置预检。

为什么单列一个脚本：
  11-30 是唯一能产出真正前瞻记录的时刻，错过一次要再等一个月。而它的失败
  大多是**静默的**——链路报 `DECIDED`，台账也写了，但 d4 字段是 None、
  或数据末日没覆盖月末、或沿用了昨天的 universe_state 快照，
  于是这期记录按 R3 不能计入证据。等到 12 月才发现就补不回来了。

本脚本**不写任何文件、不发单、不碰台账**，只检查静态前置与时点条件。
未来日期（如今天预检 11-30）里"当天才会生成的快照"一律标 PENDING，
**不**为了表格好看就假装 PASS。

用法：
    python scripts/preflight_decision_day.py                       # 预检 2026-11-30
    python scripts/preflight_decision_day.py --today 20261130 --data-last 20261130
    python scripts/preflight_decision_day.py --today 20261130 --data-last 20261127  # 看失败模式
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SP5 = REPO / "research" / "stock_pick_2026-10-25" / "scripts" / "sp5"
FWD = REPO / "research" / "stock_pick_2026-10-25" / "forward"
sys.path.insert(0, str(SP5))
sys.path.insert(0, str(SP5.parent))
sys.path.insert(0, str(REPO / "scripts"))

import trade_calendar as tcal  # noqa: E402
import deploy_manifest as dm  # noqa: E402  R6-3 部署可证性

OK = "PASS"
BAD = "FAIL"
PEND = "PENDING"


def parse_day(s: str) -> dt.date:
    s = s.replace("-", "")[:8]
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))


def timer_covers(day: str, official: list[str]) -> tuple[bool, str]:
    """systemd timer 是 `Mon..Fri 17:10`，且当天必须是交易日。"""
    d = parse_day(day)
    if d.weekday() >= 5:
        return False, "周末，Mon..Fri timer 不触发"
    if day not in official:
        return False, "非交易日（节假日），timer 触发但 is_due 会判 NOT_DUE"
    return True, f"周{'一二三四五'[d.weekday()]}，且日历列为交易日"


def calendar_digest(payload: dict) -> str:
    """与 `trade_calendar.save` 同一算法（dates 用换行拼 ASCII 后 sha256）。"""
    days = [str(x) for x in payload.get("dates", [])]
    return hashlib.sha256("\n".join(days).encode("ascii")).hexdigest()


def check(args) -> tuple[list[tuple[str, str, str]], int]:
    rows: list[tuple[str, str, str]] = []
    today = args.today
    data_last = args.data_last or today
    cal_path = Path(args.calendar) if args.calendar else FWD / "trade_calendar.json"

    # C1 日历可读 + 自校验（直接调生产 load，不自己另写一套判据）
    try:
        official = [str(x) for x in tcal.load(cal_path, today=today)]
        payload = json.loads(cal_path.read_text(encoding="utf-8"))
        dig = calendar_digest(payload)
        same = dig == payload.get("sha256_of_dates")
        rows.append(("C1 日历自校验", OK if same else BAD,
                     f"{len(official)} 条 / 覆盖 {payload.get('first')}~{payload.get('last')} / sha {dig[:12]}"))
    except Exception as e:  # noqa: BLE001 - 预检要把真实异常摆出来
        rows.append(("C1 日历自校验", BAD, f"{type(e).__name__}: {e}"))
        return rows, 1
    if not same:
        return rows, 1

    # C2 目标月覆盖
    try:
        tcal.ensure_covered(official, today[:6])
        rows.append(("C2 日历覆盖目标月", OK, f"{today[:6]} 完整覆盖"))
    except Exception as e:
        rows.append(("C2 日历覆盖目标月", BAD, str(e)))

    # C3 决策日推导（调生产纯函数，不用自己实现）
    try:
        import p13_forward as pf
        me, why, err = pf.resolve_decision_day(today, official, data_last,
                                               check_window=True)
        # ⚠️ 不能拿「today 所在月的月末」做期望：12-01 补跑时决策日是**上个月**月末。
        # 不变式应是「返回的日期是它自己那个月的最后一个交易日」。
        is_month_end = bool(me) and me in official and me == tcal.last_trading_day(me[:6], official)
        good = (is_month_end and err is None)
        rows.append(("C3 决策日推导", OK if good else BAD,
                     f"推导 {me}｜是{me[:6] if me else '?'}月末={is_month_end}｜"
                     f"{type(err).__name__ if err else '无异常'}｜{why[:60]}"))
        covered = bool(me) and data_last >= me
        rows.append(("C4 数据末日覆盖", OK if covered else BAD,
                     f"data_last={data_last} vs 决策日 {me}"
                     f"（模拟值，11-30 当天须用真实全池末日重跑）"))
    except Exception as e:
        rows.append(("C3 决策日推导", BAD, f"{type(e).__name__}: {e}"))
        me = None

    # C5 台账不存在（存在 ⇒ NOT_DUE，本期不会产出 FORWARD）
    ledger_day = me or tcal.last_trading_day(today[:6], official)
    ledger = FWD / "ledger" / f"{ledger_day}.json"
    rows.append(("C5 目标台账未落盘", OK if not ledger.exists() else BAD,
                 f"{ledger.name} {'已存在⇒当天会 NOT_DUE' if ledger.exists() else '不存在⇒可 DUE'}"))

    # C6 timer 覆盖
    ok6, why6 = timer_covers(today, official)
    rows.append(("C6 定时器会触发", OK if ok6 else BAD, why6))

    # C7 补跑窗口余量
    try:
        import p13_forward as pf
        rows.append(("C7 补跑窗口", OK,
                     f"LATE_RUN ≤{pf.MAX_LATE_RUN_DAYS} 天 / 决策窗口 ≤{pf.MAX_DECISION_AGE_DAYS} 天；"
                     f"11-30 当天 gap=0"))
    except Exception:
        pass

    # C8 d4 字段产出路径 + 快照时点
    src = (SP5 / "p13_forward.py").read_text(encoding="utf-8")
    has_d4 = 'out[f"target_holds_{key}"] = list(top["code"])' in src
    rows.append(("C8a d4 字段写入路径", OK if has_d4 else BAD,
                 "p13_forward 含 target_holds_d4 赋值" if has_d4 else "源码未找到赋值，须核对"))
    snap = FWD / "universe_state" / f"{today}.json"
    if snap.is_file():
        st = json.loads(snap.read_text(encoding="utf-8"))
        asof = str(st.get("asof") or "")[:8].replace("-", "")
        ok8 = asof == today
        rows.append(("C8b 快照时点", OK if ok8 else BAD,
                     f"{snap.name} asof={asof}（须 == 决策日）"))
    else:
        rows.append(("C8b 快照时点", PEND,
                     f"{today}.json 当日由 p17 生成；**17:10 后必须重跑本预检**。"
                     "快照失败不中断链路，台账会沿用最近可用快照 ⇒ 时点证据不新鲜"))

    # C9 ADJ 重建链（决策分支会重建并原子替换 qfq_all）
    r = REPO / "scripts" / "rebuild_adj_from_exec.py"
    v = REPO / "scripts" / "verify_qfq_rebuild.py"
    chain = (REPO / "deploy" / "bin" / "daily_chain.sh").read_text(encoding="utf-8")
    ok9 = r.is_file() and v.is_file() and "--div-source merge" in chain
    rows.append(("C9 ADJ 重建+校验链", OK if ok9 else BAD,
                 "rebuild / verify 两脚本齐 + --div-source merge；"
                 "任一失败 ⇒ ABORT_QFQ_*，本期不决策"))

    # C10 本地快照镜像
    mir = FWD / "universe_state_mirror.json"
    if mir.is_file():
        m = json.loads(mir.read_text(encoding="utf-8"))
        rows.append(("C10 快照镜像", OK if m.get("all_identical") else BAD,
                     f"{len(m.get('files', {}))} 文件 / all_identical={m.get('all_identical')}"))
    else:
        rows.append(("C10 快照镜像", BAD, "缺 universe_state_mirror.json（跑 check_universe_mirror.py）"))

    # C11 部署可证性（R6-3）：本地文件必须与 manifest 逐位一致；
    # 云端一致性默认 PENDING（离线预检拿不到云端哈希，绝不假装 PASS），
    # 加 --cloud 才实检；11-27 演练与 11-30 17:10 后各跑一次。
    try:
        rec = dm.load_manifest()
        ok11, why11, diffs11 = dm.verify_local(rec)
        rows.append(("C11a 本地部署与清单", OK if ok11 else BAD,
                     f"commit {str(rec.get('commit',''))[:8]}｜{why11}｜差异 {len(diffs11)}"))
    except Exception as e:
        rec = None
        rows.append(("C11a 本地部署与清单", BAD,
                     f"{type(e).__name__}: {e}（先跑 deploy_manifest.py --write）"))
    if getattr(args, "cloud", False) and rec is not None:
        okc, whyc, diffc = dm.verify_remote(rec)
        rows.append(("C11b 云端 /opt/quant 一致", OK if okc else BAD,
                     whyc + (f"｜{diffc[0]}" if diffc else "")))
    else:
        rows.append(("C11b 云端 /opt/quant 一致", PEND,
                     "须加 --cloud 或由 11-27 演练/11-30 17:10 后在云端实检；"
                     "未检 ≠ 一致（/opt/quant 非 git 工作副本）"))

    n_bad = sum(1 for _, st, _ in rows if st == BAD)
    return rows, n_bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="决策日只读前置预检")
    ap.add_argument("--today", default="20261130")
    ap.add_argument("--data-last", default=None,
                    help="模拟的数据末日；不给则等于 today。11-30 当天须重跑并核对真实值")
    ap.add_argument("--calendar", default=None)
    ap.add_argument("--cloud", action="store_true",
                    help="R6-3：额外 ssh 到云端比对部署文件 sha256（默认只判 PENDING）")
    args = ap.parse_args(argv)

    print("=" * 78)
    print(f"决策日只读预检（不写任何文件）｜today={args.today} "
          f"data_last={args.data_last or args.today}")
    print("=" * 78)
    rows, n_bad = check(args)
    for name, st, why in rows:
        print(f"  [{st:7s}] {name:22s} {why}")
    n_pend = sum(1 for _, st, _ in rows if st == PEND)
    print("-" * 78)
    print(f"结论：PASS {sum(1 for _, s, _ in rows if s == OK)}｜"
          f"FAIL {n_bad}｜PENDING {n_pend}")
    if n_pend:
        print("⚠️ PENDING 项须在 11-30 17:10 之后重跑本脚本才能判定，不得提前当 PASS。")
    print("[PREFLIGHT] 未写台账、未发单、未改配置。")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
