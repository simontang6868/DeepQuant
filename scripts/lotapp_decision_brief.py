"""R6-5：整手追加规则「11-30 后一页决策材料」生成器。

⭐ 时间闸门：``--publish`` 要求 ``--today >= 20261130``；11-30 前只允许
``--selftest``。预登记 sha256 启动时自校验（不符即拒绝运行）。
**不产出对外数字**是默认状态；没有同一批 navs 时一律 ``DATA_UNAVAILABLE``
失败关闭，绝不用合成/近似序列凑。

用法：
    python scripts/lotapp_decision_brief.py --selftest
    python scripts/lotapp_decision_brief.py --publish --today 20261130 \
        --navs research/.../d4_strict_out/lotapp_navs.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
SP5 = REPO / "research" / "stock_pick_2026-10-25"
PREREG = SP5 / "SP5_LOTAPP_DECISION_PREREG.md"
PREREG_SHA = SP5 / "sp5_lotapp_decision_prereg.sha256"
OUT_DEFAULT = SP5 / "d4_strict_out" / "SP5_LOTAPP_DECISION_BRIEF.md"
BASELINE = SP5 / "d4_strict_out" / "sp5_tradability_d4.json"

PUBLISH_NOT_BEFORE = "20261130"
SEED, B, REPS = 20261026, 21, 10
W = 0.20
WASTE = {
    "current": {"expected": 0.120, "sample": 0.089},
    "append": {"expected": 0.017, "sample": 0.030},
}
ANN = {"current": {"expected": 0.0750, "sample": 0.0766},
       "append": {"expected": 0.0802, "sample": 0.0796}}
MAX_WEIGHT = {"current": 1.000, "append": 1.176}
BASELINE_P_MIX20 = 0.047   # sp5_tradability_d4.json / mdd_bootstrap.mix20

EXIT_OK, EXIT_FAIL, EXIT_GATE = 0, 2, 3


class BriefError(RuntimeError):
    pass


def check_prereg() -> str:
    if not PREREG.is_file() or not PREREG_SHA.is_file():
        raise BriefError("预登记或其 sha256 文件缺失")
    want = PREREG_SHA.read_text(encoding="utf-8").split()[0]
    got = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    if got != want:
        raise BriefError(f"预登记 sha256 不符：{got[:12]} vs {want[:12]}")
    return got[:12]


def load_navs(path: Path) -> list[np.ndarray]:
    if not path.is_file():
        raise BriefError(f"DATA_UNAVAILABLE：缺 {path}（11-30 后须先导出同一批 navs 并冻结 sha）")
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BriefError(f"navs 不可解析：{type(exc).__name__}") from exc
    raw = d.get("navs")
    if not isinstance(raw, list) or not raw:
        raise BriefError("DATA_UNAVAILABLE：navs 为空")
    out = []
    for row in raw:
        a = np.asarray(row, dtype=float)
        if a.ndim != 1 or len(a) <= B + 1 or not np.all(np.isfinite(a)):
            raise BriefError("navs 结构不符（须为一维、长度 > B、全有限）")
        out.append(a)
    return out


def dmetrics(nav: np.ndarray) -> float:
    peak = np.maximum.accumulate(nav)
    return float((nav / peak - 1).min())


def mix_nav(nav: np.ndarray, w: float) -> np.ndarray:
    r = nav[1:] / nav[:-1] - 1
    pr = w * r + (1 - w) * (0.03 / 244.0)
    return np.concatenate([[1.0], np.cumprod(1 + pr)])


def replay_baseline(navs: list[np.ndarray]) -> float:
    """严格按 p03e 的 rng 流重放，验证 navs 是不是同一批（否则失败关闭）。"""
    rng = np.random.default_rng(SEED + 999_999)
    p20 = None
    for w in (0.25, 0.20):
        mdds = []
        for nv in navs:
            r = nv[1:] / nv[:-1] - 1.0
            for _ in range(REPS):
                idx: list[int] = []
                while len(idx) < len(r):
                    st = int(rng.integers(0, len(r) - B))
                    idx.extend(range(st, st + B))
                r_b = r[idx[:len(r)]]
                mdds.append(dmetrics(mix_nav(
                    np.concatenate([[1.0], np.cumprod(1 + r_b)]), w)))
        if w == 0.20:
            p20 = float((np.asarray(mdds) < -0.20).mean())
    assert p20 is not None
    return p20


def paired_bootstrap(navs: list[np.ndarray], waste: float) -> dict[str, Any]:
    """按预登记：两规则共用同一批重采样索引（先生成索引，再分别混权）。"""
    rng = np.random.default_rng(SEED + 999_999 + 1)  # 与基线重放分开，避免串流
    indices: list[tuple[np.ndarray, np.ndarray]] = []
    for nv in navs:
        r = nv[1:] / nv[:-1] - 1.0
        for _ in range(REPS):
            idx: list[int] = []
            while len(idx) < len(r):
                st = int(rng.integers(0, len(r) - B))
                idx.extend(range(st, st + B))
            indices.append((r, np.asarray(idx[:len(r)])))
    w_eff = W * (1 - waste)
    mdds = np.asarray([
        dmetrics(mix_nav(np.concatenate([[1.0], np.cumprod(1 + r[ix])]), w_eff))
        for r, ix in indices])
    return {"w_eff": w_eff, "p_mdd_gt_20pct": float((mdds < -0.20).mean()),
            "q5": float(np.quantile(mdds, .05)), "q50": float(np.quantile(mdds, .50)),
            "q95": float(np.quantile(mdds, .95)), "n_rep": int(len(mdds)),
            "block": B, "assumption": "EXPLORATORY_CONSTANT_WASTE"}


def render(rec: dict[str, Any], navs_sha: str, out: Path) -> int:
    p = rec["prereg_sha"]
    lines = [
        "# 整手追加规则决策材料（11-30 后产出，**未上线**）",
        "",
        f"**预登记**：SP5-LOTAPP-DECISION-1 sha `{p}`｜上游 `a693026f`｜"
        f"navs sha `{navs_sha}`｜基线重放 P(mix20)={rec['baseline_p']:.3f}（期望 0.047）",
        "",
        "| 指标 | 现行规则 | 追加规则 |",
        "| --- | ---: | ---: |",
    ]
    for label, key, fmt in [("整手浪费（期望）", "waste", ".1%"),
                            ("隐含年化（期望）", "ann", ".2%"),
                            ("单只最大权重", "max_weight", ".1%"),
                            ("P(MDD>20%)", "p", ".1%")]:
        a, b = rec["rows"][key]
        unit = "" if key == "max_weight" else ""
        lines.append(f"| {label} | {a:{fmt}}{unit} | {b:{fmt}}{unit} |")
    lines += [
        "",
        f"- q5（现行/追加）：{rec['q5'][0]:.1%} / {rec['q5'][1]:.1%}；"
        f"w_eff {rec['w_eff'][0]:.4f} / {rec['w_eff'][1]:.4f}；n_rep={rec['n_rep']}、B=21。",
        "- ⚠️ **8.02% 仅超 8% 门槛 0.02pp**；表中全部数字为**样本内估算**，S4=0、无样本外证据。",
        "- ⚠️ **P(MDD>20%) 为 `EXPLORATORY_CONSTANT_WASTE` 敏感性**：waste 取逐月常数"
        "（本仓库只有单月 4 只价格），真实 waste 随每月持仓价格变化；不得当精确值。",
        "- ⚠️ 配对索引与既有 4.7% 基线的 rng 流不同，**只作方向对照，不比数值**；"
        "基线重放仅用于验证 navs 是否同一批。",
        "- ⚠️ 上游 P3「部分被反驳」：本月单只超目标 17.6%、5 万档 1.3× 上限会绑定。",
        "- **本材料不含上线建议**；规则仍**未上线**，由用户决定。",
        "",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return EXIT_OK


def publish(today: str, navs_path: Path, out: Path) -> int:
    if today < PUBLISH_NOT_BEFORE:
        print(f"[brief] ✗ 时间闸门：{today} < {PUBLISH_NOT_BEFORE}，11-30 前不得产出决策材料",
              file=sys.stderr)
        return EXIT_GATE
    try:
        navs = load_navs(navs_path)
    except BriefError as exc:
        print(f"[brief] ✗ {exc}", file=sys.stderr)
        return EXIT_FAIL
    sha = hashlib.sha256(navs_path.read_bytes()).hexdigest()[:12]
    baseline_p = replay_baseline(navs)
    if abs(baseline_p - BASELINE_P_MIX20) > 0.005:
        print(f"[brief] ✗ navs 与既有基线不一致：重放 P={baseline_p:.3f} vs 0.047 ⇒ 失败关闭",
              file=sys.stderr)
        return EXIT_FAIL

    cur = paired_bootstrap(navs, WASTE["current"]["expected"])
    app = paired_bootstrap(navs, WASTE["append"]["expected"])
    rec = {
        "prereg_sha": check_prereg(), "baseline_p": baseline_p,
        "rows": {
            "waste": (WASTE["current"]["expected"], WASTE["append"]["expected"]),
            "ann": (ANN["current"]["expected"], ANN["append"]["expected"]),
            "max_weight": (MAX_WEIGHT["current"], MAX_WEIGHT["append"]),
            "p": (cur["p_mdd_gt_20pct"], app["p_mdd_gt_20pct"]),
        },
        "q5": (cur["q5"], app["q5"]),
        "w_eff": (cur["w_eff"], app["w_eff"]), "n_rep": cur["n_rep"],
    }
    rc = render(rec, sha, out)
    print(f"[brief] ✓ 已写 {out}｜P(现行)={cur['p_mdd_gt_20pct']:.3f} "
          f"P(追加)={app['p_mdd_gt_20pct']:.3f}｜规则仍未上线")
    return rc


def selftest() -> int:
    check_prereg()
    # 合成序列只用于验证算法性质，绝不写进材料。
    rng = np.random.default_rng(7)
    navs = [np.concatenate([[1.0], np.cumprod(1 + rng.normal(0.004, 0.05, 120))])
            for _ in range(12)]
    low = paired_bootstrap(navs, 0.120)
    high = paired_bootstrap(navs, 0.017)
    assert high["w_eff"] > low["w_eff"], "waste 下降必须抬高有效权重"
    assert high["q50"] <= low["q50"] + 1e-9, "权重上升不应让中位回撤变浅"
    assert low["n_rep"] == len(navs) * REPS and low["block"] == B
    assert publish("20261129", Path("/nonexistent"), OUT_DEFAULT) == EXIT_GATE
    try:
        load_navs(Path("/nonexistent"))
    except BriefError as e:
        assert "DATA_UNAVAILABLE" in str(e)
    else:
        raise AssertionError("缺 navs 必须失败关闭")
    print("[brief] selftest OK｜预登记 sha 校验通过｜时间闸门有效｜"
          "常数 waste 单调性成立｜缺 navs 失败关闭（未产出任何对外数字）")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R6-5 整手追加规则决策材料（11-30 后才可 publish）")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--today", default=dt.date.today().strftime("%Y%m%d"))
    ap.add_argument("--navs", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    try:
        if args.publish:
            return publish(args.today,
                           Path(args.navs) if args.navs else SP5 / "d4_strict_out" / "lotapp_navs.json",
                           Path(args.out) if args.out else OUT_DEFAULT)
        return selftest()
    except BriefError as exc:
        print(f"[brief] ✗ {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
