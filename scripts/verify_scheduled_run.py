#!/usr/bin/env python3
"""监督执行机上某个 systemd 定时任务"是否真的跑过"。

设计判据（R2/R3）：
- 以"留下执行痕迹"为准，而不是"没有报错"。11-23 的教训是 timer 压根没触发，
  此时 OnFailure 不会响，只看日志会得到"一切正常"的假象。
- 因此 PASS 必须同时满足：timer 在窗口内触发过 + service 结果为 success/exit 0
  + 产物文件在窗口之后落盘。任一不成立即 FAIL，并为每项标注证据。
- 窗口由调用方传入，不由"当前时间"或数据末日反推（禁止取机器当前时间当决策依据）。

退出码：0=PASS 1=FAIL 2=PENDING(窗口未到/尚未触发) 3=连接失败
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

HOST = "root@1.92.141.140"
KEY = Path.home() / ".ssh" / "fk_server_ed25519"
REPO = "/opt/quant"
CST = dt.timezone(dt.timedelta(hours=8))


def ssh(script: str, timeout: int = 30) -> tuple[int, str]:
    cmd = [
        "ssh", "-i", str(KEY), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=12", HOST, script,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "ssh timeout"
    except OSError as exc:
        return 125, f"ssh failed: {exc}"


def parse_window(text: str) -> tuple[int, int]:
    """'09:30-09:55' -> (570, 595) 分钟数；跨零点不支持，也不该用。"""
    start, _, end = text.partition("-")
    def to_min(s: str) -> int:
        h, m = s.strip().split(":")
        return int(h) * 60 + int(m)
    a, b = to_min(start), to_min(end)
    if b <= a:
        raise SystemExit(f"窗口非法（结束须晚于开始）: {text}")
    return a, b


def probe(unit: str, artifact: str | None, day: str, start_min: int, end_min: int) -> dict:
    """一次 SSH 拿全部原始事实，判定留在本地做，避免在远端写判断逻辑。"""
    remote = f"""
