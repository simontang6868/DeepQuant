"""L3p 组合级 Walk-Forward 闸门 CLI。

用法：
    python scripts/l3p_gate.py [--spec ...] [--out-dir ...]

退出码：0 = L3P_PASSED；2 = BLOCKED_PREREQUISITE；其他非零 = 被否决或不可判定。

与 L3 的差别只有「一个 fold 的收益由组合账户产出」。切窗、阈值、判定链全部同源：
阈值若两边不一致，组合与单标的的折统计就不可比，两份报告的结论也不能并列引用。
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

DEFAULT_SPEC = ROOT / "docs" / "gates" / "specs" / "H_L3P_MACROSS_000001.json"
DEFAULT_OUT = ROOT / "reports" / "gates" / "H-L3P-MACROSS-000001"
UNIVERSE_DIR = ROOT / "docs" / "gates" / "universes"

#: 前序闸门结果文件 → 从中取终态的路径。任何一个不是 PASSED，本闸门终态降为 BLOCKED_PREREQUISITE。
PRIOR_SOURCES: dict[str, tuple[Path, tuple[str, ...]]] = {
    "L1a": (ROOT / "reports" / "gates" / "H-L1A-MACROSS-000001" / "result.json", ("verdict",)),
    "L1b": (ROOT / "reports" / "gates" / "H-L1B-MACROSS-000001" / "l1b_result.json", ("result", "verdict")),
    "L2": (ROOT / "reports" / "gates" / "H-L2-MACROSS-000001" / "l2_result.json", ("result", "verdict")),
    "L3": (ROOT / "reports" / "gates" / "H-L3-MACROSS-000001" / "l3_result.json", ("result", "verdict")),
}


def _dig(payload: Any, path: tuple[str, ...]) -> Any:
    node = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def collect_prior_verdicts() -> tuple[dict[str, str], list[str]]:
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
    parser = argparse.ArgumentParser(description="L3p 组合级 Walk-Forward 闸门")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-control", action="store_true", help="跳过对照臂（只算策略，省时间）")
    args = parser.parse_args(argv)

    from deepquant.gates import load_spec
    from deepquant.gates.l3 import parse_walk_forward
    from deepquant.gates.l3_portfolio import (
        load_universe,
        parse_portfolio_spec,
        render_l3p_markdown,
        run_l3_portfolio,
    )

    spec_path = Path(args.spec)
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    if "universe" not in payload:
        # 缺这个键会让标的池静默回退到某个默认值——那等于换了实验对象却没人发现
        print("✗ 契约缺少 universe 字段（标的池文件路径）")
        return 1
    universe_path = (ROOT / str(payload["universe"])) if not Path(payload["universe"]).is_absolute() else Path(payload["universe"])

    spec = load_spec(spec_path)
    wf = parse_walk_forward(spec.walk_forward)
    pf = parse_portfolio_spec(payload.get("portfolio"))

    universe = load_universe(
        universe_path,
        data_dir=ROOT / "data" / "qfq_all",
        span_start=wf.span_start or spec.in_sample.start,
        span_end=wf.span_end or spec.out_sample.end,
    )
    prior, missing = collect_prior_verdicts()

    started = time.perf_counter()
    result = run_l3_portfolio(
        spec,
        universe=universe,
        walk_forward=wf,
        portfolio=pf,
        prior_verdicts=prior,
        with_control=not args.no_control,
    )
    elapsed = time.perf_counter() - started

    result.notes.append(
        "前序终态（读自各自结果文件）："
        + "、".join(f"{name}={value}" for name, value in sorted(prior.items()))
    )
    for item in missing:
        result.notes.append(f"前序信息缺失：{item}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_l3p_markdown(result, title=spec.title)
    (out_dir / "l3p_report.md").write_text(markdown + "\n", encoding="utf-8", newline="\n")
    record = {
        "hypothesis_id": result.hypothesis_id,
        "spec_path": str(spec_path.relative_to(ROOT)) if spec_path.is_absolute() else str(spec_path),
        "universe_path": str(universe_path.relative_to(ROOT)) if universe_path.is_absolute() else str(universe_path),
        "walk_forward_digest": wf.digest,
        "portfolio": pf.to_dict(),
        "prior_verdicts": prior,
        "elapsed_seconds": round(elapsed, 3),
        "result": result.to_dict(),
    }
    (out_dir / "l3p_result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    if not args.quiet:
        s = result.summary
        c = result.control or {}
        print(f"终态：{result.verdict}")
        if result.preliminary_verdict:
            print(f"  （不计前置时判定链结论：{result.preliminary_verdict}）")
        print(f"标的池：{universe.universe_id}（{len(universe.constituents)} 只，剔除 {len(universe.dropped)} 只）")
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
        if c.get("beats_control_ratio") is not None:
            print(
                "对照臂：策略均值 {a} vs 对照 {b}，超额 {e}，跑赢 {k}/{n} 折".format(
                    a=f"{c['strategy_mean']:+.4f}",
                    b=f"{c['control_mean']:+.4f}",
                    e=f"{c['excess_mean']:+.4f}",
                    k=c["beats_control_folds"],
                    n=c["folds_compared"],
                )
            )
        print(f"报告：{out_dir.relative_to(ROOT)}/l3p_report.md")

    if result.verdict == "L3P_PASSED":
        return 0
    if result.verdict == "L3P_BLOCKED_PREREQUISITE":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
