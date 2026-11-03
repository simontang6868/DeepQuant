"""L3 Walk-Forward 闸门 CLI。

用法：
    python scripts/l3_gate.py [--spec ...] [--out-dir ...]

退出码：0 = L3_PASSED；2 = BLOCKED_PREREQUISITE；其他非零 = 被否决或不可判定。

设计要点
--------
- 前序闸门的终态从各自的结果文件里**读**，不在这里手写：手写会让"前置已通过"变成
  一个容易过期的常量。
- 报告与结果都写到 ``reports/gates/<hypothesis>/``，与 L1a/L1b/L2 保持一致。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

DEFAULT_SPEC = ROOT / "docs" / "gates" / "specs" / "H_L3_MACROSS_000001.json"
DEFAULT_OUT = ROOT / "reports" / "gates" / "H-L3-MACROSS-000001"

#: 前序闸门结果文件 → 从中取终态的路径。缺文件时如实记为 UNKNOWN，不假设通过。
PRIOR_SOURCES: dict[str, tuple[Path, tuple[str, ...]]] = {
    "L1a": (ROOT / "reports" / "gates" / "H-L1A-MACROSS-000001" / "result.json", ("verdict",)),
    "L1b": (ROOT / "reports" / "gates" / "H-L1B-MACROSS-000001" / "l1b_result.json", ("result", "verdict")),
    "L2": (ROOT / "reports" / "gates" / "H-L2-MACROSS-000001" / "l2_result.json", ("result", "verdict")),
}


def _dig(payload: Any, path: tuple[str, ...]) -> Any:
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def collect_prior_verdicts() -> tuple[dict[str, str], list[str]]:
    """读取前序终态。返回 (终态表, 缺失说明)。"""
    verdicts: dict[str, str] = {}
    missing: list[str] = []
    for name, (path, keys) in PRIOR_SOURCES.items():
        if not path.exists():
            verdicts[name] = "UNKNOWN"
            missing.append(f"{name}：找不到 {path.relative_to(ROOT)}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            verdicts[name] = "UNKNOWN"
            missing.append(f"{name}：结果文件无法解析（{exc}）")
            continue
        value = _dig(payload, keys)
        verdicts[name] = str(value) if value else "UNKNOWN"
        if not value:
            missing.append(f"{name}：结果文件里没有 {'/'.join(keys)}")
    return verdicts, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L3 Walk-Forward 闸门")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    from deepquant.gates import load_spec
    from deepquant.gates.l3 import (
        VERDICT_PASSED,
        VERDICT_PREREQUISITE,
        parse_walk_forward,
        render_l3_markdown,
        run_l3,
    )

    spec = load_spec(args.spec)
    wf = parse_walk_forward(spec.walk_forward)
    prior, missing = collect_prior_verdicts()

    started = time.perf_counter()
    result = run_l3(spec, walk_forward=wf, prior_verdicts=prior)
    elapsed = time.perf_counter() - started

    result.notes.append(
        "前序终态（读自各自结果文件）："
        + "、".join(f"{name}={value}" for name, value in sorted(prior.items()))
    )
    for item in missing:
        result.notes.append(f"前序信息缺失：{item}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_l3_markdown(result, title=spec.title)
    (out_dir / "l3_report.md").write_text(markdown + "\n", encoding="utf-8", newline="\n")
    payload = {
        "hypothesis_id": result.hypothesis_id,
        "spec_path": str(Path(args.spec).relative_to(ROOT)) if Path(args.spec).is_absolute() else args.spec,
        "walk_forward_digest": wf.digest,
        "prior_verdicts": prior,
        "elapsed_seconds": round(elapsed, 3),
        "result": result.to_dict(),
    }
    (out_dir / "l3_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        s = result.summary
        print(f"终态：{result.verdict}")
        print(f"折：{s.get('folds_usable')}/{s.get('folds_total')} 可用；耗时 {elapsed:.1f}s")
        print(
            "测试收益：均值 {mean}、中位 {median}、正收益折 {pos}/{n}（{ratio}）".format(
                mean="—" if s.get("test_return_mean") is None else f"{s['test_return_mean']:+.4f}",
                median="—" if s.get("test_return_median") is None else f"{s['test_return_median']:+.4f}",
                pos=s.get("test_positive_folds"),
                n=s.get("folds_usable"),
                ratio="—" if s.get("test_positive_fold_ratio") is None else f"{s['test_positive_fold_ratio']:.1%}",
            )
        )
        print(
            "训练正收益折 {tr}；「训练正、测试负」{dv} 折；中位 WFE {wfe}".format(
                tr="—" if s.get("train_positive_fold_ratio") is None else f"{s['train_positive_fold_ratio']:.1%}",
                dv=s.get("divergent_folds"),
                wfe="—" if s.get("median_wfe") is None else f"{s['median_wfe']:+.4f}",
            )
        )
        print(f"报告：{out_dir.relative_to(ROOT)}/l3_report.md")

    if result.verdict == VERDICT_PASSED:
        return 0
    if result.verdict == VERDICT_PREREQUISITE:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
