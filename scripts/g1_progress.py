"""G1「连续 10 个交易日零漏跑」只读统计 —— D3 严格口径（R8 §1，2026-11-15）。

## D3 口径（**预先声明，不得事后调整**）

- **不合并两机**：严格口径只在**新机（记录机）**上计，起点
  ``D = 2026-11-12``（迁移与 R7 上线后首个全绿交易日）。
  旧机 11-03~11-11 仅作历史，**不并入**严格口径。
- **数据源改为新机产物**：按日日志及归档 + ``forward/heartbeat.json`` +
  ``forward/stage_results.json``。⚠️ 原实现只读 ``journalctl -u deepquant-daily``，
  而换机后新机该 unit 的 journal 没有 ``daily_chain 结束``／``[heartbeat]`` 行
  ⇒ 现报 0/10 是**数据源失效**，不是「真的漏跑」。
- **任何红日重置计数**：从 D 起逐交易日数连续段，遇红日归零重起，不跨红累加。
- **判定日写成条件式**：若此后全绿，则 **2026-11-25 收盘后判**（D 起第 10 个交易日）；
  出现红日则顺延、**不预先给新日期**。STATUS.md 只保留这一个判定日。

## 单文件产物的已知限制（如实登记，不伪造历史）

``heartbeat.json`` / ``stage_results.json`` 是**最新一日**的单文件，新机没有按日归档。
因此：产物文件只对**其 ts 对应的那一天**施加「心跳 ok + 阶段结果 ok」的额外校验；
D 到该日之前的历史交易日以按日日志（含 ``.log.1`` / ``.log-YYYYMMDD`` 归档）为准。
本脚本**不回填、不伪造**任何历史 stage_results 文件。读不到两个产物 ⇒ **FAIL**（失败关闭），
不是「0 天继续观察」。

## 用法

    python scripts/g1_progress.py                       # D3 严格口径（本地跑，ssh 读新机）
    python scripts/g1_progress.py --local               # 在新机自己上面跑：直读本机产物
    python scripts/g1_progress.py --state-file F.json   # 离线复算（审查用，不联网）
    python scripts/g1_progress.py --journal-file J.log  # 旧机历史口径离线复算（不对外引用）
    python scripts/g1_progress.py --today 20261130

⚠️ **只读统计**：不写台账、不补心跳、不 ping、不合并旧机数据。
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FWD = REPO / "research" / "stock_pick_2026-10-25" / "forward"

#: 新机（记录机），D1/D2 迁移后固定；旧机密钥已不在册。
HOST = "root@1.92.141.140"
KEY = Path.home() / ".ssh" / "fk_server_ed25519"

NEED = 10
#: ⭐⭐ D3 严格口径起点，**R8 预先声明，不得事后调整**。
#: main() 启动校验：--start 早于本值 ⇒ FAIL；日历第 10 个交易日 ≠ DECISION_DAY ⇒ FAIL。
START = "20261112"
#: 条件式判定日：若 D 起此后全绿，则该日收盘后判。
DECISION_DAY = "20261125"
#: 展示口径与 R8 原文一致（启动校验收由独立字符串算术核对，不按星期几猜）。
DECISION_DAY_DISPLAY = "2026-11-25"
#: 旧机口径起点，仅供 --journal-file 历史离线复算（不并入严格口径、不对外引用）。
HISTORICAL_START = "20261103"

_RE_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T")
_RE_CHAIN = re.compile(r"=== daily_chain 结束 mode=\S+ ok=1")
_RE_HB = re.compile(r"\[heartbeat\].*ok=True")
_RE_DAY8 = re.compile(r"^\d{8}$")

#: 远端只读读取器：只列日志归档与两个产物文件，不写任何东西、不回显凭证。
#: 通过 base64 传递，避开 ssh 多层引号；输出**最后一行**是 JSON。
REMOTE_READER = r'''
import glob, json, os, pathlib, re
REPO = "/opt/quant"
FWD = REPO + "/research/stock_pick_2026-10-25/forward"
LOGD = REPO + "/var/log"
out = {"host": REPO, "chain_days": [], "hb_days": [],
       "heartbeat": None, "stage_results": None, "errors": []}
chain, hb = set(), set()
name_re = re.compile(r"daily_(\d{8})\.log")
for p in sorted(glob.glob(LOGD + "/daily_*.log*")):
    m = name_re.search(os.path.basename(p))
    if not m:
        continue
    day = m.group(1)
    try:
        txt = pathlib.Path(p).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        out["errors"].append(os.path.basename(p) + ":" + type(e).__name__)
        continue
    if re.search(r"=== daily_chain 结束 mode=\S+ ok=1", txt):
        chain.add(day)
    if re.search(r"\[heartbeat\].*ok=True", txt):
        hb.add(day)
out["chain_days"] = sorted(chain)
out["hb_days"] = sorted(hb)
for name, key in (("heartbeat.json", "heartbeat"),
                  ("stage_results.json", "stage_results")):
    try:
        out[key] = json.loads((pathlib.Path(FWD) / name).read_text(encoding="utf-8"))
    except Exception as e:
        out["errors"].append(name + ":" + type(e).__name__)
print(json.dumps(out, ensure_ascii=False))
'''


def official_days() -> list[str]:
    payload = json.loads((FWD / "trade_calendar.json").read_text(encoding="utf-8"))
    return [str(x) for x in payload["dates"]]


def decision_day(trading: list[str], start: str = START,
                 need: int = NEED) -> str | None:
    """从起点起第 need 个交易日。独立算术，不调用被测函数。"""
    days = [d for d in trading if d >= start]
    return days[need - 1] if len(days) >= need else None


def parse_journal(text: str) -> tuple[set[str], set[str]]:
    """历史口径：从 ``journalctl --output=short-iso`` 抽链路 ok 与心跳 ok 的日期。"""
    chain: set[str] = set()
    hb: set[str] = set()
    for line in text.splitlines():
        m = _RE_DAY.match(line)
        if not m:
            continue
        day = "".join(m.groups())
        if _RE_CHAIN.search(line):
            chain.add(day)
        if _RE_HB.search(line):
            hb.add(day)
    return chain, hb


def effective_upto(today: str, now: dt.datetime, trading: list[str]) -> tuple[str, str]:
    """当日 17:10 未到时，不把今天算「漏跑」——只是还没到执行时点。"""
    d = dt.date(int(today[:4]), int(today[4:6]), int(today[6:]))
    cutoff = dt.datetime.combine(d, dt.time(17, 10))
    if today in trading and now < cutoff:
        prev = [x for x in trading if x < today]
        return (prev[-1] if prev else ""), "今日 17:10 未到，不计入"
    return today, ""


def count_streak(trading: list[str], good: set[str], upto: str,
                 *, start: str = START) -> tuple[int, list[str], str | None]:
    """从 upto 往前数**连续**交易日，遇到第一个没跑过的日子就停（红日重置）。

    返回 (连续数, 构成该连续段的日期, 第一个断点的日期)。
    ⚠️ 不得跨断点累加：断点之后即使又跑了，也只数断点之后的连续段。
    """
    days = [d for d in trading if start <= d <= upto]
    streak: list[str] = []
    for d in reversed(days):
        if d not in good:
            streak.reverse()
            return len(streak), streak, d
        streak.append(d)
    streak.reverse()
    return len(streak), streak, None


def fetch_journal(since: str = "2026-11-01", timeout: int = 45) -> str:
    """历史口径：拉 journalctl（仅显式指定时使用）。"""
    cmd = ["ssh", "-i", str(KEY), "-o", "StrictHostKeyChecking=no",
           "-o", f"ConnectTimeout={timeout}", HOST,
           f"journalctl -u deepquant-daily --since {since} --output=short-iso --no-pager"]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
    if cp.returncode != 0:
        raise RuntimeError(f"取日志失败 rc={cp.returncode}: {cp.stderr[-200:]}")
    return cp.stdout


def fetch_remote_state(timeout: int = 45, local: bool = False) -> dict:
    """D3 默认数据源：ssh 到**新机**只读读取按日日志归档 + 心跳 + 阶段结果。

    ``local=True`` 供**在新机上自跑**时使用：直接读本机文件、不再 ssh
    （新机 /root/.ssh 没有本地那把密钥；ssh 到自己会失败）。读的仍是同一台
    记录机上的产物，口径不变。
    """
    reader = REMOTE_READER
    if local:
        reader = reader.replace('REPO = "/opt/quant"',
                                f"REPO = {str(REPO)!r}", 1)
    payload = base64.b64encode(reader.encode("utf-8")).decode("ascii")
    inner = ("import base64;exec(base64.b64decode('" + payload
             + "').decode('utf-8'))")
    if local:
        cmd = [sys.executable, "-c", inner]
    else:
        remote = 'python3 -c "' + inner + '"'
        cmd = ["ssh", "-i", str(KEY), "-o", "BatchMode=yes",
               "-o", f"ConnectTimeout={timeout}", HOST, remote]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
    if cp.returncode != 0:
        raise RuntimeError(f"读新机产物失败 rc={cp.returncode}: {cp.stderr[-200:]}")
    lines = [x for x in cp.stdout.strip().splitlines() if x.strip()]
    if not lines:
        raise RuntimeError("远端读取器无输出——不得当成 0 天继续观察")
    return json.loads(lines[-1])


def artifact_day(doc: dict | None) -> str | None:
    """取产物 ts 的 YYYYMMDD；不可解析返回 None（调用方失败关闭）。"""
    if not isinstance(doc, dict):
        return None
    day = str(doc.get("ts") or "")[:10].replace("-", "")
    return day if _RE_DAY8.match(day) else None


def build_days(state: dict, trading: list[str], start: str) -> tuple[set[str], list[str]]:
    """D3 判据：按日日志（链路 ok ∧ 心跳 ok）∧ 产物文件当日额外校验。

    返回 (全绿日集合, 说明)。⚠️ ``state['errors']`` 非空 ⇒ 调用方必须 FAIL。
    """
    chain = set(state.get("chain_days") or [])
    hb = set(state.get("hb_days") or [])
    good = {d for d in trading if d >= start and d in chain and d in hb}
    notes: list[str] = []

    hbdoc, srdoc = state.get("heartbeat"), state.get("stage_results")
    d_hb, d_sr = artifact_day(hbdoc), artifact_day(srdoc)
    if d_hb is None or d_sr is None:
        raise ValueError("心跳/阶段结果产物 ts 缺失或不可解析 ⇒ 失败关闭")
    if d_hb not in trading or d_sr not in trading:
        raise ValueError("心跳/阶段结果产物日期不在交易日历 ⇒ 失败关闭")
    if d_hb != d_sr:
        notes.append(f"⚠️ 产物日期不一致：heartbeat={d_hb} / stage_results={d_sr}")

    # ⭐ 读取新机心跳与阶段结果：产物对应日必须两者都 ok，否则该日判红。
    hb_ok = isinstance(hbdoc, dict) and hbdoc.get("ok") is True
    sr_ok = (isinstance(srdoc, dict) and srdoc.get("ok") is True
             and not str(srdoc.get("stage_note") or "").strip())
    if not (hb_ok and sr_ok):
        good.discard(d_hb)
        good.discard(d_sr)
        notes.append(f"⛔ {d_hb}/{d_sr} 产物校验不通过 ⇒ 该日判红（重置计数）")
    else:
        notes.append(f"产物校验：{d_hb} heartbeat.ok=True / stage_results.ok=True")

    hist = [d for d in sorted(good) if d < min(d_hb, d_sr)]
    if hist:
        notes.append("历史交易日（无按日 stage_results 归档）按日志判定：" + ",".join(hist))
    return good, notes


def _report(today: str, upto: str, note: str, good: set[str], k: int,
            days: list[str], broken: str | None, notes: list[str],
            *, legacy: bool = False) -> None:
    tag = "LEGACY_HISTORICAL_ONLY｜旧机口径，不并入严格 G1、不对外引用" if legacy else "D3 严格口径｜仅新机"
    print(f"[G1] 判定日 {today}｜有效截至 {upto}｜{note or '今日已过 17:10'}｜{tag}")
    print(f"      全绿日 {len(good)} 天：{sorted(good)}")
    print(f"      **G1 = {min(k, NEED)}/{NEED}**｜连续段 {days}")
    for n in notes:
        print(f"      {n}")
    if broken:
        print(f"      断点：{broken}（红日 ⇒ 连续计数从这里重置，不跨断点累加）")
    if k >= NEED:
        print("[G1] PASS 连续 10 个交易日零漏跑")
    elif broken is None and not legacy:
        print(f"[G1] **条件式判定日**：若此后全绿，则 {DECISION_DAY_DISPLAY} 收盘后判"
              f"（D={START} 起第 {NEED} 个交易日）")
        print("      ⚠️ 该日期是条件结论；一旦出现红日即失效，不预先给新日期。")
    elif legacy:
        print("[G1] 历史口径离线复算（仅用于核对旧记录，不作判定）")
    else:
        print("[G1] 出现红日 ⇒ 计数已重置；判定日顺延，**不预先给新日期**")
    print("[G1] 只读统计：未写台账、未补心跳、未 ping、未合并旧机。")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="G1 严格口径（D3）连续交易日零漏跑统计（只读）")
    ap.add_argument("--today", default=dt.date.today().strftime("%Y%m%d"))
    ap.add_argument("--journal-file", default=None,
                    help="旧机历史口径离线复算；标注 LEGACY，不并入严格 G1")
    ap.add_argument("--state-file", default=None,
                    help="D3 离线复算：读 fetch_remote_state() 产出的 JSON")
    ap.add_argument("--local", action="store_true",
                    help="在新机（记录机）上自跑时直读本机文件，不再 ssh")
    ap.add_argument("--start", default=None,
                    help="D3 默认 START=20261112；早于 START 一律 FAIL（不得事后调整）")
    args = ap.parse_args(argv)

    try:
        trading = official_days()
    except Exception as e:  # noqa: BLE001
        print(f"[G1] FAIL 交易日历不可用：{e}")
        return 2

    # ⭐ 启动校验：冻结的判定日必须由**独立交易日历**推出，不按星期几猜。
    if decision_day(trading) != DECISION_DAY:
        print(f"[G1] FAIL 日历第 {NEED} 个交易日 ≠ 冻结判定日 {DECISION_DAY} ⇒ 失败关闭")
        return 2
    # 独立字符串算术：展示口径必须与日历常量一致，避免「两个判定日」。
    if DECISION_DAY_DISPLAY != (DECISION_DAY[:4] + "-" + DECISION_DAY[4:6]
                                + "-" + DECISION_DAY[6:]):
        print("[G1] FAIL 判定日展示格式与日历常量不一致 ⇒ 失败关闭")
        return 2

    # ---------- 旧机历史口径（离线，仅复算，不对外引用） ----------
    if args.journal_file:
        try:
            text = Path(args.journal_file).read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[G1] FAIL 拿不到历史日志：{type(e).__name__}: {e}")
            return 2
        if not text.strip():
            print("[G1] FAIL 日志为空——不得把「没数据」当成「0 天继续观察」")
            return 2
        chain, hb = parse_journal(text)
        good = chain & hb
        upto, note = effective_upto(args.today, dt.datetime.now(), trading)
        if not upto:
            print("[G1] FAIL 起算日之前没有交易日可判")
            return 2
        k, days, broken = count_streak(trading, good, upto,
                                       start=args.start or HISTORICAL_START)
        _report(args.today, upto, note, good, k, days, broken, [], legacy=True)
        return 0

    # ---------- D3 严格口径 ----------
    if args.start is not None and args.start < START:
        print(f"[G1] FAIL 严格口径起点 D={START} 已预先声明，不得事后调整（收到 {args.start}）")
        return 2

    try:
        state = (json.loads(Path(args.state_file).read_text(encoding="utf-8"))
                 if args.state_file else fetch_remote_state(local=args.local))
    except Exception as e:  # noqa: BLE001
        print(f"[G1] FAIL 拿不到新机心跳/阶段结果：{type(e).__name__}: {e}")
        return 2
    if not isinstance(state, dict):
        print("[G1] FAIL 远端状态不是对象 ⇒ 失败关闭")
        return 2
    if state.get("errors"):
        print(f"[G1] FAIL 新机产物读取有缺口：{state['errors']} ⇒ 不得当成 0 天继续观察")
        return 2
    if not (state.get("chain_days") or state.get("hb_days")):
        print("[G1] FAIL 新机按日日志无有效记录 ⇒ 失败关闭")
        return 2

    try:
        good, notes = build_days(state, trading, START)
    except ValueError as e:
        print(f"[G1] FAIL {e}")
        return 2

    upto, note = effective_upto(args.today, dt.datetime.now(), trading)
    if not upto:
        print("[G1] FAIL 起算日之前没有交易日可判")
        return 2
    k, days, broken = count_streak(trading, good, upto, start=START)
    _report(args.today, upto, note, good, k, days, broken, notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
