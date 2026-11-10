#!/usr/bin/env python3
"""ECR-P0-SNAPSHOT-1 / R7-4：7 条**真改行为**的变异，附「删除实现即变红」证据。

⚠️ 用临时副本 + importlib 加载变异体，**不原地改产品源码**，因此可与其他
   pytest 串行/并发使用，也不存在恢复失败污染工作树的风险（11-09 踩过的坑）。
⚠️ 每条变异必须让对应的验收断言从「原实现 PASS」变成「变异体 FAIL」，
   且不接受被别的分支兜住的假变异（M6 尤其注意：unknown 分支会假兜住）。

输出同时作为工程变更单第 4 节要求的「删除实现即变红」留痕。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import paper_account_adapter as paa  # noqa: E402
import shadow_daily_reconcile as sdr  # noqa: E402
import stage_rc_gate as srg  # noqa: E402

ADAPTER = ROOT / "scripts" / "paper_account_adapter.py"
SHADOW = ROOT / "scripts" / "shadow_daily_reconcile.py"
GATE = ROOT / "scripts" / "stage_rc_gate.py"


def rec(day: str, available: float = 100000.0) -> dict:
    return {"schema_version": "paper_snapshot_v2", "asof": day, "source": "gm-paper",
            "cash": {"available": available, "frozen": 0.0, "total": available},
            "positions": [], "collected_at": "T",
            "settlement_verified": "unknown"}


def load_variant(src_path: pathlib.Path, old: str, new: str, tag: str, tmp: pathlib.Path):
    text = src_path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{tag}: 变异锚点不唯一/不存在 count={text.count(old)}"
    p = tmp / (tag + "_" + src_path.name)
    p.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    s = importlib.util.spec_from_file_location("mut_" + tag, p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


# ── 每个 AC 的「验收断言」：原实现必须 True，变异体必须 False ─────────────
def check_ac1(mod, d: pathlib.Path) -> bool:
    """跨日推进：两天各写各的 rev1，两个文件都在且互不覆盖。"""
    r1, p1, _ = mod.write_daily_snapshot(rec("20261109"), directory=d)
    r2, p2, _ = mod.write_daily_snapshot(rec("20261110"), directory=d)
    return (r1 == 0 and r2 == 0 and p1 != p2
            and p1.name == "account_snapshot_20261109_rev1.json"
            and p2.name == "account_snapshot_20261110_rev1.json"
            and p1.is_file() and p2.is_file())


def check_ac2(mod, d: pathlib.Path) -> bool:
    """同日不同账户内容 ⇒ 拒绝且非零 rc，不产生 rev2。"""
    mod.write_daily_snapshot(rec("20261110", 100000.0), directory=d)
    rc, _, _ = mod.write_daily_snapshot(rec("20261110", 90000.0), directory=d)
    return rc == paa.EXIT_CONFLICT and not (d / "account_snapshot_20261110_rev2.json").exists()


def check_ac3(mod, d: pathlib.Path) -> bool:
    """陈旧快照必须拒绝（payload=None）。"""
    (d / "account_snapshot_20261110_rev1.json").write_text(
        json.dumps(rec("20261109")) + "\n", encoding="utf-8")
    why, payload, _ = mod.load_daily_snapshot("20261110", d)
    return why.startswith("account_snapshot_stale") and payload is None


def check_ac4(mod, d: pathlib.Path) -> bool:
    """多 rev 账户内容不一致 ⇒ REV_CONFLICT。"""
    (d / "account_snapshot_20261110_rev1.json").write_text(
        json.dumps(rec("20261110", 100000.0)) + "\n", encoding="utf-8")
    (d / "account_snapshot_20261110_rev2.json").write_text(
        json.dumps(rec("20261110", 90000.0)) + "\n", encoding="utf-8")
    why, payload, _ = mod.load_daily_snapshot("20261110", d)
    return why == sdr.REV_CONFLICT and payload is None


def check_ac5(mod) -> bool:
    """关键阶段 paper_snapshot rc=4 ⇒ ok=false 且告警。"""
    r = mod.evaluate("update=0:关键;paper_snapshot=4:关键;backup=0:关键")
    return r["ok"] is False and r["alert"] is True \
        and "paper_snapshot" in r["failed_key_stages"]


def check_ac6(mod, d: pathlib.Path) -> bool:
    """迁移幂等：第二次返回 already_migrated 且原件/副本字节不变。"""
    legacy = d / "account_snapshot.json"
    body = json.dumps({"asof": "20261109", "source": "gm-paper",
                       "cash": {"available": 100000.0, "frozen": 0.0, "total": 100000.0},
                       "positions": []}, indent=1, sort_keys=True) + "\n"
    legacy.write_bytes(body.encode("utf-8"))
    r1 = mod.migrate_legacy_snapshot(directory=d, legacy=legacy)
    target = d / "account_snapshot_20261109_rev1.json"
    t_before, l_before = target.read_bytes(), legacy.read_bytes()
    r2 = mod.migrate_legacy_snapshot(directory=d, legacy=legacy)
    return (r1["status"] == "migrated" and r2["status"] == "already_migrated"
            and target.read_bytes() == t_before and legacy.read_bytes() == l_before)


def check_ac7(mod, d: pathlib.Path) -> bool:
    """supersede 必须生成 rev2 并记录原因。"""
    mod.write_daily_snapshot(rec("20261110", 100000.0), directory=d)
    rc, p, _ = mod.write_daily_snapshot(rec("20261110", 90000.0), directory=d,
                                        supersede_reason="结算后重取")
    if rc != 0 or p is None:
        return False
    body = json.loads(p.read_text(encoding="utf-8"))
    return (p.name.endswith("_rev2.json") and body.get("supersedes_rev") == 1
            and body.get("supersede_reason") == "结算后重取")


MUTATIONS = [
    ("M1", "AC-1 跨日推进", ADAPTER,
     'path = directory / f"account_snapshot_{day}_rev{rev}.json"',
     'path = directory / "account_snapshot.json"',
     "check_ac1", "把按日期分文件改回单文件 ⇒ 跨日推进覆盖/丢失"),
    ("M2", "AC-2 同日不同内容拒绝", ADAPTER,
     "if not supersede_reason:",
     "if False:",
     "check_ac2", "删掉冲突拒绝分支 ⇒ 同日被静默写成 rev2"),
    ("M3", "AC-2 supersede 生成 rev2", ADAPTER,
     "rev = max(n for n, _, _ in bodies) + 1",
     "rev = 1",
     "check_ac7", "rev 恒为 1 ⇒ 覆盖 rev1、无 rev2"),
    ("M4", "AC-3 陈旧快照拒绝", SHADOW,
     '    for _, _, d in docs:\n        if str(d.get("asof", "")) != day:',
     '    for _, _, d in docs:\n        if False:',
     "check_ac3", "删掉 asof 判定 ⇒ 陈旧快照被当成当日真值"),
    ("M5", "AC-4 REV_CONFLICT", SHADOW,
     "if len(bodies) != 1:",
     "if False:",
     "check_ac4", "删掉多 rev 内容比对 ⇒ 冲突日被计入 G2"),
    ("M6", "AC-5 关键阶段 rc≠0 ⇒ ok=false", GATE,
     '    failed_key = [s["name"] for s in stages\n'
     '                  if s["kind"] == "关键" and s["rc"] != 0\n'
     '                  and s["name"] in KEY_STAGES]',
     '    failed_key = []',
     "check_ac5", "删除关键阶段失败判定（整段实现）⇒ ok 恒真、告警漏报"),
    ("M7", "AC-6 迁移幂等", ADAPTER,
     "if target.is_file():",
     "if False:",
     "check_ac6", "删掉已存在分支 ⇒ 重复迁移重写副本"),
]

CHECKS = {"check_ac1": lambda m, d: check_ac1(m, d),
          "check_ac2": lambda m, d: check_ac2(m, d),
          "check_ac3": lambda m, d: check_ac3(m, d),
          "check_ac4": lambda m, d: check_ac4(m, d),
          "check_ac5": lambda m, d: check_ac5(m),
          "check_ac6": lambda m, d: check_ac6(m, d),
          "check_ac7": lambda m, d: check_ac7(m, d)}

BASE_MODS = {"check_ac1": paa, "check_ac2": paa, "check_ac3": sdr,
             "check_ac4": sdr, "check_ac5": srg, "check_ac6": paa,
             "check_ac7": paa}


def main() -> int:
    detected = 0
    rows = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for tag, ac, src_path, old, new, check_name, desc in MUTATIONS:
            d_base = tmp / (tag + "_base"); d_base.mkdir()
            d_mut = tmp / (tag + "_mut"); d_mut.mkdir()
            fn = CHECKS[check_name]
            base_ok = bool(fn(BASE_MODS[check_name], d_base))
            mut = load_variant(src_path, old, new, tag, tmp)
            mut_ok = bool(fn(mut, d_mut))
            hit = base_ok and not mut_ok
            if hit:
                detected += 1
            rows.append((tag, ac, base_ok, mut_ok, hit, desc))
            print(f"[{tag}] {'✓ 检出' if hit else '✗ 未检出'}｜{ac}｜"
                  f"原实现 {'PASS' if base_ok else 'FAIL'} → 变异体 {'PASS' if mut_ok else 'FAIL'}"
                  f"｜{desc}")
    print(f"\n[ECR-P0-SNAPSHOT-1 变异] {detected}/{len(MUTATIONS)} 检出"
          f"（每条均真改行为，非假变异）")
    print("[证据] 删除实现即变红：以上每条的『原实现 PASS → 变异体 FAIL』"
          "即为 R7-4 要求的留痕")
    return 0 if detected == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
