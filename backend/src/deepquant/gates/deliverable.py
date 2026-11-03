"""把 L3v 结论卡片渲染成对外定稿，并守住定稿里的每一个数字。

这一层解决的是「抄写」而不是「计算」：

- L3v 保证**卡片**里的数字等于 L3u 的重算值；
- L3y 保证**对外文档**里的数字等于卡片，或者等于某个源文件按指针实算的值。

两者防的错误不同。本项目历史上的数字事故全部发生在「从产物抄到另一个文件」这一步
（pp÷小数、年化当月度、引另一次重跑、取格 A 自身水平），没有一次发生在计算里。

设计上刻意不接受「文档里写了多少就是多少」：facts 文件里的 ``display`` 只是**断言**，
真正的值每次都从 ``source.path`` 指向的文件按指针重新取一遍。源文件改了而指针不动，
就报 ``FACT_DRIFT``。这样「文档—facts—源文件」三者互相钉住。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import claims as clm

EXPERIMENT_ID = "H-L3Y-DELIVERABLE"
DELIVERABLE_ERROR = "DELIVERABLE_INVALID"

#: 可以向外部表述的分级（含零效应判定）。
CLIP_TIERS = ("QUOTABLE", "QUOTABLE_MARGINAL", "NULL_CONFIRMED")
#: 单条显著、但在多重检验校正下未通过：只能记为待验证的方向性观察，
#: **不得**表述为已得到统计支持的方向规律。
EXPLORATORY_TIERS = ("EXPLORATORY",)
#: 表格里按此顺序渲染，避免每次运行顺序漂移。
TIER_ORDER = (
    "QUOTABLE",
    "QUOTABLE_MARGINAL",
    "EXPLORATORY",
    "NULL_CONFIRMED",
    "UNDERPOWERED",
    "NOT_GRADED",
)
#: 各档能不能对外。与 CLIP_TIERS 必须一致，单测钉住。
CLIP_VERDICT = {
    "QUOTABLE": "可以，须连同限定",
    "QUOTABLE_MARGINAL": "可以，须同列 MDE 与限定",
    "EXPLORATORY": "仅可记为待验证的方向性观察，不得说成已确认",
    "NULL_CONFIRMED": "可以，作为「确认无效应」引用",
    "UNDERPOWERED": "不可，只能说「无法判断」",
    "NOT_GRADED": "不可，核对未通过",
}

#: 封印条目结论句的最短可判长度。太短（「无法判断」这类）做子串匹配会误伤。
MIN_LEAK_CHARS = 12

#: 口径常量：定义、门槛、方法参数。它们不是观测到的统计量，无法「从源文件实算」，
#: 只能从本项目的口径定义里取。这张表**必须保持极短**——每加一条就是开一个后门，
#: 所以每条都要写清理由，并在产物里逐条列出供人工复核。
SCHEMA_TOKENS: dict[str, str] = {
    "0.5": "d 定义的等权系数（0.5*long-0.5*short），取自 claims.D_DEFINITIONS 的字面",
    "0": "统计零点（「区间跨 0」「净 alpha 趋于 0」），不是观测值",
    "1": "分级门槛 1×（QUOTABLE_MARGINAL 下界）",
    "2": "分级门槛 2×（QUOTABLE 下界），也是 spread 与 d 的倍数关系",
    "3": "NW 估计量的默认滞后阶数，方法参数。它原先靠 DIRECTION_ONLY 的计数偶然放行——"
    "分级分布一变定稿就会拒绝生成，必须给它自己的出处",
    "95": "bootstrap 置信水平，方法参数",
}

#: 引用块里的 ``n=...``。window_note 与来源行都写成这个形式。
N_EQ = re.compile(r"n=(\d+)")
#: 做「结论句是否被抄进定稿」判断时的归一化：只去壳，不改字。
_FLAT_DROP = re.compile(r"[\s，。、：；（）()「」【】`*_\-—|>]")


class DeliverableError(ValueError):
    """定稿装配的输入不合法（与「闸门检出违规」是两件事）。"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_facts(path: str | Path) -> dict[str, Any]:
    """读事实表并做结构校验。值本身不在这里校验——那要看源文件。"""
    p = Path(path)
    if not p.exists():
        raise DeliverableError(f"事实表不存在：{p}")
    payload = json.loads(p.read_text(encoding="utf-8"))
    facts = payload.get("facts")
    if not isinstance(facts, list) or not facts:
        raise DeliverableError("事实表必须包含非空 facts 列表")
    seen: set[str] = set()
    for fact in facts:
        for field in ("key", "display", "source", "note"):
            if field not in fact:
                raise DeliverableError(f"事实 {fact.get('key')!r} 缺字段 {field}")
        if fact["key"] in seen:
            raise DeliverableError(f"事实 key 重复：{fact['key']}")
        seen.add(fact["key"])
        src = fact["source"]
        if "path" not in src:
            raise DeliverableError(f"事实 {fact['key']} 的 source 缺 path")
        if ("pointer" in src) == ("pointers" in src):
            raise DeliverableError(
                f"事实 {fact['key']} 的 source 必须且只能有 pointer 或 pointers 之一"
            )
        if "pointers" in src and src.get("op") != "sum":
            raise DeliverableError(f"事实 {fact['key']} 的 pointers 必须配 op=sum")
        if "pointer" in src and src.get("op") not in (None, "first_nonzero_key"):
            raise DeliverableError(f"事实 {fact['key']} 的 op 不受支持：{src.get('op')}")
    return payload


