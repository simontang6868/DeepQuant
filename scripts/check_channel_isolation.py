"""R6-1：生产环境「真实通道凭证 / 适配器」隔离检查（只读）。

为什么单列一个检查器
--------------------
「没检出」不等于「没问题」——本项目已多次踩过断言写宽、空输入恒真的坑。
所以本脚本把**采集**与**判定**分开，并对「采集失败 / 输出为空」一律判
``FAIL``（失败关闭），绝不因为拿不到远端数据就报 PASS。

只输出**文件名与变量名**，不输出任何凭证值；远端采集不回传文件内容。

用法：
    python scripts/check_channel_isolation.py                  # 本地仓库
    python scripts/check_channel_isolation.py --remote         # 云端 /opt/quant
    python scripts/check_channel_isolation.py --root /some/dir # 离线复核
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
REMOTE_HOST = "root@110.40.190.225"
REMOTE_ROOT = "/opt/quant"
SSH_KEY = REPO / "var" / "inbox" / "dq_deploy_ed25519"

OK, BAD, INFO = "PASS", "FAIL", "INFO"

#: 真实通道相关的 env 变量名前缀。LIVE_ENABLED 单独判值，不在此列
#: （它出现且为 false 是正常状态，不该误报）。
FORBIDDEN_ENV_PREFIXES = ("DQ_LIVE_", "QMT_", "BROKER_", "GM_LIVE_", "DQ_BROKER_")

#: 真实通道授权/适配器文件名的特征（大小写不敏感，按子串匹配文件名）。
FORBIDDEN_FILE_TOKENS = ("live_authorization", "live_adapter", "qmt", "mini_qmt",
                         "broker_live", "broker_real", "live.env")

#: broker 包里当前允许的模块。出现任何其它 .py ⇒ 视为真实通道适配器被部署。
ALLOWED_BROKER_MODULES = frozenset(
    {"base.py", "errors.py", "__init__.py", "mock.py", "models.py"})

ADAPTER_REL = "scripts/paper_account_adapter.py"
BROKER_DIR_REL = "backend/src/deepquant/broker"
ENV_REL = "env"

#: 仿真授权文件与所有 forward 证据同目录；真实通道授权另放 /opt/quant/config/
#: （不同目录是 R6-1 的硬要求）。
#: ⚠️ 2026-11-09 修：原实现查 root/"forward"/paper_authorization.json，但部署根下
#: 根本没有 /opt/quant/forward/，项目 forward 目录在 research/.../forward/ ⇒
#: 用户放对位置也会被判「不存在」。单一真相源，本地与远端共用这一条相对路径。
FORWARD_DIR_REL = "research/stock_pick_2026-10-25/forward"
PAPER_AUTH_REL = FORWARD_DIR_REL + "/paper_authorization.json"

#: 只读适配器里绝不允许出现下单调用。docstring 里的 ``order_*`` 不是调用，
#: 故只匹配「order_ + 标识符 + (」。
ORDER_CALL_RE = re.compile(r"\border_[A-Za-z_][A-Za-z0-9_]*\s*\(")


def _names_from_env(text: str) -> list[str]:
    return sorted({m.group(1) for m in
                    re.finditer(r"^\s*([A-Z][A-Z0-9_]*)\s*=", text, re.M)})


def collect_local(root: Path = REPO) -> dict[str, Any]:
    """离线采集：文件清单、env 变量名、broker 模块、适配器源码。"""
    root = Path(root)
    files: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        # 只关心可能与「授权/凭证/适配器」相关的浅层文件，避免扫进 data/ 大目录
        if rel.startswith(("data/", "var/", ".git/", ".workbuddy/")):
            continue
        files.append(rel)

    env_path = root / ENV_REL
    env_present = env_path.is_file()
    env_text = env_path.read_text(encoding="utf-8") if env_present else ""
    env_names = _names_from_env(env_text)
    m = re.search(r"^\s*LIVE_ENABLED\s*=\s*(\S+)", env_text, re.M)
    live_enabled = m.group(1).strip().strip('"\'') if m else None

    bdir = root / BROKER_DIR_REL
    broker_modules = sorted(p.name for p in bdir.glob("*.py")) if bdir.is_dir() else None

    adapter = root / ADAPTER_REL
    adapter_src = adapter.read_text(encoding="utf-8") if adapter.is_file() else None

    paper_auth = root / PAPER_AUTH_REL
    auth_channel = None
    if paper_auth.is_file():
        try:
            auth_channel = json.loads(paper_auth.read_text(encoding="utf-8")).get("channel")
        except Exception:
            auth_channel = "<UNPARSEABLE>"

    return {
        "present": True,
        "files": files,
        "env_names": env_names,
        "env_present": env_present,
        "live_enabled": live_enabled,
        "broker_modules": broker_modules,
        "adapter_src": adapter_src,
        "paper_auth_channel": auth_channel,
    }


#: 远端只读采集：只回文件名 / 变量名 / 计数，不回任何凭证值或文件内容。
REMOTE_SCRIPT = rf'''
echo ---FILES---
find /opt/quant -maxdepth 5 -type f \( \
  -iname "*authorization*" -o -iname "*qmt*" -o -iname "*mini_qmt*" \
  -o -iname "*live_adapter*" -o -iname "*broker_live*" -o -iname "*broker_real*" \
  -o -iname "live.env" \) -printf "%P\n" 2>/dev/null | head -50
echo ---ENV---
sed -E "s/=.*//" /opt/quant/env 2>/dev/null | grep -E "^[A-Z][A-Z0-9_]*$" | sort -u
echo ---LIVE---
sed -nE "s/^LIVE_ENABLED=//p" /opt/quant/env 2>/dev/null | head -1
echo ---BROKER---
for f in /opt/quant/backend/src/deepquant/broker/*.py; do [ -e "$f" ] && basename "$f"; done 2>/dev/null
echo ---ORDER---
grep -cE "\border_[A-Za-z_][A-Za-z0-9_]*\s*\(" /opt/quant/scripts/paper_account_adapter.py 2>/dev/null || echo 0
echo ---AUTH---
[ -f /opt/quant/{PAPER_AUTH_REL} ] && \
  grep -o '"channel"[[:space:]]*:[[:space:]]*"[^"]*"' /opt/quant/{PAPER_AUTH_REL} 2>/dev/null | head -1 || echo NONE
echo ---END---
'''


def collect_remote(timeout: int = 45) -> dict[str, Any]:
    """ssh 采集。失败 / 输出缺结束标记 ⇒ present=False（判 FAIL）。"""
    cmd = ["ssh", "-i", str(SSH_KEY), "-o", "StrictHostKeyChecking=no",
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", REMOTE_HOST,
           REMOTE_SCRIPT]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:  # 失败关闭，不猜
        return {"present": False, "error": type(exc).__name__}
    if out.returncode != 0 or "---END---" not in out.stdout:
        return {"present": False, "error": f"ssh_rc={out.returncode}"}

    sec: dict[str, list[str]] = {}
    cur = ""
    for line in out.stdout.splitlines():
        if line.startswith("---") and line.endswith("---"):
            cur = line.strip("-")
            sec[cur] = []
        elif cur:
            sec[cur].append(line)

    live = (sec.get("LIVE") or [""])[0].strip()
    try:
        order_n = int((sec.get("ORDER") or ["0"])[0].strip())
    except ValueError:
        order_n = -1
    auth_raw = (sec.get("AUTH") or ["NONE"])[0].strip()
    auth_channel = None if auth_raw == "NONE" else auth_raw.split('"')[-2] if '"' in auth_raw else "<UNPARSEABLE>"

    env_names = [x for x in sec.get("ENV", []) if x.strip()]
    if not env_names:
        # 云端 env 里至少有 DQ_GM_* 两个变量；一个都取不到就是采集坏了，
        # 绝不能当成「没有真实通道变量」而判 PASS。
        return {"present": False, "error": "远端 env 变量名为空"}

    return {
        "present": True,
        "files": [x for x in sec.get("FILES", []) if x.strip()],
        "env_names": env_names,
        "env_present": True,
        "live_enabled": live or None,
        "broker_modules": [x for x in sec.get("BROKER", []) if x.strip()] or None,
        "adapter_has_order": order_n != 0,   # -1（采集异常）也算异常 ⇒ True
        "paper_auth_channel": auth_channel,
    }


def evaluate(meta: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    if not meta.get("present"):
        return [("远端/本地采集", BAD,
                 f"采集失败或输出为空（{meta.get('error', 'unknown')}）⇒ 失败关闭，不得判 PASS")]

    bad_files = [f for f in meta.get("files", [])
                 if any(t in Path(f).name.lower() for t in FORBIDDEN_FILE_TOKENS)]
    rows.append(("真实通道授权/适配器文件", BAD if bad_files else OK,
                 f"命中 {len(bad_files)} 个" + (f"：{', '.join(bad_files[:3])}" if bad_files else "（应为 0）")))

    bad_env = [n for n in meta.get("env_names", [])
               if any(n.startswith(p) for p in FORBIDDEN_ENV_PREFIXES)]
    if meta.get("env_present") is False:
        # 本地仓库本来就没有 env（凭证只在云端）；没检查到 ≠ 通过。
        rows.append(("真实通道 env 变量", INFO,
                     "本地无 env 文件 ⇒ 此项未检，须用 --remote 复核"))
    else:
        rows.append(("真实通道 env 变量", BAD if bad_env else OK,
                     f"命中 {len(bad_env)} 个" + (f"：{', '.join(bad_env[:3])}" if bad_env else "（只查变量名，不读值）")))

    lv = meta.get("live_enabled")
    lv_bad = lv is not None and str(lv).lower() in ("1", "true", "yes", "on")
    if meta.get("env_present") is False:
        rows.append(("LIVE_ENABLED", INFO, "本地无 env 文件 ⇒ 此项未检，须用 --remote 复核"))
    else:
        rows.append(("LIVE_ENABLED", BAD if lv_bad else OK,
                     f"当前值={lv or '未设置'}（须为 false/未设置）"))

    mods = meta.get("broker_modules")
    if mods is None:
        rows.append(("broker 模块白名单", BAD, "目录不存在或采集为空 ⇒ 失败关闭"))
    else:
        extra = sorted(set(mods) - ALLOWED_BROKER_MODULES)
        rows.append(("broker 模块白名单", BAD if extra else OK,
                     f"{len(mods)} 个 .py" + (f"，白名单外：{', '.join(extra)}" if extra else "，全在白名单内")))

    if "adapter_src" in meta:
        src = meta.get("adapter_src")
        has_order = src is None or bool(ORDER_CALL_RE.search(src))   # None ⇒ 采集不到也算 FAIL
        rows.append(("只读适配器无 order_* 调用", BAD if has_order else OK,
                     "源码缺失或含下单调用 ⇒ FAIL" if has_order else "仅 set_token/login/get_cash/get_positions"))
    else:
        has_order = bool(meta.get("adapter_has_order", True))
        rows.append(("只读适配器无 order_* 调用", BAD if has_order else OK,
                     "远端检测到 order_*( 调用或计数异常" if has_order else "远端 grep 计数 0"))

    ch = meta.get("paper_auth_channel")
    if ch is None:
        rows.append(("paper_authorization.json", INFO, "不存在（当前预期：agent 不得创建）"))
    elif ch == "gm-paper":
        rows.append(("paper_authorization.json", INFO, "存在且 channel=gm-paper（须另核有效期/金额/签名）"))
    else:
        rows.append(("paper_authorization.json", BAD,
                     f"存在但 channel={ch!r} 非 gm-paper 或不可解析"))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R6-1 通道隔离检查（只读）")
    ap.add_argument("--remote", action="store_true", help="ssh 到云主机 /opt/quant 采集")
    ap.add_argument("--root", default=None, help="离线复核指定目录")
    args = ap.parse_args(argv)

    meta = (collect_remote() if args.remote else
            collect_local(Path(args.root) if args.root else REPO))
    src = "云端 /opt/quant" if args.remote else (args.root or "本地仓库")
    print(f"[isolate] 通道隔离检查｜{src}｜只输出文件名与变量名，不输出凭证值")
    rows = evaluate(meta)
    for name, st, why in rows:
        print(f"  [{st:4s}] {name:28s} {why}")
    n_bad = sum(1 for _, s, _ in rows if s == BAD)
    print(f"[isolate] 结论：FAIL {n_bad} 项｜{BAD if n_bad else OK}")
    return 1 if n_bad else 0


if __name__ == "__main__":
    sys.exit(main())
