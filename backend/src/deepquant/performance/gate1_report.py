"""Gate 1 结果报告的**机械生成器**（预登记 `gate1-prereg-v1.3`）。

为什么要有这个模块，而不是「跑完手写一份报告」：

1. **结论措辞必须由判定档位决定，不能由写报告的人决定。** 三档各对应一段固定
   文字，`render_report` 按 `summary.json` 里的 `verdict` 选段，不给人挑词的空间。
   尤其「证据不足」这一档最容易在事后被写成「基本有效」或「基本无效」。
2. **数字必须来自落盘文件，不来自记忆。** 报告里的每个数都从
   `summary.json` / `sensitivity.csv` / `monthly_paired.csv` 读出，重新计算只在
   校验时发生（对不上就报错），不在渲染时发生。
3. **读之前先验完整性。** `manifest.json` 记录了每个文件的 sha256；任一文件
   被改动过，`load_run` 直接抛错——一份被手改过的结果不该被拿来出报告。
4. **四层结论物理分离。** 软件功能 / 回测可信 / 统计优势 / 真实可交易性，
   本报告只回答第 3 层，其余三层写明「本报告不能回答」及依据在哪。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd

# --------------------------------------------------------------------------- #
# 结论措辞：一档一句，机械选取
# --------------------------------------------------------------------------- #

VERDICT_STATEMENTS: dict[str, str] = {
    "success": (
        "在预登记 `gate1-prereg-v1.3` 写死的判据下，本轮实验**达到「成功」档**：\n"
        "OOS 月均配对超额 `mean(d) ≥ +0.30pp`、`t ≥ 2.0`、且 12 组敏感性中至少 8 组为正。\n\n"
        "**这不等于「策略可交易」**：成功档只覆盖统计优势层，未检验容量、冲击成本、"
        "借券可得性与实盘滑点（见第 5 节第 4 层）。\n"
        "也不等于「可外推到 2017–2020」：池子存在单向幸存者偏差（见第 6 节）。"
    ),
    "failure": (
        "在预登记 `gate1-prereg-v1.3` 写死的判据下，本轮实验**判「失败」档**。\n"
        "失败档不是「效果比较弱」，而是判据里明确列出的任一条被触发（见第 2 节 rule_hits）。\n\n"
        "**失败档不需要用「参数没调好」来软化**：本轮主实验只有一个参数组合、"
        "自由度为零，不存在「换组参数就有效」这种解释空间——那属于新的实验，不属于本研究。"
    ),
    "insufficient": (
        "在预登记 `gate1-prereg-v1.3` 写死的判据下，本轮实验**判「证据不足」档**。\n\n"
        "**「证据不足」不是失败，也不是成功。** 它表示：现有数据与可实现的执行规则下，"
        "既无法认定策略具备统计优势，也无法认定其无效——通常是样本长度导致的检验功效不足，"
        "或某条无法实现的约束、某处数据缺陷的影响无法隔离。\n\n"
        "**不得在引用本报告时把它写成「基本有效」或「基本无效」**，也不得据此启动实盘。"
    ),
}

# 只有 success 档才能出现的措辞。其余两档若出现，说明报告被手改过。
SUCCESS_ONLY_PHRASES: tuple[str, ...] = (
    "策略有效",
    "验证有效",
    "证明有效",
    "显著优于基准",
    "可以投入使用",
)

# --------------------------------------------------------------------------- #
# 完整性校验
# --------------------------------------------------------------------------- #


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest(out_dir: Path) -> dict[str, object]:
    """按 `manifest.json` 逐文件重算 sha256。任一不符立即抛错。

    不「警告后继续」：一份被改过的结果被渲染成报告，比没有报告更糟——
    报告会带着预登记哈希前缀，看起来像是协议保证过的结论。
    """
    path = Path(out_dir)
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"{path} 下没有 manifest.json，无法校验结果完整性")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") or {}
    if not files:
        raise ValueError("manifest.json 里 files 为空，无法校验")

    for name, meta in files.items():
        f = path / name
        if not f.exists():
            raise FileNotFoundError(f"manifest 记录的文件缺失：{name}")
        actual = _sha256(f)
        if actual != meta.get("sha256"):
            raise ValueError(
                f"{name} 的 sha256 与 manifest 不符（记录 {meta.get('sha256')[:12]}…，"
                f"实际 {actual[:12]}…）——结果文件被改动过，拒绝据此生成报告"
            )
        size = int(meta.get("bytes") or -1)
        if size >= 0 and f.stat().st_size != size:
            raise ValueError(f"{name} 字节数与 manifest 不符")
    return manifest


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunData:
    out_dir: Path
    summary: dict
    sensitivity: pd.DataFrame
    paired: pd.DataFrame
    manifest: dict

    @property
    def verdict(self) -> str:
        return str(self.summary["judgement"]["verdict"])

    @property
    def label(self) -> str:
        return str(self.summary["judgement"]["label"])


def load_run(out_dir: Path) -> RunData:
    """读取一次运行的全部产物，**先校验再解析**。"""
    path = Path(out_dir)
    summary_path = path / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"{path} 下没有 summary.json：这不是一次有效运行")

    manifest = verify_manifest(path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    sens_path = path / "sensitivity.csv"
    paired_path = path / "monthly_paired.csv"
    sensitivity = pd.read_csv(sens_path) if sens_path.exists() else pd.DataFrame()
    paired = pd.read_csv(paired_path) if paired_path.exists() else pd.DataFrame()

    judgement = summary.get("judgement") or {}
    if judgement.get("verdict") not in VERDICT_STATEMENTS:
        raise ValueError(f"summary.json 里 verdict 非法：{judgement.get('verdict')!r}")

    # 交叉校验：summary 的 n_months 必须与逐月明细行数一致，防止两处记载不同步
    if len(paired) and "n_months" in summary and int(summary["n_months"]) != len(paired):
        raise ValueError(
            f"summary.n_months={summary['n_months']} 与 monthly_paired.csv 行数 "
            f"{len(paired)} 不一致"
        )
    return RunData(path, summary, sensitivity, paired, manifest)


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #


def _pct(x: float | None, digits: int = 2) -> str:
    # 空值可能是 None，也可能是 CSV 空列读回来的 NaN —— 两者都必须显示「—」，
    # 否则会渲染成 "+nan%" 这种既难看又误导的字符串。
    if x is None or pd.isna(x):
        return "—"
    return f"{float(x) * 100:+.{digits}f}%"


def _num(x: float | None, digits: int = 4) -> str:
    """带符号：用于 mean(d)、t 统计量这类「方向有意义」的量。"""
    return "—" if x is None else f"{x:+.{digits}f}"


def _mag(x: float | None, digits: int = 4) -> str:
    """不带符号：用于 std 这类「方向无意义」的量（写成 +0.0611 是错的）。"""
    return "—" if x is None else f"{x:.{digits}f}"


def _sensitivity_table(sensitivity: pd.DataFrame) -> str:
    if sensitivity.empty:
        return "_（本次运行未落盘 sensitivity.csv）_\n"
    # 新口径（2026-10-19 起）：*_eval 是评估窗口内累计，*_full 才是全程累计。
    # 旧结果目录只有 *_total_return（语义为全程），保留兼容，但表头必须说清，
    # 不能把全程值标成「窗口内累计」。
    has_eval = {"strategy_total_return_eval", "benchmark_total_return_eval"} <= set(
        sensitivity.columns
    )
    s_col = "strategy_total_return_eval" if has_eval else "strategy_total_return"
    b_col = "benchmark_total_return_eval" if has_eval else "benchmark_total_return"
    s_head = "策略累计(窗口)" if has_eval else "策略累计(全程*)"
    b_head = "基准累计(窗口)" if has_eval else "基准累计(全程*)"
    lines = [
        f"| 组 | 变量 | 取值 | n | mean(d) | t | p | win | {s_head} | {b_head} | BH q |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for _, r in sensitivity.iterrows():
        q = r.get("q_value")
        q_txt = "—" if pd.isna(q) else f"{float(q):.4f}"
        lines.append(
            f"| {r['name']} | {r['variable']} | {r['value']} | {int(r['n'])} | "
            f"{_num(r['mean_d'], 6)} | {_num(r['t_stat'], 3)} | {float(r['p_value']):.4f} | "
            f"{float(r['win_ratio']) * 100:.1f}% | {_pct(r.get(s_col))} | "
            f"{_pct(r.get(b_col))} | {q_txt} |"
        )
    if not has_eval:
        lines.append("")
        lines.append(
            "> `*` 该结果目录由旧版脚本生成，累计列是**全程**（2017-02 ~ 2024-12）累计，"
            "不随判定窗口截断，不能读成窗口内累计。新跑的结果会带 "
            "`*_total_return_eval` / `*_total_return_full` 两组列。"
        )
    return "\n".join(lines) + "\n"


def _flag_lines(judgement: dict) -> str:
    flags: Sequence[str] = judgement.get("insufficient_flags") or []
    hits: Sequence[str] = judgement.get("rule_hits") or []
    out = []
    if hits:
        out.append("**触发的判据**：\n")
        out += [f"- {h}" for h in hits]
        out.append("")
    if flags:
        out.append("**成立的「证据不足」情形（预登记 §10.2 末三条）**：\n")
        out += [f"- {f}" for f in flags]
        out.append("")
    if not hits and not flags:
        out.append("_无 rule_hits / insufficient_flags。_\n")
    return "\n".join(out)


def _control_section(summary: dict) -> str:
    control = summary.get("control_excluded")
    if not control:
        return ""
    ex = ", ".join(control.get("excluded_symbols") or [])
    st = control.get("stats") or {}
    inputs = summary.get("inputs") or {}
    main_pool = inputs.get("main_pool") or {}
    ctrl_pool = inputs.get("control_pool") or {}
    rows = (
        f"| n | {summary.get('n_months')} | {st.get('n')} |\n"
        f"| mean(d) | {_num(summary.get('main', {}).get('mean_d'), 6)} | "
        f"{_num(st.get('mean_d'), 6)} |\n"
        f"| t | {_num(summary.get('main', {}).get('t_stat'), 3)} | "
        f"{_num(st.get('t_stat'), 3)} |\n"
    )
    if main_pool and ctrl_pool:
        rows += (
            f"| 池内标的数 | {main_pool.get('n_symbols')} | {ctrl_pool.get('n_symbols')} |\n"
            f"| 输入指纹（前 8 位） | `{str(main_pool.get('fingerprint'))[:8]}` | "
            f"`{str(ctrl_pool.get('fingerprint'))[:8]}` |\n"
        )
    return (
        f"\n### 4.2 对照跑：排除 `{ex}` 后的主实验口径\n\n"
        f"| 项 | 含该股（主实验） | 排除该股（对照） |\n|---|---|---|\n"
        f"{rows}\n"
        "判定方式（预登记 §10.2）：两版 `mean(d)` 符号不一致 → 该股数据缺陷影响无法隔离，"
        "追加「证据不足」。\n"
        + (
            "\n> 两个池子的输入指纹不同，说明对照确实在**全池**上独立重跑，"
            "不是「已剔除数据上重跑同一配置」的退化解。\n"
            if main_pool and ctrl_pool
            else ""
        )
    )


def render_report(
    run: RunData,
    *,
    generated_at: str,
    authorized_by: str | None = None,
    extra_notes: Sequence[str] = (),
) -> str:
    """渲染 Markdown 报告。结论措辞由 `verdict` 机械选定，不接受外部传入。"""
    s = run.summary
    j = s["judgement"]
    main = s.get("main") or {}
    verdict = run.verdict

    statement = VERDICT_STATEMENTS[verdict]
    excl = s.get("excluded_symbols") or []
    excl_txt = ", ".join(excl) if excl else "无"

    parts: list[str] = []
    parts.append("# Gate 1 横截面动量实验 · 结果报告\n")
    parts.append("> 本文件由 `scripts/gate1_report.py` 从落盘结果**机械生成**。\n"
                 "> 结论措辞由判定档位决定，不随叙述需要更改。手改本文件即视为伪造。\n")

    parts.append("\n## 1. 运行元信息\n")
    parts.append("| 项 | 值 |\n|---|---|")
    parts.append(f"| 预登记版本 | `{s.get('prereg_version')}` |")
    parts.append(f"| 预登记 sha256 前缀 | `{s.get('prereg_sha256_prefix')}` |")
    parts.append(f"| 结果目录 | `{run.out_dir.name}` |")
    parts.append(f"| OOS 区间 | {', '.join(s.get('oos_window') or [])}（{s.get('n_months')} 个月） |")
    parts.append(f"| 排除标的 | {excl_txt} |")
    parts.append(f"| 涨跌停比例（降级值） | {float(s.get('price_limit_rate', 0)):.0%} |")
    parts.append(f"| 回测耗时 | {s.get('elapsed_seconds')} s |")
    parts.append(f"| 报告生成时间 | {generated_at} |")
    if authorized_by:
        parts.append(f"| 授权原话 | {authorized_by} |")
    parts.append("")

    parts.append("\n## 2. 判定\n")
    parts.append(f"**判定档位：`{verdict}`（{run.label}）**\n")
    parts.append(statement)
    parts.append("")
    parts.append(_flag_lines(j))

    parts.append("\n## 3. 主实验结果\n")
    parts.append("| 指标 | 值 |\n|---|---|")
    parts.append(f"| 观测月数 n | {main.get('n')} |")
    parts.append(f"| mean(d)（月均配对超额） | {_num(main.get('mean_d'), 6)} |")
    parts.append(f"| std(d) | {_mag(main.get('std_d'), 6)} |")
    parts.append(f"| t 统计量 | {_num(main.get('t_stat'), 3)} |")
    parts.append(f"| 双侧 p | {main.get('p_value'):.4f} |" if main.get("p_value") is not None else "| 双侧 p | — |")
    parts.append(f"| 胜率（d>0 月份占比） | {float(main.get('win_ratio') or 0) * 100:.1f}% |")
    parts.append("")
    parts.append("> `d_t = r_strategy,t − r_benchmark,t`，基准为**同池等权**（不是沪深 300）。"
                 "窗口级/月度统计不等于连续账户净值年化（R12）。\n")

    parts.append("\n## 4. 敏感性（12 组，BH 校正）\n")
    parts.append("### 4.1 全组明细（首行为主实验，仅作对照，不参与多重检验校正）\n")
    parts.append(_sensitivity_table(run.sensitivity))
    parts.append(_control_section(s))
    parts.append(
        "> 多重检验：12 组的 q 值按 Benjamini–Hochberg 计算，"
        "**禁止用其中 1 组 p<0.05 当作成功证据**（预登记 §11）。\n"
    )

    parts.append("\n## 5. 四层结论（不可互相替代）\n")
    parts.append(
        "| 层 | 问题 | 本报告能否回答 | 结论 / 依据 |\n"
        "|---|---|---|---|\n"
        "| 1 | 软件功能是否正确 | 否 | 由全量 pytest 回归与各模块契约测试回答，本报告不重复判定 |\n"
        "| 2 | 回测计算是否可信 | 否 | 由 `docs/A3_ACCOUNTING_SPEC.md` 四项记账口径与引擎一致性测试回答 |\n"
        f"| 3 | 策略是否显示统计优势 | **是** | 机械判定为 `{verdict}`（{run.label}），依据见第 2、3、4 节 |\n"
        "| 4 | 是否具备真实交易可行性 | 否 | 未做容量、冲击成本、借券可得性、实盘滑点检验。"
        "**不得由第 3 层推出** |\n"
    )

    parts.append("\n## 6. 数据缺陷与已知局限\n")
    parts.append(
        "- **无历史时点成分股**：股票池是建池时点的快照，幸存者偏差不可消除，"
        "且偏差**单向**（池子随时间变宽，早期样本赢家化更重）。"
        "因此 OOS 的正超额**不能外推到 2017–2020**。\n"
        "- **涨跌停降级**：组合层只支持单一比例，预登记 §6 的 board 模式无法实现。"
        "实取 10%（主板正确、2020-08-24 前创业板亦正确，仅之后对双创偏严 → 悲观方向）。\n"
        "- **停牌只能近似**：无停牌标记，以「当日无行情行」近似。\n"
        "- **无行业字段**：未做行业中性，结果可能包含行业暴露。\n"
        f"- **排除标的**：{excl_txt}。\n"
    )
    for note in extra_notes:
        parts.append(f"- {note}\n")
    parts.append("\n## 7. 引用要求\n")
    parts.append(
        f"引用本报告任何结论时，必须同时给出预登记 sha256 前缀 `{s.get('prereg_sha256_prefix')}` "
        f"与结果目录 `{run.out_dir.name}`，以证明协议先于结果存在。"
        "单独引用某个数字、不引用判定档位，视为断章取义。\n"
    )

    text = "\n".join(parts)
    # 各段自带尾换行，直接拼会产生 3 个以上连续空行；压到最多 1 个空行
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 最后一道自检：非 success 档不得出现 success 专用措辞
    if verdict != "success":
        hit = [p for p in SUCCESS_ONLY_PHRASES if p in text]
        if hit:
            raise AssertionError(
                f"报告含非 {verdict} 档不应出现的措辞 {hit}——结论被写成了成功，拒绝输出"
            )
    return text


def write_report(
    out_dir: Path,
    *,
    generated_at: str,
    authorized_by: str | None = None,
    extra_notes: Sequence[str] = (),
    filename: str = "GATE1_RESULT_REPORT.md",
) -> Path:
    """生成并落盘报告。已存在则拒绝覆盖（同 run 只允许一份报告）。"""
    run = load_run(out_dir)
    text = render_report(
        run, generated_at=generated_at, authorized_by=authorized_by, extra_notes=extra_notes
    )
    target = Path(out_dir) / filename
    if target.exists():
        raise FileExistsError(
            f"{target} 已存在，拒绝覆盖：同一 run 只允许一份报告，"
            "重写会让「报告与结果一一对应」这条证据失效"
        )
    target.write_text(text, encoding="utf-8")
    return target
