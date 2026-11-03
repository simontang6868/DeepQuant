"""L3ad：检验独立性——m 按「独立随机变量数」计。

与 ``family_v2`` 的差别只有一处，但它是本阶段全部结论的来源：

* ``family_v2.merge_edges`` 判定「严格更短 + 逐位相同」⇒ 只认子窗口。等长同值不合并，
  于是同一份数字占两个名额。
* 本模块判定「**较短序列被完全覆盖** + 逐位相同」⇒ 等长也合并。理由：多重检验的 m
  计的是「独立检验数」，两条 p 值完全相同的检验只有一次犯错机会，算两次等于对同一条
  证据罚两次——那会把实际 FWER 压到名义 α 之下（保守偏差，不是正确性）。

合并只动「名额」，不动任何一条的 p、均值、标准误。研究假设仍然各算一个。
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from deepquant.gates import dependence as dp
from deepquant.gates import family as fam
from deepquant.gates import family_v2 as f2
from deepquant.gates import multiplicity as mp

EXPERIMENT_ID = "H-L3AD-INDEPENDENCE"
PREREG_PATH = "docs/gates/l3ad_prereg.json"
PREREG_SHA = "a5d86c50e85c8644257fb7f259986fa79cee7a2cf75751b592f38793f2ee3e26"

#: 逐位相同的容差。与 family_v2 / family 一致。
TOL = 1e-12

#: 较短序列必须被对方**完全覆盖**才算同一变量。只重叠一部分不算——
#: 部分月份恰好同值可能是巧合（尤其两段极端值都取到同一分位点时）。
REQUIRE_FULL_COVERAGE = True


class IndependenceError(RuntimeError):
    """预登记或冻结输入校验失败。"""


def load_prereg(root: Any) -> dict:
    path = Path(root) / PREREG_PATH
    if not path.exists():
        raise IndependenceError(f"预登记不存在：{PREREG_PATH}")
    raw = path.read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != PREREG_SHA:
        raise IndependenceError(
            f"预登记已被改动：期望 {PREREG_SHA[:12]}… 实际 {got[:12]}…"
        )
    return json.loads(raw.decode("utf-8"))


def check_frozen(root: Any, prereg: dict) -> dict:
    root = Path(root)
    out = {}
    for name, spec in prereg["inputs_frozen"].items():
        path = root / spec["path"]
        if not path.exists():
            raise IndependenceError(f"[{name}] 冻结输入缺失：{spec['path']}")
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != spec["sha256"]:
            raise IndependenceError(
                f"[{name}] 冻结输入被改动：期望 {spec['sha256'][:12]}… 实际 {got[:12]}…"
            )
        out[name] = {"path": spec["path"], "sha256": got}
    return out


# --------------------------------------------------------------------------
# 判据
# --------------------------------------------------------------------------


def identical_pairs(series: Mapping[str, dp.MonthSeries]) -> list[dict]:
    """枚举所有「较短序列被完全覆盖且逐位相同」的对。

    返回的每一对都带 ``max_abs_diff``、``n_common``、``coverage``，便于逐对复核。
    ``coverage`` = 共同月数 / 较短序列长度；按 ``REQUIRE_FULL_COVERAGE`` 取 1.0。
    """
    keys = sorted(series)
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            sa, sb = series[a], series[b]
            common = set(sa.values) & set(sb.values)
            if not common:
                continue
            short = min(sa.n, sb.n)
            coverage = len(common) / short
            if REQUIRE_FULL_COVERAGE and len(common) != short:
                continue
            mx = max(abs(sa.values[m] - sb.values[m]) for m in common)
            if mx > TOL:
                continue
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "n_a": sa.n,
                    "n_b": sb.n,
                    "n_common": len(common),
                    "coverage": coverage,
                    "max_abs_diff": mx,
                    "equal_length": sa.n == sb.n,
                }
            )
    return pairs


def identical_groups(
    keys: Sequence[str], series: Mapping[str, dp.MonthSeries]
) -> dict:
    """并查集：逐位相同的对连在一起，同一组 = 同一个随机变量。

    传递性是判据的直接后果：A 与 B 同值、B 与 C 同值 ⇒ A、B、C 是同一份数字。
    """
    parent = {k: k for k in keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    pairs = identical_pairs({k: series[k] for k in keys})
    for p in pairs:
        union(p["a"], p["b"])

    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    ordered = sorted((sorted(v) for v in groups.values()), key=lambda g: g[0])
    return {
        "groups": ordered,
        "n_groups": len(ordered),
        "n_merged_away": len(keys) - len(ordered),
        "pairs": pairs,
    }


def group_evidence(group: Sequence[str], prereg: dict) -> dict:
    """给每个多成员分组贴上「凭什么说它们同值」的证据。

    只为三对已知情形写了文案；出现未知分组时返回 ``UNKNOWN``，
    由闸门报出来——不猜、不糊过去。
    """
    want = {
        ("phaseo_netBASE", "phasep_late", "phasev_net"): "pair_B_phaseo_phasev",
        ("phasel_alpha", "phaser_alpha"): "pair_A_phasel_phaser",
        ("phases_net", "phaset_net"): "pair_C_phases_phaset",
    }
    key = tuple(sorted(group))
    tag = want.get(key)
    if tag is None:
        return {"kind": "UNKNOWN", "quote": "", "why_same": ""}
    spec = prereg["evidence"][tag]
    return {"kind": spec["kind"], "quote": spec["quote"], "why_same": spec["why_same"]}


# --------------------------------------------------------------------------
# m 口径
# --------------------------------------------------------------------------


def choose_representative(
    group: Sequence[str], series: Mapping[str, dp.MonthSeries], scored: Sequence[str]
) -> str:
    """组内代表 = 在评分集合里、窗口最长的那条；并列取字典序最小。

    为什么优先评分集合：被合并的条目里有些不参与评分（例如 ``phasev_net`` 从未在
    L3z 的 20 条评分集合里），拿它当代表会让一条「本来不评分」的条目进评分集合。
    窗口最长是为了保住样本量——同一份数据取全窗口比取片段更有效。
    """
    pool = [k for k in group if k in set(scored)] or list(group)
    # 先按窗口长度降序、再按 key 升序，取第一条——并列时结果可复现，不依赖字典顺序
    return sorted(pool, key=lambda k: (-series[k].n, k))[0]


def fwer_simulation(
    *, n_sims: int = 200_000, alpha: float = 0.05, seed: int = 20261029
) -> dict:
    """P8：同一随机变量被读两次，计 2 个名额 vs 计 1 个名额的实际拒绝率。

    两个检验的拒绝事件完全重合（p 相同），因此「任一拒绝」的概率就是「单个拒绝」的概率。
    m=2 的 Bonferroni 把门槛压到 α/2 ⇒ 实际水平 α/2；m=1 ⇒ 实际水平 α。
    这不依赖任何分布假设，只需 p 在零假设下均匀。
    """
    import random

    rng = random.Random(seed)
    hit_m2 = 0
    hit_m1 = 0
    thr2 = alpha / 2
    for _ in range(n_sims):
        p = rng.random()  # 零假设下 p ~ U(0,1)，两次读同一个 p
        if p <= thr2:
            hit_m2 += 1
        if p <= alpha:
            hit_m1 += 1
    return {
        "n_sims": n_sims,
        "alpha": alpha,
        "seed": seed,
        "reject_rate_m2": hit_m2 / n_sims,
        "reject_rate_m1": hit_m1 / n_sims,
        "expected_m2": alpha / 2,
        "expected_m1": alpha,
    }


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def build(root: Any) -> dict:
    root = Path(root)
    prereg = load_prereg(root)
    frozen = check_frozen(root, prereg)

    from deepquant.gates import citation as ct

    entries = ct.load_prereg(root / "docs/gates/l3u_prereg.json")["entries"]
    registered: dict[str, dp.MonthSeries] = {}
    skipped = []
    for e in entries:
        try:
            registered[e["key"]] = dp.load_entry(root, e)
        except dp.DependenceError as exc:
            skipped.append({"key": e["key"], "error": type(exc).__name__, "detail": str(exc)})

    added = f2.phasei_series(root)

    l3z = json.loads(
        (root / "reports/gates/H-L3Z-MULTIPLICITY/l3z_multiplicity.json").read_text(encoding="utf-8")
    )
    l3z_p = {i["key"]: i["p"] for i in l3z["runs"]["m_independent"]["items"]}

    # 分组只在可加载的序列上做（其余各占名额）
    part = identical_groups(sorted(registered), registered)
    all_series = {**registered, **added}
    part_all = identical_groups(sorted(all_series), all_series)

    # 代表与被吸收
    rep_of: dict[str, str] = {}
    group_detail = []
    for g in part_all["groups"]:
        rep = choose_representative(g, all_series, sorted(l3z_p))
        for k in g:
            if k != rep:
                rep_of[k] = rep
        ev = group_evidence(g, prereg)
        group_detail.append(
            {
                "members": g,
                "representative": rep,
                "absorbed": [k for k in g if k != rep],
                "n_members": len(g),
                "evidence": ev,
                "member_n": {k: all_series[k].n for k in g},
            }
        )

    # 评分集合：L3z 的 20 条里，只留每组代表；再加 phasei 两条
    pvals: dict[str, float] = {}
    stats: dict[str, dict] = {}
    for key, s in all_series.items():
        rec = fam.reproduce_from_series(list(s.values.values()), lag=3)
        stats[key] = {
            "n": rec["n"],
            "mean_pp": rec["mean"],
            "se_nw_pp": rec["se_nw"],
            "t_nw3": rec["t"],
            "p": rec["p"],
            "mde_pp": 2.8016 * rec["se_nw"],
            "in_registered": key in registered,
        }
        is_scored_slot = key in l3z_p or key in added
        if is_scored_slot and key not in rep_of:
            pvals[key] = rec["p"]

    # 合并不得改 p（G5）：被吸收条目的 p 应与代表相同
    p_unchanged = []
    for absorbed, rep in rep_of.items():
        if absorbed in stats and rep in stats:
            p_unchanged.append(
                {
                    "absorbed": absorbed,
                    "representative": rep,
                    "p_absorbed": stats[absorbed]["p"],
                    "p_rep": stats[rep]["p"],
                    "identical": abs(stats[absorbed]["p"] - stats[rep]["p"]) <= TOL,
                }
            )

    m_ind = len(entries) - part["n_merged_away"] + len(added)
    m_ind_all = len(entries) - part_all["n_merged_away"] + len(added)
    variants = [
        {
            "id": "IND_m25",
            "m": m_ind,
            "rule": "独立随机变量数（逐位相同即合并，含等长）",
            "note": "**本阶段主口径**",
        },
        {
            "id": "L3AC_m27",
            "m": 27,
            "rule": "L3ac 口径（只合并严格子窗口）",
            "note": "对照：等长同值占两个名额",
        },
        {
            "id": "FP_m26",
            "m": 26,
            "rule": "L3z 指纹去重 + phasei",
            "note": "对照：指纹抓不到子窗口",
        },
        {
            "id": "ALL_m29",
            "m": 29,
            "rule": "全部登记不合并 + phasei",
            "note": "对照：最保守",
        },
    ]

    keys_sorted = sorted(pvals, key=lambda k: (pvals[k], k))
    ordered_p = [pvals[k] for k in keys_sorted]
    runs = {}
    for v in variants:
        res = fam.apply_corrections_at_m(ordered_p, m=v["m"])
        flags = {
            "bonferroni": res["bonferroni"],
            "holm": res["holm"],
            "bh": res["bh"],
            "by": res["by"],
        }
        runs[v["id"]] = {
            "m": v["m"],
            "rule": v["rule"],
            "note": v["note"],
            "bonferroni_threshold": res["bonferroni_threshold"],
            "by_unit_threshold": res["by_unit_threshold"],
            "n_scored": len(pvals),
            "n_padded": res["n_padded"],
            "rejected": {
                name: [k for k, f in zip(keys_sorted, fs, strict=True) if f]
                for name, fs in flags.items()
            },
            "counts": {name: sum(fs) for name, fs in flags.items()},
        }

    return {
        "experiment_id": EXPERIMENT_ID,
        "prereg_sha256": PREREG_SHA,
        "inputs_frozen": frozen,
        "n_registered": len(entries),
        "n_added": len(added),
        "n_scored": len(pvals),
        "skipped": skipped,
        "partition": part,
        "partition_with_added": part_all,
        "groups": group_detail,
        "merged_into": rep_of,
        "p_unchanged": p_unchanged,
        "stats": stats,
        "pvals": pvals,
        "p_order": keys_sorted,
        "variants": variants,
        "runs": runs,
        "fwer": fwer_simulation(),
    }
