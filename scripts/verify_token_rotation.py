"""R6-6：token 轮换验收（**全程不打印、不回显、不记录任何凭证值**）。

为什么要封存旧值
----------------
官网重新生成 token 后旧值自动失效。要证明「旧 token 确实失败了」，必须还能
拿旧值调一次只读探针；但 R6-6 禁止凭证再出现在对话 / 报告 / 日志里，所以
唯一可行路径是在**轮换前**把旧值封存到云端一个 600 的临时文件（不进 git、
不进任何输出），轮换后用它跑一次探针，然后**立即删除**。

全程只输出 sha256 前 8 位指纹。子进程 stdout/stderr **不回显**（SDK 异常可能
带凭证原文），只报返回码。

必须在**云主机**上由用户本人或 agent 在用户告知「已轮换」之后运行；
本脚本现在**不执行任何轮换动作**。

流程：
    python scripts/verify_token_rotation.py --prepare     # 轮换前，封存旧值
    （用户去官网「个人中心 → 安全设置」重新生成 token，更新 /opt/quant/env）
    python scripts/verify_token_rotation.py --check       # 旧必须失败、新必须成功
    python scripts/verify_token_rotation.py --audit-logs  # 确认日志里没有凭证值
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FWD = REPO / "research" / "stock_pick_2026-10-25" / "forward"
CALENDAR = FWD / "trade_calendar.json"
SEALED = REPO / "var" / "tmp" / "old_token.sealed"
ENV_FILE = REPO / "env"
ADAPTER = REPO / "scripts" / "paper_account_adapter.py"
DEFAULT_PY = REPO / ".venv-gm" / "bin" / "python"
ENV_TOKEN = "DQ_GM_TOKEN"

EXIT_OK, EXIT_FAIL = 0, 1


class RotationError(RuntimeError):
    pass


def fp(value: str) -> str:
    """不可逆指纹，只用于确认「两个值不同」。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def read_env_token(env_path: Path = ENV_FILE) -> str:
    if not env_path.is_file():
        raise RotationError(f"缺 env 文件 {env_path}（本脚本须在云主机 /opt/quant 运行）")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(ENV_TOKEN + "="):
            v = line.split("=", 1)[1].strip().strip('"\'')
            if v:
                return v
    raise RotationError(f"env 里没有 {ENV_TOKEN}（只报变量名，不报值）")


def probe_day(today: str | None = None) -> str:
    """取**交易日**做探针：否则「非交易日失败」会被误当成「旧 token 失败」。"""
    import datetime as dt
    day = today or dt.date.today().strftime("%Y%m%d")
    days = [str(x) for x in json.loads(CALENDAR.read_text(encoding="utf-8"))["dates"]]
    past = [d for d in days if d <= day]
    if not past:
        raise RotationError("交易日历不可用或无历史交易日 ⇒ 失败关闭")
    return past[-1]


def run_probe(token: str, python: Path, day: str,
              *, runner: Any = None) -> int:
    """用指定 token 调只读 --probe。**不回显任何子进程输出**。"""
    env = dict(os.environ)
    env[ENV_TOKEN] = token
    # ⚠️ 账户 ID 必须保持不变：两次探针唯一差异只能是 token。
    # pop 掉它会让「新值 probe」因缺账户 ID 失败，变成假 FAIL。
    cmd = [str(python), str(ADAPTER), "--probe", "--date", day]
    fn = runner or subprocess.run
    try:
        r = fn(cmd, env=env, capture_output=True, text=True, timeout=90)
        return int(getattr(r, "returncode", -1))
    except Exception:
        return -1


def prepare(env_path: Path = ENV_FILE, sealed: Path = SEALED) -> int:
    token = read_env_token(env_path)
    if sealed.is_file():
        print("[rotate] ✗ 封存文件已存在——先 --check 或手工确认后再 prepare，避免静默覆盖")
        return EXIT_FAIL
    sealed.parent.mkdir(parents=True, exist_ok=True)
    sealed.write_text(token, encoding="utf-8", newline="\n")
    try:
        os.chmod(sealed, 0o600)
    except OSError:
        pass  # Windows 上 chmod 无效；Linux 云端才用
    print(f"[rotate] 已封存旧值｜指纹 {fp(token)}｜文件属主权限 600（值未输出）")
    print("[rotate] 现在去官网重新生成 token 并更新 env，然后跑 --check")
    return EXIT_OK


