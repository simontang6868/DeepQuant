"""从一次 Gate 1 运行的落盘结果机械生成 Markdown 报告。

只做两件事：读（含完整性校验）、渲染。**不重新计算任何绩效指标**——
数字若对不上，应当报错，而不是在渲染层悄悄算出另一个数。

用法::

    python scripts/gate1_report.py <结果目录> [--authorized-by "<原话>"]

结果目录形如 ``gate1_out/<run_id>``，必须含 ``summary.json`` 与 ``manifest.json``。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from deepquant.performance.gate1_report import load_run, render_report, write_report  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gate 1 结果报告生成器（只读落盘结果，不重算指标）")
    p.add_argument("run_dir", help="结果目录，如 gate1_out/<run_id>")
    p.add_argument("--authorized-by", default=None, help="授权原话（写入报告元信息）")
    p.add_argument("--note", action="append", default=[], help="追加到「已知局限」的说明，可重复")
    p.add_argument("--stdout", action="store_true", help="只打印，不落盘")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"[ERROR] 目录不存在：{run_dir}", file=sys.stderr)
        return 2

    now = datetime.now().strftime("%Y-%m-%d %H:%M GMT+8")

    try:
        if args.stdout:
            run = load_run(run_dir)
            print(render_report(run, generated_at=now,
                                authorized_by=args.authorized_by, extra_notes=args.note))
            return 0

        target = write_report(run_dir, generated_at=now,
                              authorized_by=args.authorized_by, extra_notes=args.note)
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        # 这些不是 bug，是「这份结果不能出报告」的判定结果，用一行说清楚即可
        print(f"[REFUSED] {exc}", file=sys.stderr)
        return 1

    print(f"[SAVED] {target}")
    print("[NOTE] 报告由落盘结果机械生成；结论措辞由判定档位决定，不得手改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