def _walk(obj: Any, pointer: str) -> Any:
    """点路径取值。缺失就抛 KeyError —— 静默返回 None 会让比对假通过。"""
    cur = obj
    for part in pointer.split("."):
        if not isinstance(cur, Mapping):
            raise KeyError(f"{pointer}：{part} 之前不是对象")
        if part not in cur:
            raise KeyError(f"{pointer}：缺 {part}")
        cur = cur[part]
    return cur


def _resolve(payload: Any, source: Mapping[str, Any]) -> Any:
    if "pointer" in source:
        return _walk(payload, source["pointer"])
    values = [_walk(payload, p) for p in source["pointers"]]
    if not values:
        raise KeyError("pointers 为空")
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in values):
        raise KeyError("pointers 里有非数值")
    return sum(values)


def format_value(value: float, decimals: int = 0, scale: float = 1.0) -> str:
    return f"{float(value) * float(scale):.{int(decimals)}f}"


def verify_facts(
    facts: Sequence[Mapping[str, Any]], root: str | Path
) -> tuple[list[dict], list[dict]]:
    """逐条回到源文件实算。返回 (verified, problems)。"""
    root = Path(root)
    verified: list[dict] = []
    problems: list[dict] = []
    for fact in facts:
        src = fact["source"]
        path = root / src["path"]
        if not path.exists():
            problems.append(
                {
                    "code": "FACT_SOURCE_MISSING",
                    "fact": fact["key"],
                    "detail": f"源文件不存在：{src['path']}",
                }
            )
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - 数据层坏了应早发现
            problems.append(
                {"code": "FACT_SOURCE_UNREADABLE", "fact": fact["key"], "detail": str(exc)}
            )
            continue
        try:
            if src.get("op") == "first_nonzero_key":
                # 年份是「键」不是值，因此不能走 format_value——它只接受数值。
                # 取第一个非零年份，让「样本起点」这类说法也有可实算的出处。
                table = _walk(payload, src["pointer"])
                if not isinstance(table, Mapping):
                    raise KeyError(f"{src['pointer']} 不是键-值表")
                keys = [k for k, v in sorted(table.items()) if v]
                if not keys:
                    raise KeyError(f"{src['pointer']} 没有非零项")
                value: Any = keys[0]
                got = str(value)
            else:
                value = _resolve(payload, src)
                got = format_value(value, fact.get("decimals", 0), fact.get("scale", 1.0))
        except KeyError as exc:
            problems.append(
                {"code": "FACT_POINTER_MISSING", "fact": fact["key"], "detail": str(exc)}
            )
            continue
        if clm._norm(got) != clm._norm(fact["display"]):
            problems.append(
                {
                    "code": "FACT_DRIFT",
                    "fact": fact["key"],
                    "detail": f"事实表写 {fact['display']}，从 {src['path']} 实算得 {got}",
                }
            )
            continue
        verified.append(
            {**fact, "value": value, "computed": got, "source_sha256": _sha256(path)}
        )
    return verified, problems