def check(env_path: Path = ENV_FILE, sealed: Path = SEALED,
          python: Path = DEFAULT_PY, *, runner: Any = None,
          today: str | None = None) -> int:
    if not sealed.is_file():
        print("[rotate] ✗ 缺封存文件：须先在轮换前跑 --prepare ⇒ 失败关闭")
        return EXIT_FAIL
    old = sealed.read_text(encoding="utf-8").strip()
    try:
        new = read_env_token(env_path)
    except RotationError as exc:
        print(f"[rotate] ✗ {exc}")
        return EXIT_FAIL
    if not old or not new or fp(old) == fp(new):
        print("[rotate] ✗ 新旧指纹相同（或为空）⇒ token 未轮换，判 FAIL")
        return EXIT_FAIL
    try:
        day = probe_day(today)
    except Exception as exc:
        print(f"[rotate] ✗ 交易日判定失败：{type(exc).__name__} ⇒ 失败关闭")
        return EXIT_FAIL

    old_rc = run_probe(old, python, day, runner=runner)
    new_rc = run_probe(new, python, day, runner=runner)
    ok = (old_rc != 0 and new_rc == 0)
    print(f"[rotate] 交易日 {day}｜旧值指纹 {fp(old)} probe rc={old_rc}（须≠0）"
          f"｜新值指纹 {fp(new)} probe rc={new_rc}（须=0）｜{ 'PASS' if ok else 'FAIL' }")
    print("[rotate] 子进程输出未回显（防止 SDK 异常携带凭证）；只报 rc")
    if ok:
        sealed.unlink()
        print("[rotate] 已删除封存文件")
    else:
        print("[rotate] ⚠️ 未通过：封存文件保留以便排查；不得据此外报『轮换完成』")
    return EXIT_OK if ok else EXIT_FAIL


def audit_logs(env_path: Path = ENV_FILE, sealed: Path = SEALED,
               *, runner: Any = None) -> int:
    """只报命中数，不打印凭证值、不把值放进命令行。新旧一起扫。"""
    values = [read_env_token(env_path)]
    if sealed.is_file():
        values.append(sealed.read_text(encoding="utf-8").strip())
    fn = runner or subprocess.run
    texts: list[str] = []
    try:
        r = fn(["journalctl", "-u", "deepquant-daily", "--since", "2026-11-01", "--no-pager"],
               capture_output=True, text=True, timeout=60)
        texts.append(getattr(r, "stdout", "") or "")
    except Exception:
        return EXIT_FAIL
    logdir = REPO / "var" / "log"
    for p in sorted(logdir.glob("*.log"))[:200]:
        try:
            texts.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    hits = sum(t.count(v) for v in values if v for t in texts)
    print(f"[rotate] 日志审计｜扫描 {len(texts)} 个来源 / {len(values)} 个凭证"
          f"｜命中 {hits} 处｜{ 'PASS' if hits == 0 else 'FAIL' }（只报数量，不报值）")
    return EXIT_OK if hits == 0 else EXIT_FAIL


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R6-6 token 轮换验收（不输出凭证值）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prepare", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--audit-logs", action="store_true")
    ap.add_argument("--today", default=None, help="测试用，指定判定日（取最近交易日）")
    ap.add_argument("--env", default=None)
    ap.add_argument("--sealed", default=None)
    ap.add_argument("--python", default=None)
    args = ap.parse_args(argv)
    env_path = Path(args.env) if args.env else ENV_FILE
    sealed = Path(args.sealed) if args.sealed else SEALED
    py = Path(args.python) if args.python else DEFAULT_PY
    try:
        if args.prepare:
            return prepare(env_path, sealed)
        if args.check:
            return check(env_path, sealed, py, today=args.today)
        return audit_logs(env_path, sealed)
    except RotationError as exc:
        print(f"[rotate] ✗ {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
