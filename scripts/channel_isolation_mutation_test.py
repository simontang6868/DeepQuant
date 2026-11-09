"""R6-1 通道隔离检查器的变异测试。

纪律：变异必须**真改行为**。本项目出现过两次「假变异」（替换了文本但没改行为，
于是测试全绿却什么都没证明）。所以每个变异体都先确认：
  ① 替换文本确实命中；② 原版对该 ``meta`` 判 FAIL；③ 变异后不再判 FAIL。
三者缺一即报错，不假装检出。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "check_channel_isolation.py"
ORIGINAL = SRC.read_text(encoding="utf-8")

sys.path.insert(0, str(SRC.parent))
import check_channel_isolation as base  # noqa: E402


def clean(**kw):
    m = {
        "present": True, "files": [],
        "env_names": ["DQ_GM_TOKEN"], "env_present": True,
        "live_enabled": None,
        "broker_modules": sorted(base.ALLOWED_BROKER_MODULES),
        "adapter_has_order": False, "paper_auth_channel": None,
    }
    m.update(kw)
    return m


def load_variant(code: str):
    ns = {"__name__": "cci_variant", "__file__": str(SRC)}
    exec(compile(code, str(SRC), "exec"), ns)  # noqa: S102 - 仅执行本仓库脚本源码
    return ns


def has_bad(mod, meta, name=None):
    rows = mod["evaluate"](meta)
    return any(s == "FAIL" and (name is None or n == name) for n, s, _ in rows)


MUTATIONS = [
    ("M1", "禁用 env 前缀清空",
     'FORBIDDEN_ENV_PREFIXES = ("DQ_LIVE_", "QMT_", "BROKER_", "GM_LIVE_", "DQ_BROKER_")',
     'FORBIDDEN_ENV_PREFIXES = ()',
     clean(env_names=["DQ_LIVE_TOKEN"]), "真实通道 env 变量"),

    ("M2", "禁用文件名特征清空",
     'FORBIDDEN_FILE_TOKENS = ("live_authorization", "live_adapter", "qmt", "mini_qmt",\n'
     '                         "broker_live", "broker_real", "live.env")',
     'FORBIDDEN_FILE_TOKENS = ()',
     clean(files=["forward/live_authorization.json"]), "真实通道授权/适配器文件"),

    ("M3", "broker 白名单外模块不检查",
     "extra = sorted(set(mods) - ALLOWED_BROKER_MODULES)",
     "extra = []",
     clean(broker_modules=["base.py", "qmt_live.py"]), "broker 模块白名单"),

    ("M4", "适配器下单调用检测失效",
     'ORDER_CALL_RE = re.compile(r"\\border_[A-Za-z_][A-Za-z0-9_]*\\s*\\(")',
     'ORDER_CALL_RE = re.compile(r"(?!x)x")',
     clean(adapter_src="def f():\n    return order_volume(1)\n"), "只读适配器无 order_* 调用"),

    ("M5", "采集失败不再失败关闭",
     'if not meta.get("present"):',
     'if False:',
     clean(present=False, env_names=[]), None),

    ("M6", "LIVE_ENABLED=true 不再判红",
     'lv_bad = lv is not None and str(lv).lower() in ("1", "true", "yes", "on")',
     'lv_bad = False',
     clean(live_enabled="true"), "LIVE_ENABLED"),

    ("M7", "远端 order_ 计数标记不再判红",
     'has_order = bool(meta.get("adapter_has_order", True))',
     'has_order = False',
     clean(adapter_has_order=True), "只读适配器无 order_* 调用"),

    ("M8", "仿真授权 channel=live 不再判红",
     'elif ch == "gm-paper":',
     'elif ch in ("gm-paper", "live"):',
     clean(paper_auth_channel="live"), "paper_authorization.json"),
]


def main() -> int:
    detected = 0
    for mid, desc, old, new, meta, name in MUTATIONS:
        if old not in ORIGINAL:
            print(f"[{mid}] ✗ 变异定义失效：替换文本未命中源码")
            return 1
        variant = load_variant(ORIGINAL.replace(old, new, 1))
        base_bad = has_bad(vars(base), meta, name)
        var_bad = has_bad(variant, meta, name)
        if not base_bad:
            print(f"[{mid}] ✗ 测试用例无效：原版未判 FAIL（{desc}）")
            return 1
        if var_bad:
            print(f"[{mid}] ✗ 变异未被检出：{desc}")
            return 1
        detected += 1
        print(f"[{mid}] ✓ 检出：{desc}")

    # M9：变异点在 collect_remote（不在 evaluate 路径上），必须真的调远端采集，
    # 否则又是「替换了文本但没改被测行为」的假变异。
    old9 = '"adapter_has_order": order_n != 0,   # -1（采集异常）也算异常 ⇒ True'
    new9 = '"adapter_has_order": False,'
    if old9 not in ORIGINAL:
        print("[M9] ✗ 变异定义失效：替换文本未命中源码")
        return 1

    class FakeOut:
        returncode = 0
        stdout = ("---FILES---\n---ENV---\nDQ_GM_TOKEN\n---LIVE---\n"
                  "---BROKER---\nbase.py\n---ORDER---\n1\n"
                  "---AUTH---\nNONE\n---END---\n")

    def fake_run(*_a, **_k):
        return FakeOut()

    def order_fail(ns):
        saved = ns["subprocess"]
        ns["subprocess"] = types.SimpleNamespace(run=fake_run)
        try:
            meta = ns["collect_remote"]()
        finally:
            ns["subprocess"] = saved
        return has_bad(ns, meta, "只读适配器无 order_* 调用")

    base_ns = vars(base)
    var_ns = load_variant(ORIGINAL.replace(old9, new9, 1))
    if not order_fail(base_ns):
        print("[M9] ✗ 测试用例无效：原版未判 FAIL")
        return 1
    if order_fail(var_ns):
        print("[M9] ✗ 变异未被检出：远端 order_ 计数恒为 0")
        return 1
    detected += 1
    print("[M9] ✓ 检出：远端 order_ 计数恒为 0（真调 collect_remote）")

    # M10：2026-11-09 实际抓到的路径分叉。变异点在 collect_local 用的常量，
    # 必须真调本地采集；只测 evaluate 就是「替换了文本但没改被测行为」的假变异。
    old10 = 'PAPER_AUTH_REL = FORWARD_DIR_REL + "/paper_authorization.json"'
    new10 = 'PAPER_AUTH_REL = "forward/paper_authorization.json"'
    if old10 not in ORIGINAL:
        print("[M10] ✗ 变异定义失效：替换文本未命中源码")
        return 1
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        real = root / base.FORWARD_DIR_REL
        real.mkdir(parents=True)
        (real / "paper_authorization.json").write_text(
            '{"channel": "gm-paper"}', encoding="utf-8")

        def auth_read(ns):
            return ns["collect_local"](root)["paper_auth_channel"]

        if auth_read(base_ns) != "gm-paper":
            print("[M10] ✗ 测试用例无效：原版未读到授权文件")
            return 1
        var10 = load_variant(ORIGINAL.replace(old10, new10, 1))
        if auth_read(var10) is not None:
            print("[M10] ✗ 变异未被检出：路径回退到不存在的根 forward/")
            return 1
    detected += 1
    print("[M10] ✓ 检出：授权文件路径回退（真调 collect_local）")

    total = len(MUTATIONS) + 2
    print(f"[mutation] {detected}/{total} 检出；全部变异均真改行为")
    return 0


if __name__ == "__main__":
    sys.exit(main())
