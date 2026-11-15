#!/usr/bin/env python3
"""R8 §5：gm-paper 探针适配器的真变异测试 —— ⭐ **每条 DENY→ALLOW 必须检出**。

六条针对 ``paper_authorization_guard`` 的六项校验（逐项 ``cN = True``）：
每项用一个**只让该项失败**的授权 doc，验证「原实现 DENY ⇒ 变异体 ALLOW」。
另四条针对适配器自身的闸门（订单规则／执行闸门／幂等／旧台账禁用）。

用法： python scripts/gm_paper_probe_mutation_test.py
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

GUARD_SRC = (SCRIPTS / "paper_authorization_guard.py").read_text(encoding="utf-8")
ADAPTER_SRC = (SCRIPTS / "gm_paper_order_adapter.py").read_text(encoding="utf-8")


def load(text: str, tag: str, module_name: str):
    p = SCRIPTS / f"_mut_probe_{tag}.py"
    p.write_text(text, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(module_name, p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # ⭐ @dataclass 在 _is_type 里查 sys.modules[cls.__module__]，动态加载必须先注册
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return p, mod


def with_guard(text_guard, text_adapter, tag, tmp):
    """注入 guard 变体到 sys.modules，再加载（可能是变体的）adapter。"""
    saved = sys.modules.get("paper_authorization_guard")
    created = []
    try:
        pg, gmod = load(text_guard, f"g_{tag}", "paper_authorization_guard")
        created.append(pg)
        sys.modules["paper_authorization_guard"] = gmod
        pa, amod = load(text_adapter, f"a_{tag}", f"_mut_probe_adapter_{tag}")
        created.append(pa)
        return amod, created
    except BaseException:
        for p in created:
            try:
                p.unlink()
            except OSError:
                pass
        if saved is None:
            sys.modules.pop("paper_authorization_guard", None)
        else:
            sys.modules["paper_authorization_guard"] = saved
        raise


def restore(saved, created):
    for p in created:
        try:
            p.unlink()
        except OSError:
            pass
    for name in [n for n in list(sys.modules) if n.startswith("_mut_probe_adapter_")]:
        sys.modules.pop(name, None)
    if saved is None:
        sys.modules.pop("paper_authorization_guard", None)
    else:
        sys.modules["paper_authorization_guard"] = saved


# ---------------- 六项授权校验的缺陷 doc（只让对应一项失败） ----------------
AUTH_DEFECTS = {
    "SCHEMA": dict(schema="paper_auth_v0"),
    "CHANNEL": dict(channel="other"),
    "EXPIRY": dict(not_after="2026-11-01T00:00:00+08:00"),
    "SCOPE": dict(scopes=["query_cash"]),
    "NOTIONAL": dict(max_notional=100),
    "HASH": dict(tamper=True),
}
GUARD_MUTATIONS = [
    ("AUTH_1_SCHEMA_DENY_TO_ALLOW",
     'c1 = doc["schema_version"] == SCHEMA_VERSION', 'c1 = True', "SCHEMA"),
    ("AUTH_2_CHANNEL_DENY_TO_ALLOW",
     'c2 = doc["channel"] == ONLY_CHANNEL', 'c2 = True', "CHANNEL"),
    ("AUTH_3_EXPIRY_DENY_TO_ALLOW",
     "c3 = now_t <= until_t", "c3 = True", "EXPIRY"),
    ("AUTH_4_SCOPE_DENY_TO_ALLOW",
     "    c4 = (action in FROZEN_SCOPES and action in scopes\n"
     "          and all(s in FROZEN_SCOPES for s in scopes))",
     "    c4 = True", "SCOPE"),
    ("AUTH_5_NOTIONAL_DENY_TO_ALLOW",
     "c5 = (cap <= avail and n <= cap and cum + n <= cap)", "c5 = True", "NOTIONAL"),
    ("AUTH_6_HASH_DENY_TO_ALLOW",
     'c6 = hmac.compare_digest(compute_hash(body), doc["sha256"])', "c6 = True", "HASH"),
]

ADAPTER_MUTATIONS = [
    ("ADAPTER_ORDER_RULE_OFF", 'if not order["ok"]:', 'if False:'),
    ("ADAPTER_EXECUTE_OPEN", 'return {"decision": NOT_AUTHORIZED,',
     'return {"decision": guard.ALLOW,'),
    ("ADAPTER_CID_RANDOM",
     'return PROBE_TAG + "-" + hashlib.sha256(raw).hexdigest()[:24]',
     'return PROBE_TAG + "-" + __import__("os").urandom(12).hex()'),
    ("ADAPTER_LEGACY_FORBIDDEN_OFF", "if c in LEGACY_FORBIDDEN_CODES:", "if False:"),
]


def auth_doc_path(amod, tmp, tag, defect):
    p = tmp / f"auth_{tag}.json"
    amod._write_doc(p, amod._auth_doc(**AUTH_DEFECTS[defect]))
    return p


def main() -> int:
    saved = sys.modules.get("paper_authorization_guard")
    detected = 0
    total = len(GUARD_MUTATIONS) + len(ADAPTER_MUTATIONS)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # ---- 基线：原实现上所有缺陷 doc 必须 DENY、execute 必须 NOT_AUTHORIZED ----
        amod, created = with_guard(GUARD_SRC, ADAPTER_SRC, "baseline", tmp)
        try:
            ok_doc = tmp / "auth_ok.json"
            amod._write_doc(ok_doc, amod._auth_doc())
            for _n, _o, _new, defect in GUARD_MUTATIONS:
                p = auth_doc_path(amod, tmp, "base_" + defect, defect)
                if amod.GmPaperProbeAdapter().evaluate(amod._req(p))["decision"] != "DENY":
                    print(f"[MUT] FAIL 基线 {defect} 未 DENY ⇒ 检查器写错")
                    return 1
            if amod.GmPaperProbeAdapter().execute(amod._req(ok_doc))["decision"] != "NOT_AUTHORIZED":
                print("[MUT] FAIL 基线 execute 不是 NOT_AUTHORIZED")
                return 1
            if not amod.board_ok("600519") or amod.board_ok("603977"):
                print("[MUT] FAIL 基线板块规则异常")
                return 1
        finally:
            restore(saved, created)
        print(f"[MUT] 基线自检 OK：六项 DENY + 执行闸门 + 板块规则均正确\n")

        # ---- 六项 DENY→ALLOW ----
        for i, (name, old, new, defect) in enumerate(GUARD_MUTATIONS, 1):
            if GUARD_SRC.count(old) != 1:
                print(f"[MUT] FAIL {name} 锚点出现 {GUARD_SRC.count(old)} 次")
                return 1
            gv = GUARD_SRC.replace(old, new, 1)
            am, created = with_guard(gv, ADAPTER_SRC, f"g{i}", tmp)
            try:
                p = auth_doc_path(am, tmp, f"g{i}", defect)
                decision = am.GmPaperProbeAdapter().evaluate(am._req(p))["decision"]
                ok = decision == "ALLOW"
                print(f"  {name:34s} {'DETECTED' if ok else 'MISSED  '} | DENY→{decision}")
                detected += int(ok)
            finally:
                restore(saved, created)

        # ---- 适配器自身闸门 ----
        for i, (name, old, new) in enumerate(ADAPTER_MUTATIONS, 1):
            if ADAPTER_SRC.count(old) != 1:
                print(f"[MUT] FAIL {name} 锚点出现 {ADAPTER_SRC.count(old)} 次")
                return 1
            av = ADAPTER_SRC.replace(old, new, 1)
            am, created = with_guard(GUARD_SRC, av, f"a{i}", tmp)
            try:
                ok_doc = tmp / f"ok_a{i}.json"
                am._write_doc(ok_doc, am._auth_doc())
                if i == 1:      # 订单规则被关：大单应放行
                    ok = am.GmPaperProbeAdapter().evaluate(am._req(ok_doc, price=99.0,
                                             notional=9900.0))["decision"] == "ALLOW"
                elif i == 2:    # 执行闸门被打开
                    ok = am.GmPaperProbeAdapter().execute(am._req(ok_doc))["decision"] == "ALLOW"
                elif i == 3:    # 幂等键被随机化
                    kw = dict(probe_id="P", code="600519", side="BUY",
                              day="20261117", lots=1)
                    ok = am.client_order_id(**kw) != am.client_order_id(**kw)
                else:           # 旧台账整组禁用被关
                    ok = am.board_ok("603977") and \
                        am.GmPaperProbeAdapter().evaluate(am._req(ok_doc, code="603977"))["decision"] == "ALLOW"
                print(f"  {name:34s} {'DETECTED' if ok else 'MISSED  '}")
                detected += int(ok)
            finally:
                restore(saved, created)

    print(f"\n[MUT] DENY→ALLOW / 闸门变异检出 {detected}/{total}")
    if detected != total:
        print("[MUT] FAIL 有变异未被检出 ⇒ 不得报绿")
        return 1
    print("[MUT] PASS 全部变异真改行为且被检出（零真实委托）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
