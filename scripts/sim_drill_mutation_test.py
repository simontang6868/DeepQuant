"""EARLY-IMPL-1 真变异测试（预登记 P3 / P4 / P5）。

纪律（本项目复发过 12 次「假变异」）：
1. 每个变异的替换文本必须**命中且唯一**；
2. 原版签名必须是「停机 / 拒绝」；
3. 变异后签名必须变成「PASS / 放行」——**只换个拒绝理由不算检出**；
4. 期望值来自**独立算术与产物**，不调用被测函数自身；
5. 覆盖 11-30 之后的 12-01 场景（variants 用 2026-12-01 时点的台账标识）。

运行：``python scripts/sim_drill_mutation_test.py``
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REC_SRC = SCRIPTS / "sim_reconcile.py"
DRV_SRC = SCRIPTS / "sim_drill_driver.py"
REC_ORIGINAL = REC_SRC.read_text(encoding="utf-8")
DRV_ORIGINAL = DRV_SRC.read_text(encoding="utf-8")
sys.path.insert(0, str(SCRIPTS))
import sim_reconcile as base  # noqa: E402
import sim_drill_driver as drv_base  # noqa: E402


def fixture():
    return base.selfcheck_fixture()


def with_extra_local():
    t, l = fixture()
    o = copy.deepcopy(l["orders"][0])
    o["client_order_id"] = "SIM-20261201-EXTRA"
    l["orders"].append(o)
    return t, l


def with_field_diff(key, value):
    t, l = fixture()
    l["orders"][0][key] = value
    return t, l


def with_duplicate_fill():
    t, l = fixture()
    t["execution_reports"].append(copy.deepcopy(t["execution_reports"][0]))
    l["orders"].append(copy.deepcopy(l["orders"][0]))
    return t, l


def with_position_diff():
    t, l = fixture()
    l["positions"][0]["quantity"] = 99
    return t, l


def with_cash_diff():
    t, l = fixture()
    l["cash"]["available"] = 98000.0
    return t, l


def with_both_empty():
    t, l = fixture()
    t["execution_reports"] = []
    l["orders"] = []
    return t, l


EMPTY_GUARD = ('    if (not isinstance(truth["execution_reports"], list)\n'
               '            or not truth["execution_reports"]\n'
               '            or not isinstance(local.get("orders"), list)\n'
               '            or not local["orders"]):')

#: (编号, 描述, 原文, 变异, fixture 构造函数, 原版期望 decision)
REC_MUTATIONS = [
    ("M1", "空输入不再失败关闭（空回报被当无差异）",
     EMPTY_GUARD, '    if False:', with_both_empty, base.NO_INPUTS),
    ("M2", "委托集合双向差集失效（本地多单不拦）",
     '    if set(ext) != set(loc):', '    if False:',
     with_extra_local, "HALT:ORDER_SET"),
    ("M3", "逐字段比较失效（代码不符不拦）",
     '            if e[fld] != l[fld]:', '            if False:',
     lambda: with_field_diff("code", "000001"), "HALT:FIELD"),
    ("M4", "逐笔价格比较失效",
     '        if e["prices"] != l["prices"]:', '        if False:',
     lambda: with_field_diff("price", 10.01), "HALT:FIELD"),
    ("M5", "成交累计比较失效",
     '        if ext[cid]["quantity"] != loc[cid]["quantity"]:', '        if False:',
     lambda: with_field_diff("quantity", 99), "HALT:FILL"),
    ("M6", "重复回报检测失效（同一 fill_id 两次）",
     '    if len(all_fill_ids) != len(set(all_fill_ids)):', '    if False:',
     with_duplicate_fill, "HALT:FILL"),
    ("M7", "费用比较失效",
     '        if ext[cid]["fee"] != loc[cid]["fee"]:', '        if False:',
     lambda: with_field_diff("fee", 1.0), "HALT:FEE"),
    ("M8", "持仓 / 可用数比较失效",
     '    if ext_pos != loc_pos:', '    if False:',
     with_position_diff, "HALT:POSITION"),
    ("M9", "资金比较失效",
     '    if ext_cash != loc_cash:', '    if False:',
     with_cash_diff, "HALT:CASH"),
]

EXEC_GUARD = ('    if args.execute:\n'
              '        print(f"[sim-drill] {NOT_AUTHORIZED}｜工程准备授权不含 12-01 执行许可；"\n'
              '              "须 U-16 书面放行 + 11-30 FORWARD 台账 + 另立执行预登记｜未发任何委托")\n'
              '        return 4')

PATH_GUARD = ('        if not p.name.startswith("selftest_"):\n'
              '            raise RuntimeError(f"自检产物名必须以 selftest_ 开头：{p.name}")')


def load_variant(code: str, name: str) -> dict:
    ns = {"__name__": name, "__file__": str(REC_SRC if name.startswith("rec") else DRV_SRC)}
    exec(compile(code, ns["__file__"], "exec"), ns)  # noqa: S102 - 本仓库脚本源码
    return ns


def rec_sig(ns, t, l):
    r = ns["reconcile"](t, l)
    return (r["decision"], r["n_differences"])


def main() -> int:
    detected = 0
    total = len(REC_MUTATIONS) + 2

    # ---- 对账器 9 个变异：原版停机，变异后必须 PASS 且 0 差异 ----
    for mid, desc, old, new, make, expected in REC_MUTATIONS:
        if old not in REC_ORIGINAL or REC_ORIGINAL.count(old) != 1:
            print(f"[{mid}] ✗ 变异定义失效：替换文本未命中或不唯一"
                  f"（count={REC_ORIGINAL.count(old)}）")
            return 1
        t, l = make()
        b = rec_sig(vars(base), t, l)
        if b[0] != expected or b[1] < 1:
            print(f"[{mid}] ✗ 测试用例无效：原版签名 {b}，期望 {expected}/≥1")
            return 1
        variant = load_variant(REC_ORIGINAL.replace(old, new, 1), "rec_variant")
        v = rec_sig(variant, t, l)
        if v == b or v[0] != base.PASS or v[1] != 0:
            print(f"[{mid}] ✗ 变异未被检出或只是换了个拒绝理由：{desc}（{b}→{v}）")
            return 1
        detected += 1
        print(f"[{mid}] ✓ 检出：{desc}（{b[0]}→{v[0]}）")

    # ---- 驱动 D1：--execute 拒绝闸门失效 ⇒ 放行到自检 ----
    if EXEC_GUARD not in DRV_ORIGINAL or DRV_ORIGINAL.count(EXEC_GUARD) != 1:
        print("[D1] ✗ 变异定义失效：执行闸门未命中或不唯一")
        return 1
    b = drv_base.main(["--execute", "--self-check"])
    if b != 4:
        print(f"[D1] ✗ 原版未拒绝：rc={b}")
        return 1
    v = load_variant(DRV_ORIGINAL.replace(EXEC_GUARD, EXEC_GUARD.replace(
        "    if args.execute:", "    if False:", 1), 1), "drv_variant")
    rc = v["main"](["--execute", "--self-check"])
    if rc != 0:
        print(f"[D1] ✗ 执行闸门变异未被检出（拒绝→放行失败）：rc={rc}")
        return 1
    detected += 1
    print("[D1] ✓ 检出：--execute 闸门失效（NOT_AUTHORIZED→SELF_CHECK_PASS）")

    # ---- 驱动 D2：selftest_ 路径守卫失效 ⇒ 允许写正式演练文件名 ----
    if PATH_GUARD not in DRV_ORIGINAL or DRV_ORIGINAL.count(PATH_GUARD) != 1:
        print("[D2] ✗ 变异定义失效：路径守卫未命中或不唯一")
        return 1
    unsafe = drv_base.SIM_ORDERS / "20261201.jsonl"
    safe = drv_base.SIM_RECONCILE / "selftest_x_driver.json"
    try:
        drv_base._assert_safe_paths(unsafe, safe)
        print("[D2] ✗ 原版未拦截正式演练文件名")
        return 1
    except RuntimeError:
        pass
    v2 = load_variant(DRV_ORIGINAL.replace(
        PATH_GUARD, PATH_GUARD.replace(
            '        if not p.name.startswith("selftest_"):', "        if False:", 1), 1),
        "drv_path_variant")
    try:
        v2["_assert_safe_paths"](unsafe, safe)
        raised = False
    except RuntimeError:
        raised = True
    if raised:
        print("[D2] ✗ 路径守卫变异未被检出（仍拦截）")
        return 1
    detected += 1
    print("[D2] ✓ 检出：selftest_ 路径守卫失效（拦截→放行）")

    print(f"[mutation] {detected}/{total} 检出；全部变异均真改行为"
          "（停机/拒绝 → PASS/放行），无假变异")
    return 0


if __name__ == "__main__":
    sys.exit(main())
