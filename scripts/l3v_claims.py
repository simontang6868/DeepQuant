"""L3v：结论卡片注册表 —— 把「对外只说一句话」钉在可复算的数字上。

跑法（仓库根）：
    PYTHONPATH="backend/src" python scripts/l3v_claims.py [--quiet]

只读 L3u 产物与卡片表，不重跑任何策略（R5）。L3u 产物 sha256 在启动时校验：
它一变，本实验的对照基准就换了，必须当新实验处理。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.gates import citation as cit  # noqa: E402
from deepquant.gates import claims as clm  # noqa: E402

PREREG = ROOT / "docs" / "gates" / "l3v_prereg.json"
PREREG_SHA = "16f10272652681143ffa3e97d6896958083c0264fee01b10284d2962167dfb39"

L3U_JSON = ROOT / "reports" / "gates" / "H-L3U-CITATION-REGISTRY" / "l3u_citation.json"
L3U_SHA = "f7d1a66d64c386e1a9c14e717c1606f9d931d2f789230d470a37cc83ce5c6e6b"
L3U_PREREG = ROOT / "docs" / "gates" / "l3u_prereg.json"

CLAIMS = ROOT / "docs" / "gates" / "l3v_claims.json"

#: 分级依据清单。三个来源都冻结 sha —— 依据一变，卡片就得重审。
#: L3w 处理「取错量」的 6 条，L3x 处理「仅 t 口径不同」的 6 条，两者都是解封（要求 UNSEALED）；
#: L3z 是审计后降级（要求 DOWNGRADED）。清单取并集，同一 key 分级矛盾会直接报错。
UNSEAL_SOURCES: tuple[tuple[Path, str], ...] = (
    (ROOT / "reports" / "gates" / "H-L3W-UNSEAL" / "l3w_unseal.json",
     "ce10baa7dabb1e1219177448e7e4ba6d1d81769cbcbde40c8dae85860445b6c0"),
    (ROOT / "reports" / "gates" / "H-L3X-TONLY" / "l3x_unseal.json",
     "e70703b2f69072194f943a8ab64abe4a7377534dc6a82948061d34623422e5c7"),
    (ROOT / "reports" / "gates" / "H-L3Z-MULTIPLICITY" / "l3z_claims_patch.json",
     "7aa350edfadb46a074695a4b77b065a59d37584eb38631b3270fc7c19a93888f"),
)

OUT_DIR = ROOT / "reports" / "gates" / "H-L3V-CLAIM-REGISTRY"
EVIDENCE = ROOT / "reports" / "L3V_CLAIMS_EVIDENCE.json"

#: 抽样复算的条目：头条 / 可引用 / 不同单位族各一。固定写死，不随机挑。
SPOT_KEYS = ("gate6_d", "phaseo_netBASE", "l3s_ic")
MAX_LITERALS_PER_CLAIM = 4


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _esc(text: object) -> str:
    return str(text).replace("|", r"\|")


def _unseal_index(sources: tuple[tuple[Path, str], ...]) -> dict[str, dict]:
    """读分级依据清单并冻结校验；返回 key → {status, tier, source, basis}。

    多个来源按给定顺序合并。**同一依据（basis）内部**若同一 key 给出不同分级，直接报错
    ——那说明该依据自身矛盾，必须先解决。**不同依据之间**则允许后者覆盖前者：
    L3z 审计降级是发生在 L3w 解封之后的一次修订，不是与它并列的一次复核，
    把它当成「矛盾」会阻塞正常的依据演进。覆盖事件记在返回值的 ``overrides`` 里。
    """
    index: dict[str, dict] = {}
    for path, expected in sources:
        if not path.exists():
            raise clm.ClaimsError(f"分级依据不存在：{path}")
        actual = _sha(path)
        if actual != expected:
            raise clm.ClaimsError(
                f"分级依据已被改动：{path} 期望 {expected}，实际 {actual}；卡片的分级依据变了，拒跑"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        basis = str(payload.get("basis") or payload.get("experiment_id"))
        for d in payload["decisions"]:
            entry = {
                "status": d["status"],
                "tier": d["tier"],
                "source": payload["experiment_id"],
                "basis": basis,
            }
            prior = index.get(d["key"])
            if prior and prior["tier"] != entry["tier"]:
                if prior.get("basis") == basis:
                    raise clm.ClaimsError(
                        f"{d['key']} 在同一依据 {basis} 内分级不同："
                        f"{prior['tier']} vs {entry['tier']}"
                    )
                entry["overrides"] = {
                    "from_tier": prior["tier"],
                    "from_basis": prior.get("basis"),
                    "from_source": prior.get("source"),
                }
            index[d["key"]] = entry
    return index


def _f(value: object, digits: int = 2) -> str:
    if not isinstance(value, (int, float)) or value != value:
        return "—"
    return f"{value:.{digits}f}"


def _spot_check(l3u_rows: list[dict], prereg: dict) -> dict:
    """从原始序列独立重算 3 条，确认「卡片 ← recomputed ← 原始 CSV」这条链没断。"""
    entries = {e["key"]: e for e in prereg["entries"]}
    by_key = {r["key"]: r for r in l3u_rows}
    out = []
    for key in SPOT_KEYS:
        entry = entries.get(key)
        row = by_key.get(key)
        if entry is None or row is None:
            out.append({"key": key, "ok": False, "detail": "条目缺失"})
            continue
        rebuilt = cit.rebuild_series(entry, ROOT)
        stats = cit.summarise_series(rebuilt.values)
        stored = row["recomputed"]
        diffs = {
            name: abs(float(stats[name]) - float(stored[name]))
            for name in ("mean", "t_nw3", "mde", "power")
            if stored.get(name) is not None
        }
        worst = max(diffs.values()) if diffs else 0.0
        out.append(
            {
                "key": key,
                "n": stats["n"],
                "n_matches": int(stats["n"]) == int(stored["n"]),
                "mean_recomputed": stats["mean"],
                "mean_stored": stored["mean"],
                "max_abs_diff": worst,
                "ok": bool(worst <= 1e-9 and int(stats["n"]) == int(stored["n"])),
            }
        )
    return {
        "keys": list(SPOT_KEYS),
        "checks": out,
        "all_ok": all(c["ok"] for c in out),
    }


def _check_predictions(
    payload: dict,
    registry: dict,
    retractions: int,
    literals_over: int,
    firstrun: dict | None = None,
) -> list[dict]:
    counts = registry["violation_counts"]
    drift = (
        counts.get("VALUE_DRIFT", 0)
        + counts.get("UNREGISTERED_NUMBER", 0)
        + counts.get("UNUSED_NUMBER", 0)
    )
    first_drift = None
    if firstrun:
        fcounts = firstrun.get("violation_counts") or {}
        first_drift = (
            fcounts.get("VALUE_DRIFT", 0)
            + fcounts.get("UNREGISTERED_NUMBER", 0)
            + fcounts.get("UNUSED_NUMBER", 0)
        )
    hard = payload["hardcoded"]
    observed = {
        "P1": {
            "missing": len(registry["coverage"]["missing"]),
            "orphan": len(registry["coverage"]["orphan"]),
        },
        "P2": (
            {"firstrun": first_drift, "final": drift} if first_drift is not None else {"final": drift}
        ),
        "P3": {
            "QUOTABLE": registry["tier_counts"].get("QUOTABLE", 0),
            "QUOTABLE_MARGINAL": registry["tier_counts"].get("QUOTABLE_MARGINAL", 0),
        },
        "P4": {"ESTIMATOR_MISMATCH": counts.get("ESTIMATOR_MISMATCH", 0)},
        "P5": {
            "groups": len(registry["duplicate_groups"]),
            "keys": registry["n_duplicate_keys"],
            "independent": registry["n_independent_numbers"],
        },
        "P6": {"retractions": retractions, "BANNED_PHRASING": counts.get("BANNED_PHRASING", 0)},
    }
    holds = {
        "P1": observed["P1"]["missing"] == 0 and observed["P1"]["orphan"] == 0,
        # 预测写的是「首次运行会检出几处」，所以用首跑值判定；首跑不存在才退回终版值
        "P2": 1 <= (drift if first_drift is None else first_drift) <= 8,
        "P3": observed["P3"]["QUOTABLE"] == hard["n_expected_quotable"]
        and observed["P3"]["QUOTABLE_MARGINAL"] == hard["n_expected_quotable_marginal"],
        "P4": observed["P4"]["ESTIMATOR_MISMATCH"] == 0,
        "P5": observed["P5"]["groups"] == hard["n_expected_duplicate_groups"]
        and observed["P5"]["keys"] == hard["n_expected_duplicate_keys"]
        and observed["P5"]["independent"] == hard["n_expected_independent_numbers"],
        "P6": retractions == hard["n_expected_retractions"]
        and observed["P6"]["BANNED_PHRASING"] == 0,
    }
    rows = []
    for pred in payload["predictions"]:
        pid = pred["id"]
        sup = pred.get("superseded_by")
        rows.append(
            {
                "id": pid,
                "statement": pred["statement"],
                "quantity": pred["quantity"],
                "observed": observed[pid],
                # 被后续阶段取代的预测不再作为判据：既不记成立也不记反驳，保留原样可查
                "holds": None if sup else holds[pid],
                "refuted": False if sup else (not holds[pid]),
                "superseded_by": sup,
                "superseded_note": pred.get("superseded_note"),
            }
        )
    if literals_over:
        rows.append(
            {
                "id": "S1",
                "statement": f"每条 literals 不超过 {MAX_LITERALS_PER_CLAIM} 个（白名单不得变成垃圾桶）",
                "quantity": "超过上限的条目数",
                "observed": {"over_limit": literals_over},
                "holds": literals_over == 0,
                "refuted": literals_over != 0,
            }
        )
    return rows


def _render_md(
    registry: dict,
    payload: dict,
    prereg: dict,
    l3u: dict,
    spot: dict,
    preds: list[dict],
    claims_sha: str,
    firstrun: dict | None = None,
) -> str:
    rows = {r["key"]: r for r in l3u["rows"]}
    by_tier: dict[str, list[str]] = {}
    for claim in payload["claims"]:
        by_tier.setdefault(claim["quotability"], []).append(claim["key"])

    def tier_line(tier: str) -> str:
        keys = by_tier.get(tier) or []
        return f"{tier}（{len(keys)}）：" + ("、".join(f"`{k}`" for k in keys) if keys else "无")

    refuted = [p["id"] for p in preds if p["refuted"]]

    lines: list[str] = []
    lines.append("# L3v 结论卡片注册表：27 条结论的对外表述唯一来源")
    lines.append("")
    lines.append(
        f"> 预登记 `docs/gates/l3v_prereg.json` sha256 `{PREREG_SHA[:12]}…`（脚本启动校验）"
        f"｜对照 L3u `{L3U_SHA[:12]}…`（启动校验）"
        f"｜卡片表 sha256 `{claims_sha[:12]}…`（只记录不冻结）"
        f"｜**只读审计，不重跑策略**"
    )
    lines.append("")
    lines.append("## 一、结论先行")
    lines.append("")
    n_q = registry["n_quotable"]
    lines.append(
        f"**27 条结论里，能放心单引点估计的有 {n_q} 条。**"
        f"按强度分：{tier_line('QUOTABLE')}；{tier_line('QUOTABLE_MARGINAL')}；"
        f"{tier_line('DIRECTION_ONLY')}；{tier_line('NULL_CONFIRMED')}；"
        f"{tier_line('UNDERPOWERED')}；{tier_line('NOT_GRADED')}。"
    )
    lines.append("")
    lines.append(
        f"另外两个结构性事实：**27 条只对应 {registry['n_independent_numbers']} 份独立数字**"
        f"（{len(registry['duplicate_groups'])} 组重复，共 {registry['n_duplicate_keys']} 个 key 共用同一份序列）；"
        f"**{registry['tier_counts'].get('NOT_GRADED', 0)} 条因为 L3u 核对未通过而不参与分级**——"
        "它们的参考分级只作内部提示，任何对外材料都不得使用。"
    )
    lines.append("")
    n_unseal = registry["n_quoted_via_unseal"]
    if n_unseal:
        lines.append(
            f"其中 **{n_unseal} 条的分级不是来自 L3u，而是来自 L3w / L3x 解封复核**"
            "（`tier_basis=\"l3w_unseal\"`）：它们当初被判「核对未通过」，复核证明要么是 L3t 抄错了列或窗口，"
            "要么只是 t 用了 iid 而登记表用 NW(3)，判定量与序列本身完全同源。"
            "解封要求卡片的分级**逐字等于**复核给出的一档，不能自定；复核还要求在 NW 滞后网格上不跨桶。"
        )
        lines.append("")
    lines.append(
        f"闸门本次检出 **{registry['n_violations']} 处违规**"
        + (f"（{registry['violation_counts']}）" if registry["violation_counts"] else "")
        + f"，最终状态 `{registry['status']}`。"
    )
    lines.append("")

    quotable = [
        c
        for c in payload["claims"]
        if c["quotability"] in {"QUOTABLE", "QUOTABLE_MARGINAL", "DIRECTION_ONLY", "NULL_CONFIRMED"}
    ]
    n_null = sum(1 for c in quotable if c["quotability"] == "NULL_CONFIRMED")
    lines.append(f"## 二、可对外的 {len(quotable)} 条（含 {n_null} 条零效应判定）")
    lines.append("")
    lines.append(
        "其余条目要么是 `UNDERPOWERED`（只能说「无法判断」）、要么是 `NOT_GRADED`（核对未通过，不参与分级）。"
        "下面每一条都可以直接抄进对外材料，但**必须连同限定一起抄**。"
    )
    lines.append("")
    # 按 key 取块，不靠下标对齐：某条缺覆盖时下标会整体错位
    block_keys = [c["key"] for c in payload["claims"] if c["key"] in rows]
    blocks_by_key = dict(zip(block_keys, registry["citation_blocks"], strict=False))
    idx = 0
    for claim in quotable:
        idx += 1
        lines.append(f"### {idx}. `{claim['key']}`｜{claim['quotability']}")
        lines.append("")
        lines.append(blocks_by_key[claim["key"]])
        lines.append("")

    lines.append("## 三、27 条逐条")
    lines.append("")
    lines.append("| 条目 | 一句话（须照抄，不得改写数字） | 强度 | 核对 |")
    lines.append("|---|---|---|---|")
    for claim in payload["claims"]:
        row = rows[claim["key"]]
        cmp = row["comparison"]
        label = cmp["class"] + ("（仅 t 不同）" if cmp.get("t_only") else "")
        lines.append(
            f"| `{claim['key']}` | {_esc(claim['headline'])} | {claim['quotability']} | "
            f"{_esc(label)} |"
        )
    lines.append("")

    lines.append("## 四、违规明细")
    lines.append("")
    if registry["violations"]:
        lines.append("| 条目 | 代码 | 说明 |")
        lines.append("|---|---|---|")
        for v in registry["violations"]:
            lines.append(f"| `{v['claim_key']}` | {v['code']} | {_esc(v['detail'])} |")
    else:
        lines.append("无。每个数字四舍五入后与重算字段字面相等、符号一致；分级未上调；限定齐全。")
    lines.append("")

    lines.append("## 五、数值指纹：6 个 key 共用 3 份数字")
    lines.append("")
    lines.append("同一份月度序列被复制到不同阶段目录，判据不同但数字逐位相同。**这不是两次独立验证。**")
    lines.append("")
    lines.append("| 组 | 条目 | 共用序列 |")
    lines.append("|---|---|---|")
    for g in registry["duplicate_groups"]:
        keys = "、".join(f"`{k}`" for k in g["keys"])
        series = rows[g["keys"][0]]["series"]
        lines.append(f"| {g['n']} 条 | {keys} | `{_esc(series)}` |")
    lines.append("")
    lines.append(
        f"去重后独立数字 = {len(l3u['rows'])} − {registry['n_duplicate_keys']} + "
        f"{len(registry['duplicate_groups'])} = **{registry['n_independent_numbers']}**。"
    )
    lines.append("")

    lines.append("## 六、抽样复算：链条没断")
    lines.append("")
    lines.append("从原始序列重算 3 条（头条 / 可引用 / 不同单位族各一），与 L3u 存的 `recomputed` 对比：")
    lines.append("")
    lines.append("| 条目 | n | 重算 mean | 存档 mean | 最大绝对差 | 结论 |")
    lines.append("|---|---|---|---|---|---|")
    for c in spot["checks"]:
        lines.append(
            f"| `{c['key']}` | {c.get('n')} | {_f(c.get('mean_recomputed'), 6)} | "
            f"{_f(c.get('mean_stored'), 6)} | {c.get('max_abs_diff', 0):.2e} | "
            f"{'一致' if c['ok'] else '不一致'} |"
        )
    lines.append("")
    lines.append("## 七、首跑对照：仪器错与数据错必须分开")
    lines.append("")
    causes = {
        "UNREGISTERED_NUMBER": (
            "首版数字 token 抽取没有排除紧跟字母的写法，`NW3`/`MAX5`/`m1` 里的数字被当成统计量",
            "检查器（已修，预登记留 amendment）",
        ),
        "SIGN_MISMATCH": (
            "display 只写数值、负号只出现在正文，校验拿不到对照",
            "数据侧：负值字面补负号；校验改成 display 与正文两处互校",
        ),
        "MISSING_CORE_FIELD": (
            "正文没有报 MDE，6 条结论缺分辨率",
            "数据侧：补 MDE",
        ),
        "ESTIMATOR_MISMATCH": (
            "判定用 iid，正文只引了 NW3 的 t",
            "数据侧：同列 iid 与 NW3",
        ),
        "UNUSED_NUMBER": (
            "登记了数字但正文没出现，文案改过而登记没删",
            "数据侧：删登记或补正文",
        ),
    }
    if firstrun:
        fc = firstrun.get("violation_counts") or {}
        lines.append(
            f"首跑检出 **{firstrun.get('n_violations', 0)} 处**："
            + "、".join(f"{k} {v}" for k, v in fc.items())
            + "。其中 UNREGISTERED_NUMBER 一项就把 P2 的阈值（1~8）顶穿了。"
        )
        lines.append("")
        lines.append("| 代码 | 处数 | 根因 | 修在哪一侧 |")
        lines.append("|---|---|---|---|")
        for code, count in sorted(fc.items(), key=lambda kv: -kv[1]):
            why, side = causes.get(code, ("见首跑产物", "—"))
            lines.append(f"| {code} | {count} | {_esc(why)} | {_esc(side)} |")
        lines.append("")
        lines.append(
            "**13 处是仪器错，20 处是被测对象的真错。**混在一起看会得出「卡片全是错的」这种错误印象；"
            "分开看才知道：检查器把口径标签当数字，而卡片本身漏了负号、漏了 MDE、漏了另一侧 t。"
            "仪器修完、数据补完再跑，违规归零。"
        )
        lines.append("")
        lines.append(
            "但 P2 的判定**不改**：预登记写的是「1~8 处」，首跑实测 13 处，形式上就是被反驳；"
            "被反驳的预测原样保留，不因为事后发现是仪器问题就翻案。"
        )
        lines.append("")
        lines.append(f"首跑产物保留在 `{firstrun.get('_path', '')}`（R5：不删失败）。")
    else:
        lines.append("未找到首跑产物。")
    lines.append("")

    lines.append("## 八、预测对照（先于测量写定）")
    lines.append("")
    lines.append("| # | 预测 | 实测 | 结论 |")
    lines.append("|---|---|---|---|")
    for p in preds:
        if p.get("superseded_by"):
            verdict = f"已被 `{p['superseded_by']}` 取代"
        else:
            verdict = "成立" if p["holds"] else "**被反驳**"
        lines.append(
            f"| {p['id']} | {_esc(p['statement'])} | {_esc(json.dumps(p['observed'], ensure_ascii=False))} | "
            f"{verdict} |"
        )
    lines.append("")
    lines.append(f"被反驳：{('、'.join(refuted)) if refuted else '无'}。被反驳的预测原样保留，不改判据（R5）。")
    if any(p["id"] == "P2" and p["refuted"] for p in preds):
        lines.append("")
        lines.append(
            "P2 的两个观测值都不落在 1~8：首跑 13 处（仪器把口径标签当数字）、终版 0 处（修完）。"
            "无论取哪个，这条预测都不成立——区别只在于「为什么不成立」，而这恰好是 §七 要分开的那件事。"
        )
    lines.append("")

    lines.append("## 九、限制")
    lines.append("")
    for item in prereg["disclosures"]:
        lines.append(f"- {item}")
    lines.append(
        f"- 每条 literals 上限 {MAX_LITERALS_PER_CLAIM} 个，超出即判违规（`LITERALS_OVERFLOW`）。"
    )
    lines.append("- 卡片只覆盖 L3u 收录的 27 条；未进入 L3t 登记表的结论不在本表内，也不得引用。")
    lines.append(
        "- **闸门只校验 headline 里的数字**；`limits_of_use` 与 `window_note` 里出现的数字是说明性背景"
        "（多来自其他阶段），不参与回指校验。若要把它们搬进正文，必须先登记。"
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3v 结论卡片注册表")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    cit.check_prereg_sha(PREREG, PREREG_SHA)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))

    actual_l3u_sha = _sha(L3U_JSON)
    if actual_l3u_sha != L3U_SHA:
        raise clm.ClaimsError(
            f"L3u 产物已被改动：期望 {L3U_SHA}，实际 {actual_l3u_sha}；对照基准变了，拒跑"
        )
    l3u = json.loads(L3U_JSON.read_text(encoding="utf-8"))
    l3u_prereg = cit.load_prereg(L3U_PREREG)

    claims_payload = clm.load_claims(CLAIMS)
    claims_sha = _sha(CLAIMS)

    literals_over = sum(
        1 for c in claims_payload["claims"] if len(c.get("literals") or []) > MAX_LITERALS_PER_CLAIM
    )

    unseal_index = _unseal_index(UNSEAL_SOURCES)
    registry = clm.assemble_registry(claims_payload, l3u, unseal_index)
    if literals_over:
        registry["violations"].append(
            {
                "claim_key": None,
                "code": "LITERALS_OVERFLOW",
                "detail": f"{literals_over} 条的 literals 超过 {MAX_LITERALS_PER_CLAIM} 个",
            }
        )
        registry["violation_counts"]["LITERALS_OVERFLOW"] = literals_over
        registry["n_violations"] += 1
        registry["status"] = "CLAIMS_INCONSISTENT"

    spot = _spot_check(l3u["rows"], l3u_prereg)
    if not spot["all_ok"]:
        registry["status"] = "CLAIMS_INCONSISTENT"

    firstrun_path = OUT_DIR / "l3v_claims_firstrun_tokenbug.json"
    firstrun = None
    if firstrun_path.exists():
        firstrun = json.loads(firstrun_path.read_text(encoding="utf-8"))
        firstrun["_path"] = firstrun_path.relative_to(ROOT).as_posix()

    preds = _check_predictions(
        payload,
        registry,
        len(claims_payload.get("retractions") or []),
        literals_over,
        firstrun=firstrun,
    )

    elapsed = time.perf_counter() - started
    result = {
        "experiment_id": payload["experiment_id"],
        "audit_only": True,
        "prereg_sha256": PREREG_SHA,
        "subject_l3u_sha256": L3U_SHA,
        "claims_sha256": claims_sha,
        "unseal_sources": [
            {"path": p.relative_to(ROOT).as_posix(), "sha256": s} for p, s in UNSEAL_SOURCES
        ],
        "n_unseal_decisions": len(unseal_index),
        "n_quoted_via_unseal": registry["n_quoted_via_unseal"],
        "elapsed_seconds": round(elapsed, 3),
        "n_claims": len(claims_payload["claims"]),
        "n_l3u_rows": len(l3u["rows"]),
        "coverage": registry["coverage"],
        "tier_counts": registry["tier_counts"],
        "tier_keys": registry["tier_keys"],
        "n_quotable": registry["n_quotable"],
        "n_not_graded": registry["tier_counts"].get("NOT_GRADED", 0),
        "violations": registry["violations"],
        "violation_counts": registry["violation_counts"],
        "n_violations": registry["n_violations"],
        "duplicate_groups": registry["duplicate_groups"],
        "n_duplicate_keys": registry["n_duplicate_keys"],
        "n_independent_numbers": registry["n_independent_numbers"],
        "spot_check": spot,
        "firstrun_compared": (
            {
                "path": firstrun["_path"],
                "n_violations": firstrun.get("n_violations"),
                "violation_counts": firstrun.get("violation_counts"),
            }
            if firstrun
            else None
        ),
        "retractions": claims_payload.get("retractions") or [],
        "predictions": preds,
        "status": registry["status"],
        "citation_blocks": registry["citation_blocks"],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "l3v_claims.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    md = _render_md(registry, claims_payload, payload, l3u, spot, preds, claims_sha, firstrun)
    (OUT_DIR / "l3v_claims.md").write_text(md, encoding="utf-8", newline="\n")

    evidence = {
        "experiment_id": payload["experiment_id"],
        "prereg_sha256": PREREG_SHA,
        "prereg_bytes": len(PREREG.read_bytes()),
        "subject_l3u_sha256": L3U_SHA,
        "claims_sha256": claims_sha,
        "unseal_sha256": [s for _, s in UNSEAL_SOURCES],
        "n_quoted_via_unseal": registry["n_quoted_via_unseal"],
        "outputs": {
            "json": (OUT_DIR / "l3v_claims.json").relative_to(ROOT).as_posix(),
            "markdown": (OUT_DIR / "l3v_claims.md").relative_to(ROOT).as_posix(),
        },
        "status": registry["status"],
        "n_violations": registry["n_violations"],
        "violation_counts": registry["violation_counts"],
        "tier_counts": registry["tier_counts"],
        "n_independent_numbers": registry["n_independent_numbers"],
        "spot_check_all_ok": spot["all_ok"],
        "refuted_predictions": [p["id"] for p in preds if p["refuted"]],
    }
    EVIDENCE.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )

    if not args.quiet:
        print(f"卡片 {len(claims_payload['claims'])} 条｜覆盖 missing={len(registry['coverage']['missing'])} "
              f"orphan={len(registry['coverage']['orphan'])}")
        print("分级：" + "｜".join(f"{k}={v}" for k, v in registry["tier_counts"].items()))
        print(f"违规 {registry['n_violations']} 处：{registry['violation_counts'] or '无'}")
        print(f"独立数字 {registry['n_independent_numbers']}/{len(l3u['rows'])}"
              f"（重复 {len(registry['duplicate_groups'])} 组、{registry['n_duplicate_keys']} 个 key）")
        print(f"抽样复算 {'全部一致' if spot['all_ok'] else '有不一致'}")
        refuted = [p["id"] for p in preds if p["refuted"]]
        print(f"被反驳的预测：{', '.join(refuted) or '无'}")
        print(f"状态 {registry['status']}")
        print(f"报告：{(OUT_DIR / 'l3v_claims.md').relative_to(ROOT).as_posix()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
