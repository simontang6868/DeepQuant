"""PA-GUARD-1 授权校验器的变异测试（预登记第七节要求 100% 检出）。

纪律：每个变异必须**真改行为**。判定签名 = (decision, reason)；
对每个变异要求：① 替换文本命中；② 原版签名等于预期（闸门确实拒绝）；
③ 变异后签名变化**且 decision == ALLOW**（闸门确实失效，不是换了个拒绝理由）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "paper_authorization_guard.py"
ORIGINAL = SRC.read_text(encoding="utf-8")
sys.path.insert(0, str(SRC.parent))
import paper_authorization_guard as base  # noqa: E402

NOW = "2026-12-01T09:35:00+08:00"
BASE_DOC = {
    "schema_version": "paper_auth_v1",
    "channel": "gm-paper",
    "account_ref": "REF_ONLY_NOT_A_CREDENTIAL",
    "max_notional": 100000,
    "not_after": "2026-12-02T23:59:00+08:00",
    "scopes": ["query_cash", "query_positions", "query_execution_reports",
               "sim_rebalance_order"],
    "statement_ref": "3207bff41a58",
    "issued_by": "ACCOUNT_HOLDER",
    "issued_at": "2026-11-09T11:16:00+08:00",
}


def body_hash(body):
    """测试侧独立算术，不调用被测模块。"""
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def doc(**over):
    b = {**BASE_DOC}
    forced = over.pop("sha256", None)   # 允许构造「哈希格式非法」的失败关闭用例
    b.update(over)
    return {**b, "sha256": forced if forced is not None else body_hash(b)}


def kw(**over):
    d = {"now": NOW, "action": "sim_rebalance_order", "notional": 4000,
         "cumulative": 0, "available": 100000}
    d.update(over)
    return d


def load_variant(code):
    ns = {"__name__": "paguard_variant", "__file__": str(SRC)}
    exec(compile(code, str(SRC), "exec"), ns)  # noqa: S102 - 本仓库脚本源码
    return ns


def sig(ns, d, k):
    r = ns["check"](doc=d, **k)
    return (r["decision"], r["reason"])


# (编号, 描述, 原文, 变异, 授权文档, 请求参数, 原版应得的拒绝理由)
MUTATIONS = [
    ("M1", "schema 校验失效",
     'c1 = doc["schema_version"] == SCHEMA_VERSION',
     'c1 = True',
     doc(schema_version="paper_auth_v0"), kw(), "SCHEMA"),

    ("M2", "通道校验失效（live 也放行）",
     'c2 = doc["channel"] == ONLY_CHANNEL',
     'c2 = True',
     doc(channel="live"), kw(), "CHANNEL"),

    ("M3", "有效期校验失效",
     'c3 = now_t <= until_t',
     'c3 = True',
     doc(), kw(now="2026-12-03T00:00:00+08:00"), "EXPIRY"),

    ("M4", "动作范围校验失效",
     'action in FROZEN_SCOPES and action in scopes',
     'True',
     doc(scopes=["query_cash"]), kw(), "SCOPE"),

    ("M5", "金额双校失效（累计超限也放行）",
     'cap <= avail and n <= cap and cum + n <= cap',
     'True',
     doc(), kw(cumulative=99000), "NOTIONAL"),

    ("M6", "哈希校验失效",
     'hmac.compare_digest(compute_hash(body), doc["sha256"])',
     'True',
     {**doc(), "sha256": "0" * 64}, kw(), "HASH"),

    ("M7", "文件缺失不再失败关闭",
     '    if not isinstance(doc, dict):\n        return _result(DENY, NO_AUTH)',
     '    if not isinstance(doc, dict):\n        return _result(ALLOW, None)',
     None, kw(), base.NO_AUTH),

    ("M8", "时间不可解析不再失败关闭",
     '    except (ValueError, TypeError, AttributeError):\n'
     '        return _result(DENY, NO_AUTH)',
     '    except (ValueError, TypeError, AttributeError):\n'
     '        return _result(ALLOW, None)',
     doc(), kw(now="not-a-date"), base.NO_AUTH),

    ("M9", "非法金额/资金不再失败关闭",
     '    if not (cap > 0 and avail > 0 and n > 0 and cum >= 0):\n'
     '        return _result(DENY, NO_AUTH)',
     '    if not (cap > 0 and avail > 0 and n > 0 and cum >= 0):\n'
     '        return _result(ALLOW, None)',
     doc(), kw(available=0), base.NO_AUTH),

    ("M10", "哈希格式错不再失败关闭",
     '    if not isinstance(doc.get("sha256"), str) or not HEX64.match(doc["sha256"]):\n'
     '        return _result(DENY, NO_AUTH)',
     '    if not isinstance(doc.get("sha256"), str) or not HEX64.match(doc["sha256"]):\n'
     '        return _result(ALLOW, None)',
     doc(sha256="bad"), kw(), base.NO_AUTH),
]


def main() -> int:
    base_ns = vars(base)
    detected = 0
    for mid, desc, old, new, d, k, reason in MUTATIONS:
        if old not in ORIGINAL:
            print(f"[{mid}] ✗ 变异定义失效：替换文本未命中源码")
            return 1
        if ORIGINAL.count(old) != 1:
            print(f"[{mid}] ✗ 替换文本不唯一（{ORIGINAL.count(old)} 处），无法定位")
            return 1
        b = sig(base_ns, d, k)
        if b != (base.DENY, reason):
            print(f"[{mid}] ✗ 测试用例无效：原版签名为 {b}，期望 DENY:{reason}")
            return 1
        v = sig(load_variant(ORIGINAL.replace(old, new, 1)), d, k)
        if v == b or v[0] != base.ALLOW:
            print(f"[{mid}] ✗ 变异未被检出或只是换了个拒绝理由：{desc}（{b}→{v}）")
            return 1
        detected += 1
        print(f"[{mid}] ✓ 检出：{desc}（{b}→{v}）")

    print(f"[mutation] {detected}/{len(MUTATIONS)} 检出；全部变异均真改行为（DENY→ALLOW）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
