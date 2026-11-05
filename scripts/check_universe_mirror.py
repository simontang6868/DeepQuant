"""校验本地 universe_state 快照与云端逐文件一致（11-27 月末演练用）。

为什么单独一个脚本：
  云端 `universe_state/SHA256S.txt` 只登记**当日**一个文件（生产者行为），
  所以「云端有哈希清单」不等于「能校验全部快照」。本脚本**自己两侧算哈希**，
  不依赖那份清单。

失败关闭（R5-5）：
  - 拿不到云端哈希（ssh 失败 / 空输出）⇒ **FAIL**，不是 SKIPPED；
    曾经因为「空集合上 all() 恒真」把没有输入判成 PASS，这类假绿必须挡住。
  - 本地有、云端没有的文件 ⇒ FAIL（本地可能是过期或伪造的）。
  - 云端有、本地没有 ⇒ FAIL（镜像是残的，演练会用不完整的快照）。

用法：
    python scripts/check_universe_mirror.py                 # 比对并写/核对 universe_state_mirror.json
    python scripts/check_universe_mirror.py --no-write      # 只检查，不改清单
    python scripts/check_universe_mirror.py --offline       # 只核对本地清单与本地文件（不出网）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE_DIR = REPO / "research" / "stock_pick_2026-10-25" / "forward" / "universe_state"
#: 清单放在 `forward/` 而不是 `universe_state/`：后者被 .gitignore 忽略，
#: 而"我核对过哪些文件"这件事必须能随仓库审计。
MIRROR = STATE_DIR.parent / "universe_state_mirror.json"
HOST = "root@110.40.190.225"
KEY = REPO / "var" / "inbox" / "dq_deploy_ed25519"
REMOTE = "/opt/quant/research/stock_pick_2026-10-25/forward/universe_state"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def local_hashes(d: Path) -> dict[str, str]:
    return {p.name: sha256_file(p) for p in sorted(d.glob("2026*")) if p.is_file()}


def parse_sha256sum(text: str) -> dict[str, str]:
    """解析 `sha256sum` 输出，去掉二进制标记 `*`。非法行直接跳过（不给默认值）。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or len(parts[0]) != 64:
            continue
        out[parts[1].lstrip("*")] = parts[0]
    return out


def compare(local: dict[str, str], remote: dict[str, str]) -> dict:
    """返回结构化结论。**空云端集合直接判 FAIL**（否则 all() 恒真 → 假绿）。"""
    if not remote:
        return {"ok": False, "reason": "CLOUD_HASHES_EMPTY", "mismatch": [], "missing_local": []}
    if not local:
        return {"ok": False, "reason": "LOCAL_SNAPSHOTS_EMPTY", "mismatch": [], "missing_local": []}
    mismatch = sorted(k for k in remote if k in local and local[k] != remote[k])
    missing_local = sorted(k for k in remote if k not in local)
    local_only = sorted(k for k in local if k not in remote)
    ok = not mismatch and not missing_local and not local_only
    reason = "OK" if ok else "MISMATCH"
    return {"ok": ok, "reason": reason, "mismatch": mismatch,
            "missing_local": missing_local, "local_only": local_only}


def fetch_remote_hashes(host: str = HOST, key: Path = KEY, remote: str = REMOTE,
                        timeout: int = 40) -> dict[str, str]:
    cmd = ["ssh", "-i", str(key), "-o", "StrictHostKeyChecking=no",
           "-o", f"ConnectTimeout={timeout}", host,
           f"cd {remote} && sha256sum *.csv *.json"]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
    except (OSError, subprocess.SubprocessError):
        return {}
    if cp.returncode != 0:
        return {}
    return parse_sha256sum(cp.stdout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验 universe_state 本地镜像与云端一致")
    ap.add_argument("--offline", action="store_true", help="不出网，只核对 universe_state_mirror.json 与本地文件")
    ap.add_argument("--no-write", action="store_true", help="不更新 universe_state_mirror.json")
    ap.add_argument("--dir", default=str(STATE_DIR))
    args = ap.parse_args(argv)

    d = Path(args.dir)
    local = local_hashes(d)
    print(f"[mirror] 本地 {len(local)} 个快照文件：{sorted({k[:8] for k in local})}")

    if args.offline:
        if not MIRROR.is_file():
            print("[mirror] FAIL 缺 universe_state_mirror.json，无法离线核对")
            return 2
        rec = json.loads(MIRROR.read_text(encoding="utf-8"))
        res = compare(local, rec.get("files") or {})
        print(f"[mirror] 离线核对（清单 {rec.get('pulled_at')}）：{res['reason']}")
        if not res["ok"]:
            print(f"         差异={res.get('mismatch')} 本地缺={res.get('missing_local')} 清单外={res.get('local_only')}")
            return 1
        return 0

    remote = fetch_remote_hashes()
    print(f"[mirror] 云端 {len(remote)} 个文件")
    res = compare(local, remote)
    if not res["ok"]:
        print(f"[mirror] FAIL {res['reason']}")
        if res.get("mismatch"):
            print(f"         哈希不符：{res['mismatch']}")
        if res.get("missing_local"):
            print(f"         本地缺：{res['missing_local']}")
        if res.get("local_only"):
            print(f"         云端没有（本地多余）：{res['local_only']}")
        return 1
    print(f"[mirror] PASS 逐文件一致（{len(remote)} 个）")
    if not args.no_write:
        prev = {}
        if MIRROR.is_file():
            try:
                prev = json.loads(MIRROR.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                prev = {}
        rec = dict(prev)
        rec.update({"files": local, "all_identical": True, "mismatch": [],
                    "days": sorted({k[:8] for k in local}),
                    "verified_at": datetime.now().isoformat(timespec="seconds")})
        rec.setdefault("pulled_at", rec["verified_at"])
        rec.setdefault("note", "由 scripts/check_universe_mirror.py 校验后写入")
        MIRROR.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8", newline="\n")
        print(f"[mirror] 已更新 {MIRROR.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