def collect_allowed(
    claims_payload: Mapping[str, Any],
    l3v: Mapping[str, Any],
    l3u_rows: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """定稿允许出现的数字，以及每个数字的出处。

    只有三个来源，都独立于定稿文本本身：卡片已核准数字、facts 实算值、产物里的结构计数。
    ``allowed`` 全局有效（不区分位置）——这是已知缺口，写在报告限制节。
    """
    allowed: dict[str, list[str]] = {}

    def add(raw: object, reason: str) -> None:
        token = clm._strip_sign(str(raw))
        if not re.fullmatch(r"\d+(?:\.\d+)?", token):
            return
        bucket = allowed.setdefault(token, [])
        if reason not in bucket:
            bucket.append(reason)

    for claim in claims_payload["claims"]:
        for num in claim.get("numbers") or []:
            add(num["display"], f"card:{claim['key']}:{num['field']}")
        for lit in claim.get("literals") or []:
            add(lit["display"], f"literal:{claim['key']}")

    for fact in facts:
        add(fact["display"], f"fact:{fact['key']}")

    tier_counts = l3v.get("tier_counts") or {}
    for tier in TIER_ORDER:
        if tier in tier_counts:
            add(tier_counts[tier], f"tier_counts:{tier}")
    add(sum(v for k, v in tier_counts.items() if k in CLIP_TIERS), "tier_counts:clip_total")
    for field in ("n_claims", "n_l3u_rows", "n_independent_numbers", "n_quoted_via_unseal"):
        if l3v.get(field) is not None:
            add(l3v[field], f"l3v:{field}")

    for row in l3u_rows:
        for raw in (row.get("series_n"), (row.get("recomputed") or {}).get("n")):
            if isinstance(raw, int) and not isinstance(raw, bool):
                add(raw, f"n:{row.get('key')}")

    for token, reason in SCHEMA_TOKENS.items():
        add(token, f"schema:{reason}")

    return allowed


def render_claim_block(claim: Mapping[str, Any], row: Mapping[str, Any]) -> str:
    """渲染一条可引用结论。

    刻意**不**渲染 ``window_note`` 与 ``quotability_note``：

    - 前者的样本数用行里的实算 ``series_n`` 代替，字符串里的 n 不受管；
    - 后者是内部审计记录（讲的是当初核对错了什么），里面的比值有 3 位小数，
      与卡片登记的 2 位不一致，放进对外文档只会制造第二个版本的数字。
    """
    lines = [
        f"**`{claim['key']}`**（{row['family']}，单位 {row['unit']}）",
        f"> {claim['headline']}",
        "",
        f"- 窗口：{claim['window']}",
        f"- 样本数：n={row['series_n']}",
        f"- 判定估计量：{claim['estimator_for_verdict']}",
    ]
    if claim.get("d_definition") and claim["d_definition"] != "not_applicable":
        lines.append(f"- d 定义：{claim['d_definition']}")
    if claim.get("cost_id"):
        lines.append(f"- 成本口径：{claim['cost_id']}")
    if claim.get("unit_note"):
        lines.append(f"- 单位说明：{claim['unit_note']}")
    for limit in claim.get("limits_of_use") or []:
        lines.append(f"- 限定：{limit}")
    lines.append(f"- 可引用强度：{claim['quotability']}")
    basis = str(claim.get("tier_basis") or "l3u_grade")
    if basis == "l3u_grade":
        lines.append("- 分级来源：最初核对即通过")
    elif basis == "l3z_audit":
        lines.append(
            "- 分级来源：L3z 审计降级（单条显著，但在把全部结论作为一组同时报告的多重检验下未通过；"
            "原分级见「分级依据」）"
        )
    else:
        lines.append(
            f"- 分级来源：`{basis}`（最初核对判为「核对未通过」，复核证明判定量与序列同源，"
            "且分级在标准误的滞后网格上不跨档）"
        )
    return "\n".join(lines)


def scan_numbers(text: str, allowed: Mapping[str, Sequence[str]]) -> list[dict]:
    """抽全文数字，逐个查出处。同一个未登记数字只报一次（附出现次数）。"""
    counts: dict[str, int] = {}
    for token in clm.NUM_TOKEN.findall(text):
        bare = clm._strip_sign(token)
        if bare in allowed:
            continue
        counts[bare] = counts.get(bare, 0) + 1
    return [
        {
            "code": "UNSOURCED_NUMBER",
            "token": token,
            "detail": (
                f"数字 {token} 出现 {n} 次，既不在卡片已核准数字、"
                "也不在 facts 实算值或产物结构计数里"
            ),
        }
        for token, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _flatten(text: str) -> str:
    return _FLAT_DROP.sub("", text)


def check_sealed_leak(md: str, sealed_claims: Sequence[Mapping[str, Any]]) -> list[dict]:
    """封印条目的结论句不得以任何形式出现在定稿里。"""
    flat = _flatten(md)
    out: list[dict] = []
    for claim in sealed_claims:
        headline = claim.get("headline") or ""
        fh = _flatten(headline)
        if len(fh) < MIN_LEAK_CHARS:
            continue
        if fh in flat:
            out.append(
                {
                    "code": "SEALED_LEAK",
                    "claim_key": claim["key"],
                    "detail": f"封印条目的结论句出现在定稿里：{headline[:40]}…",
                }
            )
    return out


def check_window_n(md: str, allowed_ns: Sequence[int]) -> list[dict]:
    """引用块里的 ``n=...`` 必须等于某条序列的实算样本数。"""
    ok = {str(v) for v in allowed_ns}
    out: list[dict] = []
    for token in N_EQ.findall(md):
        if token not in ok:
            out.append(
                {
                    "code": "WINDOW_N_MISMATCH",
                    "token": token,
                    "detail": f"定稿写 n={token}，但没有任何序列的实算 n 等于它",
                }
            )
    return out


def check_tier_table(md: str, tier_counts: Mapping[str, int]) -> list[dict]:
    """分级表必须逐行等于冻结产物里的计数——手抄计数最容易漂。"""
    out: list[dict] = []
    for tier, expected in tier_counts.items():
        pattern = re.compile(r"\|\s*`?" + re.escape(tier) + r"`?\s*\|\s*(\d+)\s*\|")
        found = pattern.search(md)
        if not found:
            out.append(
                {"code": "TIER_ROW_MISSING", "tier": tier, "detail": f"分级表缺 {tier} 这一行"}
            )
            continue
        got = int(found.group(1))
        if got != expected:
            out.append(
                {
                    "code": "TIER_COUNT_DRIFT",
                    "tier": tier,
                    "detail": f"{tier} 冻结产物记 {expected}，定稿表里写 {got}",
                }
            )
    return out


def check_tier_recount(
    claims_payload: Mapping[str, Any], tier_counts: Mapping[str, int]
) -> dict[str, Any]:
    """卡片重算分级 vs 冻结产物。不等就是「改了卡片没重跑 L3v」。"""
    recount: dict[str, int] = {}
    for claim in claims_payload["claims"]:
        tier = claim["quotability"]
        recount[tier] = recount.get(tier, 0) + 1
    drift = {
        tier: {"card": recount.get(tier, 0), "frozen": tier_counts.get(tier, 0)}
        for tier in set(recount) | set(tier_counts)
        if recount.get(tier, 0) != tier_counts.get(tier, 0)
    }
    return {
        "recount": recount,
        "drift": drift,
        "ok": not drift,
        "violations": [
            {
                "code": "TIER_RECOUNT_DRIFT",
                "tier": tier,
                "detail": f"{tier}：卡片重算 {v['card']}，冻结产物 {v['frozen']}",
            }
            for tier, v in sorted(drift.items())
        ],
    }


def sealed_reason_codes(
    sealed_claims: Sequence[Mapping[str, Any]],
    l3u_rows: Sequence[Mapping[str, Any]],
    unseal_decisions: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """封印原因码全部取自产物，不由定稿手写。"""
    by_key = {row["key"]: row for row in l3u_rows}
    out: dict[str, str] = {}
    for claim in sealed_claims:
        key = claim["key"]
        decision = unseal_decisions.get(key)
        if decision and decision.get("class"):
            out[key] = decision["class"]
            continue
        cmp_class = (by_key.get(key, {}).get("comparison") or {}).get("class")
        out[key] = cmp_class or "UNKNOWN"
    return out


def decision_index(sources: Mapping[str, str], root: str | Path) -> dict[str, dict]:
    """从解封复核产物取 key → decision。文件缺失就当没有（原因码退回 L3u）。"""
    root = Path(root)
    out: dict[str, dict] = {}
    for label, rel in sources.items():
        if not label.startswith("unseal"):
            continue
        path = root / rel
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:  # pragma: no cover
            continue
        for item in payload.get("decisions") or []:
            out.setdefault(item["key"], item)
    return out


def render_document(
    *,
    claims_payload: Mapping[str, Any],
    l3u: Mapping[str, Any],
    l3v: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    tier_counts: Mapping[str, int],
    reason_codes: Mapping[str, str],
) -> str:
    """渲染定稿。出现的每个数字都必须已在 ``collect_allowed`` 里。"""
    rows = {row["key"]: row for row in l3u["rows"]}
    clip = [c for c in claims_payload["claims"] if c["quotability"] in CLIP_TIERS]
    exploratory = [
        c for c in claims_payload["claims"] if c["quotability"] in EXPLORATORY_TIERS
    ]
    sealed = [c for c in claims_payload["claims"] if c["quotability"] == "NOT_GRADED"]
    weak = [c for c in claims_payload["claims"] if c["quotability"] == "UNDERPOWERED"]
    clip_total = len(clip)
    exploratory_total = len(exploratory)
    sealed_total = len(sealed)
    weak_total = len(weak)

    lines: list[str] = []
    lines.append("# 对外表述定稿：可引用的结论与必须封印的结论")
    lines.append("")
    lines.append(
        "> 这份文件的唯一用途是**当抄写来源**。写材料、做汇报、跟外部沟通时，"
        "结论句与限定从下面直接取，不要另行组织措辞。"
    )
    lines.append(">")
    lines.append(
        "> 每个数字都在生成时被机器核对过：要么等于结论卡片里已核准的数字，"
        "要么等于某个源文件按指针重新取出来的实算值。手写改一个数字，文件就会拒绝生成。"
    )
    lines.append("")
    lines.append("## 一、一页看完")
    lines.append("")
    lines.append(
        f"登记在册的结论共 {l3v['n_claims']} 条，按可引用强度分成六档："
    )
    lines.append("")
    lines.append("| 分级 | 条数 | 能否对外 |")
    lines.append("| --- | --- | --- |")
    for tier in TIER_ORDER:
        if tier in tier_counts:
            lines.append(f"| `{tier}` | {tier_counts[tier]} | {CLIP_VERDICT[tier]} |")
    lines.append("")
    lines.append(
        f"可以对外表述的是前两档加零效应判定，合计 **{clip_total} 条**；"
        f"另有 **{exploratory_total} 条**只能记为探索性观察（单条显著、但在同时报告全族的"
        f"多重检验校正下未通过）；其余 {sealed_total} 条核对未通过、{weak_total} 条检验本身没有"
        "分辨力——这两类都不给数字，只能说明「为什么不给」。"
    )
    lines.append("")
    lines.append(
        f"⚠️ 这 {l3v['n_claims']} 条结论只对应 **{l3v['n_independent_numbers']} 份独立数字**"
        "——有几组结论判定不同、但数字逐位相同。看着像「多轮反复验证过」，"
        "其实是一次测量被引用了几次。把同一份数字的几条结论并列，不构成交叉证据。"
    )
    lines.append("")

    lines.append("## 二、数据与口径边界")
    lines.append("")
    lines.append("所有结论都在同一套数据层上产生。下面这几个规模决定了它们能覆盖到哪：")
    lines.append("")
    for fact in facts:
        lines.append(f"- **{fact['note']}**：{fact['display']}")
    lines.append("")
    lines.append("口径上有三条硬约束，任何引用都不能绕开：")
    lines.append("")
    lines.append("- **窗口**：判定窗口与事后重跑的窗口不是一回事，跨窗口引用等于换实验。")
    lines.append(
        "- **估计量**：iid 与 NW(3) 的 t 在同一条序列上可以差到改变结论，"
        "只引其中一个等于挑显著性。"
    )
    lines.append(
        "- **成本**：净 alpha 必须带成本编号（资金档 + 费率档），"
        "「成本后」三个字本身不构成口径。"
    )
    lines.append("")

    lines.append(f"## 三、可对外引用（{clip_total} 条）")
    lines.append("")
    lines.append(
        "每条都自带窗口、估计量、d 定义、限定与序列出处。"
        "**限定不是免责声明，是这句话成立的条件**——删掉限定，这句话就不成立。"
    )
    lines.append("")
    for claim in clip:
        lines.append(f"### `{claim['key']}`｜`{claim['quotability']}`")
        lines.append("")
        lines.append(render_claim_block(claim, rows[claim["key"]]))
        lines.append("")

    if exploratory:
        lines.append(f"## 四、探索性观察（{exploratory_total} 条）")
        lines.append("")
        lines.append(
            "这几条**单条检验是显著的**，但在把全部结论作为一组同时报告时，"
            "它们没能通过多重检验校正。因此："
        )
        lines.append("")
        lines.append(
            "- 可以记为「观察到了某个方向」，"
            "**不能**表述为「已得到统计支持的方向规律」；"
        )
        lines.append(
            "- 不能据此做决策，也不能把它写成可交易的依据；"
        )
        lines.append(
            "- 它**不等于**「效应不存在」——只是现有证据不足以支撑确认。"
        )
        lines.append("")
        lines.append(
            "原分级为 `DIRECTION_ONLY`，本节是 L3z 审计后的修订结果，"
            "修改理由记在每张卡片的「分级依据」里。"
        )
        lines.append("")
        for claim in exploratory:
            lines.append(f"### `{claim['key']}`｜`{claim['quotability']}`")
            lines.append("")
            lines.append(render_claim_block(claim, rows[claim["key"]]))
            lines.append("")

    lines.append(f"## 五、不给数字的两类（{sealed_total} + {weak_total} 条）")
    lines.append("")
    lines.append(
        "这两类的处理看起来一样（都不给数字），原因完全不同，混在一起讲会错判性质。"
    )
    lines.append("")
    lines.append(f"### 封印：核对未通过（{sealed_total} 条）")
    lines.append("")
    lines.append("判定用的统计量与重建出来的序列**不是同一个东西**，所以连比值都不该算：")
    lines.append("")
    lines.append(
        "- **不同源**：不同资金档的序列、回归系数而非均值、或根本没有可核对的分母。"
        "把两者的 mean 与 MDE 拼在一起算比值，是在混用口径。"
    )
    lines.append(
        "- **非单一均值**：判定由多门槛规则给出，没有单一的 mean 可以拿来分级。"
        "这不等于结论错，只等于它不适合按比值引用。"
    )
    lines.append("")
    lines.append("| 结论 | 族 | 封印原因码 |")
    lines.append("| --- | --- | --- |")
    for claim in sealed:
        row = rows.get(claim["key"]) or {}
        lines.append(
            f"| `{claim['key']}` | {row.get('family', '')} | `{reason_codes[claim['key']]}` |"
        )
    lines.append("")
    lines.append(
        "要解封其中任何一条，必须先把它对应的序列单独导出成新实验、并给出新的预登记。"
        "不能在本文档里就地补一个数字。"
    )
    lines.append("")
    lines.append(f"### 无分辨力：检验本身不携带信息（{weak_total} 条）")
    lines.append("")
    lines.append(
        "这一批的数字**可能是对的**，但最小可检测效应大于效应本身——用这个检验去问"
        "「有没有效应」，它答不了。因此只能引「无法判断」，不能引量级，"
        "**也不能反过来说「无效应」**：没有证据不等于证据表明没有。"
    )
    lines.append("")
    lines.append("| 结论 | 族 |")
    lines.append("| --- | --- |")
    for claim in weak:
        row = rows.get(claim["key"]) or {}
        lines.append(f"| `{claim['key']}` | {row.get('family', '')} |")
    lines.append("")

    lines.append("## 六、引用规则（三条反例）")
    lines.append("")
    lines.append(
        "**规则一：谈量级时必须同列 MDE。** 判别标准是「效应 / MDE」这个比值，"
        "不是 t 值，也不是 p 值。t 值大只说明「不像随机」，不说明「大得能派上用场」。"
    )
    lines.append("")
    lines.append(
        "**规则二：iid 与 NW(3) 的 t 必须同列。** 反例：某条结论判定用的是 iid，"
        "而整理材料时只抄了 NW(3) 的 t——两个数都真实存在，"
        "但读者会以为这是同一次检验的两个说法。"
    )
    lines.append("")
    lines.append(
        "**规则三：分级来源必须标明。** 分级来源有三种，不能一律写成「一开始就通过」："
        "**最初核对通过**、**解封复核**（曾有「核对未通过」的判定，复核证明判定量与序列同源）、"
        "**审计降级**（单条显著但在全族多重检验下未通过，因此只能当探索性观察）。"
    )
    lines.append("")
    lines.append("**反例（看上去无害，实际会让整条结论失效）：**")
    lines.append("")
    lines.append(
        "- 把 `QUOTABLE_MARGINAL` 说成「显著」——它只表示可以引用，不表示量级够大。"
    )
    lines.append(
        "- 把横截面月度差距说成组合收益——前者是两格之差，后者要过一次现金流与整手约束。"
    )
    lines.append("- 拿同一份数字的几条结论互相印证——它们本来就只有一个来源。")
    lines.append("")

    lines.append("## 七、已知缺口")
    lines.append("")
    lines.append(
        "这些是本文件自身的局限，不是结论的局限。可以照抄本文件，"
        "但不应当把本文件当成「已经全部核对过」的证明。"
    )
    lines.append("")
    lines.append(
        "- **数字查重不区分位置。** 校验只能判断「这个数字有没有出处」，"
        "不能判断「这个位置的数字是否用对了出处」。"
        "一个恰好等于别处合法值的手误可以蒙混过关。"
    )
    lines.append(
        "- **只校验结论句与结构计数。** 引用块里的限定文字本身不受数字校验；"
        "限定被删掉不会被机器发现。"
    )
    lines.append(
        "- **孤立数字无法消歧。** 同一个数值在多处出现时，机器无法分辨指的是哪一个量。"
    )
    lines.append(
        "- **口径常量表是一处有限的放行口。** d 定义的等权系数、分级门槛、"
        "置信水平、统计零点这类数字无法从数据实算，只能白名单放行；"
        "每一条都在产物里列出理由，供人工复核。这张表越短越好。"
    )
    lines.append(
        "- **本文件不构成样本外证据。** 全部结论都在历史区间内产生，"
        "没有任何前瞻期数据参与。"
    )
    lines.append("")

    lines.append("## 八、复现")
    lines.append("")
    lines.append("```bash")
    lines.append("PYTHONPATH=backend/src python scripts/l3y_deliverable.py")
    lines.append("```")
    lines.append("")
    lines.append(
        "生成时先校验预登记文件未被改动，再回到源文件实算全部事实项，"
        "最后扫描全文数字。任一步不过就不写出文件。"
    )
    lines.append("")
    lines.append(
        "本文件由脚本生成，**不要手工编辑**。路径、sha256 与逐项数字出处记在"
        "同名产物目录的 JSON 里。"
    )
    lines.append("")
    return "\n".join(lines)


def assemble(
    *,
    claims_payload: Mapping[str, Any],
    l3u: Mapping[str, Any],
    l3v: Mapping[str, Any],
    facts_payload: Mapping[str, Any],
    root: str | Path,
    prereg_sha: str,
    sources: Mapping[str, str],
) -> dict[str, Any]:
    """装配并过闸门。返回结构化结果，不写文件。"""
    root = Path(root)
    facts, problems = verify_facts(facts_payload["facts"], root)
    allowed = collect_allowed(claims_payload, l3v, l3u["rows"], facts)
    tier_counts = l3v.get("tier_counts") or {}
    recount = check_tier_recount(claims_payload, tier_counts)

    rows = {row["key"]: row for row in l3u["rows"]}
    # 「不给数字」= 既不进可引用章节、也不进探索性章节。
    # EXPLORATORY 是要渲染完整结论句的（只是不能说成已确认），不能算在这一类里，
    # 否则 SEALED_LEAK 会把它自己的结论句判成泄漏。
    no_number = [
        c
        for c in claims_payload["claims"]
        if c["quotability"] not in CLIP_TIERS and c["quotability"] not in EXPLORATORY_TIERS
    ]
    sealed = [c for c in no_number if c["quotability"] == "NOT_GRADED"]
    weak = [c for c in no_number if c["quotability"] == "UNDERPOWERED"]
    codes = sealed_reason_codes(sealed, l3u["rows"], decision_index(sources, root))

    md = render_document(
        claims_payload=claims_payload,
        l3u=l3u,
        l3v=l3v,
        facts=facts,
        tier_counts=tier_counts,
        reason_codes=codes,
    )

    allowed_ns: list[int] = []
    for row in l3u["rows"]:
        for raw in (row.get("series_n"), (row.get("recomputed") or {}).get("n")):
            if isinstance(raw, int) and not isinstance(raw, bool):
                allowed_ns.append(raw)

    violations: list[dict] = []
    violations.extend(problems)
    violations.extend(recount["violations"])
    violations.extend(scan_numbers(md, allowed))
    violations.extend(check_sealed_leak(md, no_number))
    violations.extend(check_window_n(md, allowed_ns))
    violations.extend(check_tier_table(md, tier_counts))

    counts: dict[str, int] = {}
    for v in violations:
        counts[v["code"]] = counts.get(v["code"], 0) + 1

    return {
        "experiment_id": EXPERIMENT_ID,
        "prereg_sha256": prereg_sha,
        "status": "DELIVERABLE_CONSISTENT" if not violations else "DELIVERABLE_INCONSISTENT",
        "facts": [
            {
                "key": f["key"],
                "note": f["note"],
                "display": f["display"],
                "computed": f["computed"],
                "source": f["source"],
                "source_sha256": f["source_sha256"],
            }
            for f in facts
        ],
        "n_facts": len(facts),
        "n_allowed_tokens": len(allowed),
        "allowed_tokens": dict(sorted(allowed.items())),
        "tier_recount": recount["recount"],
        "tier_counts_frozen": dict(tier_counts),
        "n_clip_claims": len(
            [c for c in claims_payload["claims"] if c["quotability"] in CLIP_TIERS]
        ),
        "n_sealed_claims": len(sealed),
        "n_weak_claims": len(weak),
        "sealed_reason_codes": codes,
        "schema_tokens": SCHEMA_TOKENS,
        "n_violations": len(violations),
        "violation_counts": counts,
        "violations": violations,
        "markdown": md,
    }
