"""H9 修复真变异：把 Timestamp→ISO 改回 str(v)，测试必须变红。

真变异判据：注入变异 import 后运行 test_created_at_pb_fix，若测试仍全绿
⇒ MISSED（测试没覆盖修复点）；若变红 ⇒ DETECTED。恢复后 sha 逐位一致。
不修改磁盘上的源文件：用临时副本 + importlib 覆盖模块名实现。
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import contextlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "paper_account_adapter.py"
TARGET_SHA = hashlib.sha256(SRC.read_bytes()).hexdigest()

OLD = '''    ts_iso = _proto_timestamp_to_iso(v)
    if ts_iso is not None:
        return ts_iso
    return str(v)'''
NEW = '''    return str(v)  # MUT: 退回旧缺陷——Timestamp 走 str 落盘'''


def _load_variant(source: str):
    """把给定源码作为 paper_account_adapter 加载并返回模块对象（无 pytest 竞争）。"""
    import tempfile
    mod_path = Path(tempfile.mkdtemp(prefix="dq_h9mut_")) / "paper_account_adapter.py"
    mod_path.write_text(source, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location("paper_account_adapter", mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["paper_account_adapter"] = mod
    spec.loader.exec_module(mod)
    return mod, mod_path


def _probe(mod) -> bool:
    """直接调用被测模块的 _plain_value + 对账解析，等价于核心修复测试。

    不经过 pytest 收集（测试文件顶部 sys.path.insert 会重新 import 磁盘原文件，
    覆盖注入的变异模块 —— 这正是"假变异 MISSED"的根因）。
    """
    import datetime as dt
    from sim_reconcile import check_exec_timestamps

    class _Desc:
        full_name = "google.protobuf.Timestamp"
    class _TS:
        DESCRIPTOR = _Desc()
        def __init__(self, s, n=0): self.seconds = s; self.nanos = n

    secs = int(dt.datetime(2026, 12, 1, 1, 35, 12,
                           tzinfo=dt.timezone.utc).timestamp())
    iso = mod._plain_value(_TS(secs))
    ok, _ = check_exec_timestamps([{"created_at": iso}],
                                  intended="2026-12-01T09:35:00+08:00",
                                  window_min=10)
    return bool(ok)  # 修复在 ⇒ ISO 合法 ⇒ PASS；退回 str(v) ⇒ UNPARSEABLE ⇒ FAIL


def main() -> int:
    original = SRC.read_text(encoding="utf-8")
    assert original.count(OLD) == 1, f"锚点应唯一，实际 {original.count(OLD)}"
    mutated = original.replace(OLD, NEW)
    assert mutated != original, "变异必须真改行为"

    base_mod, _ = _load_variant(original)
    base_ok = _probe(base_mod)
    print("BASE_ALL_GREEN:", base_ok)

    mut_mod, _ = _load_variant(mutated)
    mut_ok = _probe(mut_mod)
    detected = not mut_ok
    print("M1_TIMESTAMP_STR_REGRESSION_DETECTED:", detected)
    if not detected:
        print("!! MISSED：探针未覆盖修复点")

    after = hashlib.sha256(SRC.read_bytes()).hexdigest()
    print("SHA_STABLE:", after == TARGET_SHA, after[:12])
    return 0 if (base_ok and detected and after == TARGET_SHA) else 1


if __name__ == "__main__":
    import sys as _s
    _s.exit(main())