set -u
echo "TIMER_ACTIVE=$(systemctl show {unit}.timer -p ActiveState --value 2>/dev/null)"
echo "TIMER_TRIGGER=$(systemctl show {unit}.timer -p LastTriggerUSec --value 2>/dev/null)"
echo "TIMER_NEXT=$(systemctl show {unit}.timer -p NextElapseUSecRealtime --value 2>/dev/null)"
echo "TIMER_RESULT=$(systemctl show {unit}.timer -p Result --value 2>/dev/null)"
echo "SVC_RESULT=$(systemctl show {unit}.service -p Result --value 2>/dev/null)"
echo "SVC_STATUS=$(systemctl show {unit}.service -p ExecMainStatus --value 2>/dev/null)"
echo "SVC_EXIT_AT=$(systemctl show {unit}.service -p ExecMainExitTimestamp --value 2>/dev/null)"
echo "SVC_INVOC=$(systemctl show {unit}.service -p InvocationID --value 2>/dev/null)"
"""
    if artifact:
        remote += f'echo "ART_STAT=$(stat -c \'%y|%s\' {REPO}/{artifact} 2>/dev/null || echo MISSING)"\n'
        remote += f'echo "ART_HEAD=$(head -c 600 {REPO}/{artifact} 2>/dev/null | tr \'\\n\' \' \')"\n'
    remote += f'echo "JOURNAL_BEGIN"\nsudo -u deepquant journalctl -u {unit}.service --since "{day} 00:00" --no-pager -n 40 2>/dev/null | tail -20\n'

    rc, out = ssh(remote)
    facts: dict[str, str] = {}
    journal: list[str] = []
    in_journal = False
    for line in out.splitlines():
        if line.strip() == "JOURNAL_BEGIN":
            in_journal = True
            continue
        if in_journal:
            journal.append(line)
        elif "=" in line:
            k, _, v = line.partition("=")
            facts[k.strip()] = v.strip()
    return {"rc": rc, "raw": out, "facts": facts, "journal": journal}


def parse_systemd_time(value: str) -> dt.datetime | None:
    """systemctl 输出如 'Tue 2026-11-24 09:35:00 CST'；n/a 或空返回 None。

    ⚠️ 两种 strptime 陷阱都踩过：%Z 在 Windows 下不认 'CST'；%a 是 locale 相关的，
    中文 locale 里 'Mon' 解析不出来。两者都会让结果变成 None，被当成"从未触发"，
    把真跑过的任务误判成 FAIL。故直接正则提取日期时间，绕开 locale 与缩写。
    """
    v = (value or "").strip()
    if not v or v.lower().startswith("n/a"):
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", v)
    if not m:
        return None
    parts = [int(x) for x in m.groups()]
    return dt.datetime(*parts, tzinfo=CST)


def evaluate(args) -> tuple[str, list[dict], str]:
    start_min, end_min = parse_window(args.window)
    day_start = dt.datetime.strptime(args.day, "%Y-%m-%d").replace(tzinfo=CST)
    window_open = day_start + dt.timedelta(minutes=start_min)
    window_close = day_start + dt.timedelta(minutes=end_min)

    data = probe(args.unit, args.artifact, args.day, start_min, end_min)
    if data["rc"] != 0:
        return "UNREACHABLE", [{"check": "ssh", "ok": False,
                                  "detail": f"rc={data['rc']} {data['raw'][:200]}"}], ""

    f = data["facts"]
    checks: list[dict] = []

    # C1 timer 在窗口内触发过（这是 11-23 缺的那一环）
    trigger = parse_systemd_time(f.get("TIMER_TRIGGER", ""))
    if trigger is None:
        checks.append({"check": "timer_fired", "ok": False,
                       "detail": f"LastTriggerUSec={f.get('TIMER_TRIGGER','?')}（从未触发）"})
    elif not (window_open <= trigger <= window_close):
        checks.append({"check": "timer_fired", "ok": False,
                       "detail": f"触发于 {trigger:%Y-%m-%d %H:%M:%S}，不在窗口 "
                                 f"{window_open:%H:%M}-{window_close:%H:%M} 内"})
    else:
        checks.append({"check": "timer_fired", "ok": True,
                       "detail": f"触发于 {trigger:%Y-%m-%d %H:%M:%S}"})

    # C2 service 成功退出。⚠️ 从未运行过的 service 在 systemd 里默认就是
    # Result=success/ExecMainStatus=0，只看这两项会把"没跑"判成"跑好了"
    # （「没检出」≠「没问题」的同型陷阱）。故必须同时要求 InvocationID 非空。
    svc_result = (f.get("SVC_RESULT") or "").strip()
    svc_status = (f.get("SVC_STATUS") or "").strip()
    invoc = (f.get("SVC_INVOC") or "").strip()
    ok_c2 = bool(invoc) and svc_result == "success" and svc_status == "0"
    detail_c2 = (f"Result={svc_result or '?'} ExecMainStatus={svc_status or '?'} "
                 f"InvocationID={invoc[:8] or '(空=从未运行)'}")
    checks.append({"check": "service_success", "ok": ok_c2, "detail": detail_c2})

    # C3 产物在窗口之后落盘
    if args.artifact:
        stat_val = (f.get("ART_STAT") or "").strip()
        if stat_val == "MISSING" or not stat_val:
            checks.append({"check": "artifact", "ok": False, "detail": f"{args.artifact} 不存在"})
        else:
            stamp = stat_val.split("|")[0]
            size = stat_val.split("|")[-1]
            try:
                mtime = dt.datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
            except ValueError:
                mtime = None
            if mtime is None:
                checks.append({"check": "artifact", "ok": False, "detail": f"mtime 无法解析: {stamp}"})
            else:
                checks.append({"check": "artifact", "ok": mtime >= window_open,
                               "detail": f"{args.artifact} mtime={mtime:%Y-%m-%d %H:%M:%S} size={size}B"})

    # C4 日志里没有硬失败标记（不替代 C1，只是补充证据）
    bad = [ln for ln in data["journal"] if any(k in ln for k in ("Traceback", "HALT", "ERROR", "Failed"))]
    checks.append({"check": "log_clean", "ok": not bad,
                   "detail": "无 Traceback/HALT/ERROR" if not bad else f"{len(bad)} 行异常，见 journal"})

    verdict = "PASS" if all(c["ok"] for c in checks) else "FAIL"

    # 尚未触发且窗口未到 => PENDING，不算失败
    now = dt.datetime.now(CST)
    if verdict == "FAIL" and now < window_close and trigger is None:
        verdict = "PENDING"

    return verdict, checks, (data["facts"].get("ART_HEAD") or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", required=True, help="监督的目标日期 YYYY-MM-DD")
    ap.add_argument("--unit", required=True, help="systemd 单元名（不带 .timer/.service）")
    ap.add_argument("--artifact", default=None, help=f"相对 {REPO} 的产物路径")
    ap.add_argument("--window", default="09:30-09:55", help="触发窗口 HH:MM-HH:MM")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    verdict, checks, art_head = evaluate(args)
    payload = {"verdict": verdict, "day": args.day, "unit": args.unit,
               "checkedAt": dt.datetime.now(CST).isoformat(timespec="seconds"),
               "checks": checks, "artifactHead": art_head}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[{verdict}] {args.unit} ({args.day})")
        for c in checks:
            print(f"  {'OK  ' if c['ok'] else 'BAD '} {c['check']:16s} {c['detail']}")

    return {"PASS": 0, "FAIL": 1, "PENDING": 2}.get(verdict, 3)


if __name__ == "__main__":
    sys.exit(main())
