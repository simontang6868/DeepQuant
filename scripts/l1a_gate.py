"""L1a 向量化粗筛闸门：命令行入口。

用法：

    python scripts/l1a_gate.py --spec docs/gates/specs/H_L1A_MACROSS_000001.json
    python scripts/l1a_gate.py --spec ... --out reports/gates/H-L1A-MACROSS-000001

行为：
- 读取假设契约（跑前冻结的 JSON），执行 ``契约 → 数据 → 未来函数 → 覆盖 → 边界``；
- 写出 ``report.md``（给人看）与 ``result.json``（给机器比对）；
- 退出码：``L1A_PASS`` → 0，其余 → 1（可直接做 CI 门禁）。

为什么不提供"改边界重跑"的便捷开关
----------------------------------
边界写进契约文件、契约有摘要。想改边界就得改文件、摘要随之变化、报告与旧结论
对不上——这是**有意**的摩擦。闸门最怕的不是严格，是"再试一次就过了"。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.gates import PASS, load_spec, render_markdown, run_l1a  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L1a 向量化粗筛闸门")
    parser.add_argument("--spec", required=True, help="假设契约 JSON 路径")
    parser.add_argument("--out", default=None, help="输出目录（默认 reports/gates/<hypothesis_id>）")
    parser.add_argument("--probes", type=int, default=8, help="未来函数动态检测的截断点数")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    spec = load_spec(args.spec)
    out_dir = Path(args.out) if args.out else ROOT / "reports" / "gates" / spec.hypothesis_id
    out_dir.mkdir(parents=True, exist_ok=True)

    result = run_l1a(spec, probes=args.probes)
    payload = result.to_dict()
    payload["spec_file"] = str(Path(args.spec))

    (out_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "report.md").write_text(
        render_markdown(payload) + "\n", encoding="utf-8", newline="\n"
    )
    (out_dir / "spec.json").write_text(
        json.dumps(payload["spec"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if not args.quiet:
        print(f"结论：{result.verdict}")
        for stage in result.stages:
            print(f"  {stage.name:10s} {stage.status:12s} {stage.detail[:120]}")
        print(f"报告：{out_dir / 'report.md'}")
    return 0 if result.verdict == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
