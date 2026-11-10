"""R6-3：部署可证性——文件 sha256 + 仓库 commit 清单，支持本地/云端一致性校验。

⚠️ 一条必须写明的事实
----------------------
云主机 ``/opt/quant`` **不是 git 工作副本**（无 .git），裸仓库在
``/opt/quant-backup/repo.git``。所以 manifest 里的 ``commit`` 来自**生成本地仓库
的 HEAD**，它证明「这批文件对应哪个提交」，**不能**证明云端目录本身就是该提交
的工作副本。逐文件 sha256 比对才是云端一致性的证据。

失败关闭：清单缺失、哈希集合为空、ssh 失败、任一路径缺席 ⇒ FAIL，
绝不因为「没发现不一致」就报 PASS（本项目已多次栽在空输入恒真上）。

用法：
    python scripts/deploy_manifest.py --write              # 生成新清单，旧清单转 .prev
    python scripts/deploy_manifest.py --verify             # 本地文件 vs 清单
    python scripts/deploy_manifest.py --verify --remote   # 云端 /opt/quant vs 清单
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FWD = REPO / "research" / "stock_pick_2026-10-25" / "forward"
MANIFEST = FWD / "deploy_manifest.json"
PREV = FWD / "deploy_manifest.prev.json"
SSH_KEY = REPO / "var" / "inbox" / "dq_deploy_ed25519"
REMOTE_HOST = "root@110.40.190.225"
REMOTE_ROOT = "/opt/quant"
SCHEMA = "deploy_manifest_v1"

#: 实际参与运行/部署的文件（显式列出，避免把 data/、日志、凭证卷进来）。
DEPLOY_FILES = [
    "deploy/bin/daily_chain.sh",
    "deploy/bin/backup_offsite.sh",
    "deploy/bin/weekly_offsite.sh",
    "deploy/bin/is_due.py",
    "deploy/bin/heartbeat_alert.py",
    "deploy/bin/notify_mail.py",
    "deploy/bin/preflight_sources.py",
    "deploy/bin/migrate_data.py",
    "scripts/paper_account_adapter.py",
    "scripts/shadow_daily_reconcile.py",
    "scripts/preflight_decision_day.py",
    "scripts/check_universe_mirror.py",
    "scripts/check_channel_isolation.py",
    "scripts/paper_authorization_guard.py",   # PA-GUARD-1：授权闸门，纳入部署可证性
    "scripts/stage_rc_gate.py",               # ECR-P0-SNAPSHOT-1 / R7-3：日更链阶段 rc 闸门
    "scripts/r7_dryrun_check.py",             # ECR-P0-SNAPSHOT-1 / R7-5：11-27 DRYRUN 自检
    "scripts/deploy_manifest.py",
    "scripts/lotapp_decision_brief.py",
    "scripts/verify_token_rotation.py",
    "scripts/dryrun_decision_day.py",
    "scripts/g1_progress.py",
    "scripts/rebuild_adj_from_exec.py",
    "scripts/verify_qfq_rebuild.py",
    "scripts/update_exec_incremental.py",
    "research/stock_pick_2026-10-25/scripts/sp5/trade_calendar.py",
    "research/stock_pick_2026-10-25/scripts/sp5/p03e_afford.py",
    "research/stock_pick_2026-10-25/scripts/sp5/p13_forward.py",
    "research/stock_pick_2026-10-25/scripts/sp5/p17_universe_filter.py",
    "research/stock_pick_2026-10-25/scripts/sp5/strategy_params.py",
]

#: 运行期需要 import 的包目录（动态展开，新增模块自动纳入）。
DEPLOY_GLOBS = [
    "backend/src/deepquant/execution/*.py",
    "backend/src/deepquant/broker/*.py",
]

OK, BAD = "PASS", "FAIL"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def collect_paths() -> list[str]:
    paths = list(DEPLOY_FILES)
    for pattern in DEPLOY_GLOBS:
        paths += sorted(p.relative_to(REPO).as_posix()
                        for p in REPO.glob(pattern) if p.is_file())
    return sorted(set(paths))


def git_head() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def build(root: Path = REPO) -> dict:
    files: dict[str, str] = {}
    missing: list[str] = []
    for rel in collect_paths():
        p = Path(root) / rel
        if p.is_file():
            files[rel] = sha256_file(p)
        else:
            missing.append(rel)
    if missing:
        raise RuntimeError(f"清单里 {len(missing)} 个文件不存在：{', '.join(missing[:3])}")
    if not files:
        raise RuntimeError("清单为空 ⇒ 拒绝生成（空输入不得判 PASS）")
    return {
        "schema_version": SCHEMA,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "commit": git_head(),
        "commit_note": "commit 来自生成方本地 HEAD；/opt/quant 无 .git，云端一致性以逐文件 sha256 为准",
        "n_files": len(files),
        "files": files,
        "sha256_of_files": manifest_digest(files),
    }


def write_manifest() -> int:
    rec = build()
    body = json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if MANIFEST.is_file():
        # 保留上一版本：只留一版，不做无限堆叠。
        PREV.write_text(MANIFEST.read_text(encoding="utf-8"),
                        encoding="utf-8", newline="\n")
    FWD.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(body, encoding="utf-8", newline="\n")
    print(f"[manifest] {rec['n_files']} 文件｜commit {rec['commit'][:8]}｜"
          f"sha_of_files {rec['sha256_of_files'][:12]}｜已写 {MANIFEST.name}"
          + ("｜上一版已存 .prev.json" if PREV.is_file() else ""))
    return 0


def load_manifest(path: Path = MANIFEST) -> dict:
    if not path.is_file():
        raise RuntimeError(f"缺 {path.name}，先跑 --write")
    rec = json.loads(path.read_text(encoding="utf-8"))
    if rec.get("schema_version") != SCHEMA or not rec.get("files"):
        raise RuntimeError("manifest 结构不对或 files 为空")
    if manifest_digest(rec["files"]) != rec.get("sha256_of_files"):
        raise RuntimeError("manifest 自身 sha256 不符，文件可能被改过")
    return rec


def verify_local(rec: dict) -> tuple[bool, str, list[str]]:
    """预检 C11a 用：本地部署文件 vs manifest。返回 (ok, 摘要, 差异列表)。"""
    diffs: list[str] = []
    files = rec["files"]
    for rel, want in sorted(files.items()):
        p = REPO / rel
        if not p.is_file():
            diffs.append(f"缺 {rel}")
        elif sha256_file(p) != want:
            diffs.append(f"改 {rel}")
    return (not diffs, f"{len(files)} 文件逐位比对", diffs)


def verify_remote(rec: dict, timeout: int = 90) -> tuple[bool, str, list[str]]:
    """ssh 到云端取每个文件的 sha256（只回哈希，不回内容）。"""
    files = rec["files"]
    paths = sorted(files)
    quoted = " ".join("'" + p + "'" for p in paths)
    cmd = ["ssh", "-i", str(SSH_KEY), "-o", "StrictHostKeyChecking=no",
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", REMOTE_HOST,
           f"cd {REMOTE_ROOT} && sha256sum {quoted}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return False, f"ssh 异常 {type(exc).__name__} ⇒ 失败关闭", ["SSH_FAIL"]
    got: dict[str, str] = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and len(parts[0]) == 64:
            got[parts[1].lstrip("* ").strip()] = parts[0]
    # ⚠️ sha256sum 遇到「某个文件不存在」会 rc=1，但仍输出其余文件的哈希。
    # 直接因 rc!=0 判 SSH_FAIL 会把「缺哪几个文件」这条最有用的信息丢掉。
    if not got:
        return False, (f"云端哈希集合为空（ssh rc={r.returncode}）⇒ 失败关闭"), ["EMPTY_HASHES"]

    diffs: list[str] = []
    for rel in paths:
        if rel not in got:
            diffs.append(f"云端缺 {rel}")
        elif got[rel] != files[rel]:
            diffs.append(f"不一致 {rel}")
    note = f"云端 {len(got)}/{len(files)} 文件逐位比对"
    if r.returncode != 0:
        note += f"（sha256sum rc={r.returncode}，通常意味着有文件缺席）"
    return (not diffs, note, diffs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R6-3 部署 manifest（只读校验 / 显式 --write 才写）")
    ap.add_argument("--write", action="store_true", help="生成新清单并把旧清单转 .prev.json")
    ap.add_argument("--verify", action="store_true", help="校验（默认校验本地）")
    ap.add_argument("--remote", action="store_true", help="--verify 时改为校验云端 /opt/quant")
    args = ap.parse_args(argv)

    if args.write:
        try:
            return write_manifest()
        except Exception as exc:
            print(f"[manifest] ✗ {exc}", file=sys.stderr)
            return 1

    try:
        # 显式传模块级 MANIFEST：默认参数在定义时就绑定了对象，
        # 测试里 monkeypatch MANIFEST 不会生效（曾因此误判一次）。
        rec = load_manifest(MANIFEST)
    except Exception as exc:
        print(f"[manifest] ✗ {exc}", file=sys.stderr)
        return 1

    if args.remote:
        ok, why, diffs = verify_remote(rec)
        label = "云端 /opt/quant"
    else:
        ok, why, diffs = verify_local(rec)
        label = "本地仓库"
    print(f"[manifest] {label}｜commit {rec['commit'][:8]}｜{why}｜{OK if ok else BAD}")
    for d in diffs[:10]:
        print(f"  - {d}")
    if len(diffs) > 10:
        print(f"  ... 另有 {len(diffs) - 10} 条")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
