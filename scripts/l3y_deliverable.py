#!/usr/bin/env python
"""L3y：把可引用的结论渲染成对外定稿，并守住定稿里的每个数字。

与 L3v 的分工：
- L3v 守「卡片里的数字 == L3u 重算值」；
- L3y 守「对外文档里的数字 == 卡片数字 或 源文件实算值」。

用法：
    PYTHONPATH=backend/src python scripts/l3y_deliverable.py
    PYTHONPATH=backend/src python scripts/l3y_deliverable.py --quiet
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

from deepquant.gates import deliverable as dlv

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "gates" / "H-L3Y-DELIVERABLE"
DELIVERABLE = ROOT / "docs" / "DELIVERABLE_QUOTE_SHEET.md"

PREREG = ROOT / "docs" / "gates" / "l3y_prereg.json"
PREREG_SHA = "3b4e1bb334c4572c41150a1176fec969a2aa5f4a2014bd99395585961c800f35"

FACTS = ROOT / "docs" / "gates" / "l3y_facts.json"
CARDS = ROOT / "docs" / "gates" / "l3v_claims.json"
L3V_RESULT = ROOT / "reports" / "gates" / "H-L3V-CLAIM-REGISTRY" / "l3v_claims.json"
L3U_RESULT = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
UNSEAL_L3W = ROOT / "reports" / "gates" / "H-L3W-UNSEAL" / "l3w_unseal.json"
UNSEAL_L3X = ROOT / "reports" / "gates" / "H-L3X-TONLY" / "l3x_unseal.json"

FROZEN = {
    "facts": (FACTS, "7cec45b00634708420b0f72cbc274bec3f5839346377a528365221de35d5cb11"),
    "claims_cards": (CARDS, "5b4173c7265e5dd5be0cbf37b9af8620ed3488ea3480224d249a91740b1317b0"),
    "l3v_result": (L3V_RESULT, "8cc5f64883b6797af535ed43e0d2b1d508199e745550fc3466a2a0fcc9be59c7"),
    "l3u_result": (L3U_RESULT, "f7d1a66d64c386e1a9c14e717c1606f9d931d2f789230d470a37cc83ce5c6e6b"),
    "unseal_l3w": (UNSEAL_L3W, "ce10baa7dabb1e1219177448e7e4ba6d1d81769cbcbde40c8dae85860445b6c0"),
    "unseal_l3x": (UNSEAL_L3X, "e70703b2f69072194f943a8ab64abe4a7377534dc6a82948061d34623422e5c7"),
}

SOURCES = {key: path.relative_to(ROOT).as_posix() for key, (path, _) in FROZEN.items()}

#: 首跑实测的未登记数字处数（当时仪器有缺陷、卡片也有 1 处真错）。
#: 保留这个数是为了让 P4 维持「被反驳」——修完仪器后重跑归零不等于预测成立。
FIRSTRUN_UNSOURCED = 20


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_frozen() -> None:
    problems = []
    if not PREREG.exists():
        raise SystemExit(f"预登记不存在：{PREREG}")
    actual = _sha(PREREG)
    if actual != PREREG_SHA:
        raise SystemExit(
            f"预登记已被改动：期望 {PREREG_SHA}，实际 {actual}；拒绝运行。"
            " 若确需修订，先写入 amendments 并更新脚本里的 sha。"
        )
    for key, (path, expected) in FROZEN.items():
        if not path.exists():
            problems.append(f"{key}: 文件不存在 {path.relative_to(ROOT)}")
            continue
        got = _sha(path)
        if got != expected:
            problems.append(
                f"{key}: 期望 {expected[:12]}…，实际 {got[:12]}…（{path.relative_to(ROOT)}）"
            )
    if problems:
        raise SystemExit("冻结输入已被改动，拒绝运行：\n  " + "\n  ".join(problems))


def _sealed_source_split(claims_payload, l3u, decisions) -> dict[str, int]:
    """封印原因码的出处分布：来自解封复核 vs 回落到 L3u。

    「封印」只指 NOT_GRADED（核对未通过）。UNDERPOWERED 是另一类（无分辨力），
    按预登记 A3 不算封印，混进来会把分布算成 8/10 而不是 3/3。
    """
    sealed = [c for c in claims_payload["claims"] if c["quotability"] == "NOT_GRADED"]
    out = {"unseal": 0, "l3u": 0}
    for claim in sealed:
        out["unseal" if claim["key"] in decisions else "l3u"] += 1
    return out


def _check_predictions(result, claims_payload, l3u, decisions) -> list[dict]:
    counts = result["violation_counts"]
    tier = result["tier_counts_frozen"]
    split = _sealed_source_split(claims_payload, l3u, decisions)
    unseal_based = sum(
        1
        for c in claims_payload["claims"]
        if c["quotability"] in dlv.CLIP_TIERS
        and str(c.get("tier_basis") or "l3u_grade") != "l3u_grade"
    )
    observed = {
        "P1": {"n_clip": result["n_clip_claims"], "quotable": tier.get("QUOTABLE", 0)},
        "P2": {"unseal_based_clip": unseal_based},
        "P3": split,
        "P4": {
            "unsourced_firstrun": FIRSTRUN_UNSOURCED,
            "unsourced_final": counts.get("UNSOURCED_NUMBER", 0),
        },
        "P5": {"fact_drift": counts.get("FACT_DRIFT", 0)},
        "P6": {
            "sealed_leak": counts.get("SEALED_LEAK", 0),
            "window_n_mismatch": counts.get("WINDOW_N_MISMATCH", 0),
        },
    }
    # P4 按首跑判定，不因「事后把仪器修好了」翻案：首跑 20 处说明我对数字面的估计
    # 本来就是错的，修掉的是仪器，不是那个错误的估计。
    holds = {
        # P1 的判据随 L3z 审计后的口径修订（见预登记 A5）：可引用由 9 条减为 6 条。
        "P1": observed["P1"]["n_clip"] == 6 and observed["P1"]["quotable"] == 1,
        "P2": observed["P2"]["unseal_based_clip"] >= 4,
        "P3": split["unseal"] == 3 and split["l3u"] == 3,
        "P4": observed["P4"]["unsourced_firstrun"] <= 2,
        "P5": observed["P5"]["fact_drift"] == 0,
        "P6": observed["P6"]["sealed_leak"] == 0 and observed["P6"]["window_n_mismatch"] == 0,
    }
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    rows = []
    for pred in prereg["predictions"]:
        pid = pred["id"]
        rows.append(
            {
                "id": pid,
                "statement": pred["statement"],
                "quantity": pred["quantity"],
                "observed": observed[pid],
                "holds": holds[pid],
                "refuted": not holds[pid],
            }
        )
    return rows


def _render_report(result, preds, claims_payload, l3u) -> str:
    def esc(text: object) -> str:
        return str(text).replace("|", r"\|")

    lines = []
    lines.append("# L3y：对外表述定稿（生成与校验报告）")
    lines.append("")
    lines.append(
        f"状态 **{result['status']}**｜违规 {result['n_violations']} 处｜"
        f"事实项 {result['n_facts']} 条全部回源实算｜"
        f"允许数字 {result['n_allowed_tokens']} 个"
    )
    lines.append("")
    lines.append("## 一、这一阶段做了什么")
    lines.append("")
    lines.append(
        "L3v 保证了卡片里的数字等于 L3u 的重算值，但「卡片 → 对外文档」这一步仍然是手工的，"
        "而本项目历史上的数字事故全部发生在这一步。本阶段把这一步也交给机器："
        "定稿由脚本从卡片渲染，散文里仅有的几个数字必须能从源文件按指针取出来。"
    )
    lines.append("")
    lines.append(
        f"定稿收录可引用结论 {result['n_clip_claims']} 条，"
        f"封印 {result['n_sealed_claims']} 条（不给数字）。"
    )
    lines.append("")
    lines.append("## 二、事实项实算（G1）")
    lines.append("")
    lines.append("| 事实 | 定稿写 | 源文件实算 | 源 |")
    lines.append("| --- | --- | --- | --- |")
    for fact in result["facts"]:
        src = fact["source"]
        where = src.get("pointer") or " + ".join(src.get("pointers") or [])
        lines.append(
            f"| `{fact['key']}` | {fact['display']} | {fact['computed']} | "
            f"`{esc(src['path'])}` :: `{esc(where)}` |"
        )
    lines.append("")
    lines.append(
        "事实表里的 `display` 只是断言；每次运行都会回到源文件按指针重取一遍。"
        "源文件变了而断言不动，就报 `FACT_DRIFT` 并拒绝写出定稿。"
    )
    lines.append("")
    lines.append("## 三、分级表（G5 / G6 双源）")
    lines.append("")
    lines.append("| 分级 | 卡片重算 | 冻结产物 | 一致 |")
    lines.append("| --- | --- | --- | --- |")
    frozen = result["tier_counts_frozen"]
    recount = result["tier_recount"]
    for tier in dlv.TIER_ORDER:
        if tier not in frozen and tier not in recount:
            continue
        a = recount.get(tier, 0)
        b = frozen.get(tier, 0)
        lines.append(f"| `{tier}` | {a} | {b} | {'✓' if a == b else '✗'} |")
    lines.append("")
    lines.append(
        "两列来自不同文件：左边从卡片重算，右边是 L3v 冻结产物。"
        "只要有一行不等，就说明改了卡片却没重跑 L3v。"
    )
    lines.append("")
    lines.append("## 四、闸门与违规明细")
    lines.append("")
    if not result["violations"]:
        lines.append("全部七道闸门通过，违规 0 处。")
    else:
        lines.append("| 代码 | 次数 |")
        lines.append("| --- | --- |")
        for code, n in sorted(result["violation_counts"].items()):
            lines.append(f"| `{code}` | {n} |")
        lines.append("")
        lines.append("明细：")
        lines.append("")
        for v in result["violations"]:
            lines.append(f"- `{v['code']}`｜{esc(v['detail'])}")
    lines.append("")
    lines.append("## 五、封印条目的原因码出处")
    lines.append("")
    lines.append("| 结论 | 原因码 | 出处 |")
    lines.append("| --- | --- | --- |")
    decisions = dlv.decision_index(SOURCES, ROOT)
    for key, code in result["sealed_reason_codes"].items():
        origin = "解封复核产物" if key in decisions else "L3u comparison"
        lines.append(f"| `{key}` | `{code}` | {origin} |")
    lines.append("")
    lines.append("## 六、预测对照（先于测量写定）")
    lines.append("")
    lines.append("| 预测 | 内容 | 观测 | 结果 |")
    lines.append("| --- | --- | --- | --- |")
    for p in preds:
        lines.append(
            f"| {p['id']} | {esc(p['statement'])} | "
            f"{esc(json.dumps(p['observed'], ensure_ascii=False))} | "
            f"{'成立' if p['holds'] else '**被反驳**'} |"
        )
    lines.append("")
    refuted = [p["id"] for p in preds if p["refuted"]]
    lines.append(
        f"成立 {len(preds) - len(refuted)}/{len(preds)}。"
        f"被反驳：{('、'.join(refuted)) if refuted else '无'}。"
        "被反驳的预测原样保留，不改判据。"
    )
    lines.append("")
    lines.append("## 七、限制")
    lines.append("")
    lines.append(
        "- **数字查重不区分位置**：`allowed` 集合全局有效，只能判断「这个数字有没有出处」，"
        "不能判断「这个位置的数字是否用对了出处」。恰好等于别处合法值的手误可以放过。"
    )
    lines.append(
        "- **只校验结论句与结构计数**：引用块里的限定文字不受数字校验，删掉限定不会被机器发现。"
    )
    lines.append(
        "- **孤立数字无法消歧**：同一数值在多处出现时无法分辨指的是哪一个量。"
    )
    lines.append(
        "- **紧贴字母的数字不参与校验**：`NW3`、`MAX5`、`m1` 里的数字被视为口径标签。"
        "这是有意为之（否则 L3v 首跑会出 13 处假阳性），但代价是这类标签写错也抓不到。"
    )
    lines.append(
        "- **本阶段不产生新结论**：定稿里没有任何新的 alpha、t 或 MDE，"
        "全部数字都是既有结论的搬运。"
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    started = time.perf_counter()
    _check_frozen()
    quiet = "--quiet" in sys.argv

    claims_payload = json.loads(CARDS.read_text(encoding="utf-8"))
    l3u = json.loads(L3U_RESULT.read_text(encoding="utf-8"))
    l3v = json.loads(L3V_RESULT.read_text(encoding="utf-8"))
    facts_payload = dlv.load_facts(FACTS)

    result = dlv.assemble(
        claims_payload=claims_payload,
        l3u=l3u,
        l3v=l3v,
        facts_payload=facts_payload,
        root=ROOT,
        prereg_sha=PREREG_SHA,
        sources=SOURCES,
    )

    decisions = dlv.decision_index(SOURCES, ROOT)
    preds = _check_predictions(result, claims_payload, l3u, decisions)
    result["predictions"] = preds
    result["inputs_frozen"] = {
        key: {"path": path.relative_to(ROOT).as_posix(), "sha256": sha}
        for key, (path, sha) in FROZEN.items()
    }
    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = result.pop("markdown")
    (OUT_DIR / "l3y_deliverable.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    report = _render_report(result, preds, claims_payload, l3u)
    (OUT_DIR / "l3y_deliverable.md").write_text(report, encoding="utf-8", newline="\n")

    if result["status"] == "DELIVERABLE_CONSISTENT":
        DELIVERABLE.parent.mkdir(parents=True, exist_ok=True)
        DELIVERABLE.write_text(md + "\n", encoding="utf-8", newline="\n")
        target = DELIVERABLE.relative_to(ROOT).as_posix()
        (OUT_DIR / "l3y_rejected.md").unlink(missing_ok=True)
    else:
        rejected = OUT_DIR / "l3y_rejected.md"
        rejected.write_text(md + "\n", encoding="utf-8", newline="\n")
        target = f"{rejected.relative_to(ROOT).as_posix()}（定稿未落盘）"

    if not quiet:
        print(f"status           {result['status']}")
        print(f"事实项           {result['n_facts']} 条全部回源实算通过")
        print(f"允许数字         {result['n_allowed_tokens']} 个")
        print(f"可引用 / 封印    {result['n_clip_claims']} / {result['n_sealed_claims']}")
        print(f"违规             {result['n_violations']} 处 {result['violation_counts']}")
        for v in result["violations"][:20]:
            print(f"  - {v['code']}: {v['detail']}")
        refuted = [p["id"] for p in preds if p["refuted"]]
        print(f"预测             成立 {len(preds) - len(refuted)}/{len(preds)}，被反驳 {refuted or '无'}")
        print(f"定稿             {target}")
    return 0 if result["status"] == "DELIVERABLE_CONSISTENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
