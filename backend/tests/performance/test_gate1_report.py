"""`performance/gate1_report.py` 的契约测试。

这个模块的职责是「让结论无法被手写」，所以测试重点不是格式好不好看，而是：

1. **完整性**：结果文件被改过一个字节，就必须拒绝出报告。
2. **措辞锁定**：三档各对应一段固定文字，非成功档不得出现成功专用措辞。
3. **数字不漂移**：报告里的数必须与 `summary.json` / `sensitivity.csv` 逐位一致。
4. **四层分离**：第 4 层（真实可交易性）必须显式写明「本报告不能回答」。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from deepquant.performance import gate1_report as gr

PREREG_SHA = "5bef0e3b1c2d"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(verdict: str = "insufficient", *, with_control: bool = False) -> dict:
    label = {"success": "成功", "failure": "失败", "insufficient": "证据不足"}[verdict]
    s = {
        "prereg_version": "gate1-prereg-v1.1",
        "prereg_sha256_prefix": PREREG_SHA,
        "main": {
            "n": 48, "mean_d": 0.001234, "std_d": 0.0611,
            "t_stat": 1.399, "p_value": 0.1682, "win_ratio": 0.5208,
        },
        "judgement": {
            "verdict": verdict, "label": label, "n_positive_sensitivity": 6,
            "rule_hits": [] if verdict != "failure" else ["mean(d) ≤ 0"],
            "insufficient_flags": [] if verdict != "insufficient" else ["某条 flag"],
        },
        "oos_window": ["2021-01-01", "2024-12-31"],
        "n_months": 48,
        "price_limit_rate": 0.10,
        "elapsed_seconds": 512.3,
    }
    if with_control:
        s["excluded_symbols"] = ["600809.SH"]
        s["control_excluded"] = {
            "excluded_symbols": ["600809.SH"],
            "stats": {"n": 48, "mean_d": -0.0004, "std_d": 0.06,
                      "t_stat": -0.05, "p_value": 0.96, "win_ratio": 0.48},
        }
    return s


def _sensitivity() -> pd.DataFrame:
    rows = [{
        "name": "main", "variable": "—", "value": "基准配置", "n": 48,
        "mean_d": 0.001234, "std_d": 0.0611, "t_stat": 1.399, "p_value": 0.1682,
        "win_ratio": 0.5208, "strategy_total_return": 0.31, "benchmark_total_return": 0.25,
        "q_value": None,
    }]
    for i in range(12):
        rows.append({
            "name": f"S{i + 1}", "variable": "lookback", "value": str(20 * (i + 1)),
            "n": 48, "mean_d": 0.0010 - i * 0.0002, "std_d": 0.06,
            "t_stat": 1.0 - i * 0.1, "p_value": 0.3 + i * 0.01, "win_ratio": 0.5,
            "strategy_total_return": 0.3, "benchmark_total_return": 0.25,
            "q_value": 0.4 + i * 0.01,
        })
    return pd.DataFrame(rows)


def _paired() -> pd.DataFrame:
    months = [f"2021-{m:02d}" for m in range(1, 13)] * 4
    return pd.DataFrame({
        "month": months,
        "r_strategy": [0.01] * 48,
        "r_benchmark": [0.008] * 48,
        "d": [0.002] * 48,
    })


def make_run_dir(root: Path, verdict: str = "insufficient", *, with_control: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(
        json.dumps(_summary(verdict, with_control=with_control), ensure_ascii=False),
        encoding="utf-8",
    )
    _sensitivity().to_csv(root / "sensitivity.csv", index=False)
    _paired().to_csv(root / "monthly_paired.csv", index=False)
    files = {
        p.name: {"sha256": _sha256(p), "bytes": p.stat().st_size}
        for p in sorted(root.iterdir()) if p.is_file()
    }
    (root / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# 完整性
# --------------------------------------------------------------------------- #


def test_load_run_accepts_freshly_written_run(tmp_path):
    d = make_run_dir(tmp_path / "run")
    run = gr.load_run(d)
    assert run.verdict == "insufficient"
    assert len(run.paired) == 48
    assert len(run.sensitivity) == 13


def test_tampered_result_file_is_refused(tmp_path):
    """把 mean_d 从 0.0012 改成 0.09 —— 必须被发现，不能生成报告。"""
    d = make_run_dir(tmp_path / "run")
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    s["main"]["mean_d"] = 0.09
    (d / "summary.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="sha256"):
        gr.load_run(d)


def test_missing_manifest_is_refused(tmp_path):
    d = make_run_dir(tmp_path / "run")
    (d / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="manifest"):
        gr.load_run(d)


def test_manifest_listing_absent_file_is_refused(tmp_path):
    d = make_run_dir(tmp_path / "run")
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["files"]["ghost.csv"] = {"sha256": "0" * 64, "bytes": 1}
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="ghost.csv"):
        gr.load_run(d)


def test_n_months_must_match_paired_rows(tmp_path):
    """summary 与逐月明细两处记载必须一致，否则说明落盘不同步。"""
    d = make_run_dir(tmp_path / "run")
    _paired().iloc[:40].to_csv(d / "monthly_paired.csv", index=False)
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["files"]["monthly_paired.csv"] = {
        "sha256": _sha256(d / "monthly_paired.csv"),
        "bytes": (d / "monthly_paired.csv").stat().st_size,
    }
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")

    with pytest.raises(ValueError, match="n_months"):
        gr.load_run(d)


def test_illegal_verdict_in_summary_is_refused(tmp_path):
    d = make_run_dir(tmp_path / "run")
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    s["judgement"]["verdict"] = "maybe_works"
    (d / "summary.json").write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["files"]["summary.json"] = {
        "sha256": _sha256(d / "summary.json"), "bytes": (d / "summary.json").stat().st_size}
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")

    with pytest.raises(ValueError, match="verdict"):
        gr.load_run(d)


# --------------------------------------------------------------------------- #
# 措辞锁定
# --------------------------------------------------------------------------- #


def test_three_verdicts_have_distinct_statements():
    texts = {v: gr.VERDICT_STATEMENTS[v] for v in ("success", "failure", "insufficient")}
    assert len(set(texts.values())) == 3


def test_insufficient_report_says_not_failure_not_success(tmp_path):
    run = gr.load_run(make_run_dir(tmp_path / "run", "insufficient"))
    text = gr.render_report(run, generated_at="2026-10-18 18:40 GMT+8")
    assert "不是失败，也不是成功" in text
    assert "`insufficient`" in text
    assert "基本有效" in text and "基本无效" in text   # 禁令本身要写出来


def test_failure_report_does_not_soften_into_parameter_tuning(tmp_path):
    run = gr.load_run(make_run_dir(tmp_path / "run", "failure"))
    text = gr.render_report(run, generated_at="t")
    assert "参数没调好" in text
    assert "mean(d) ≤ 0" in text            # rule_hits 必须原样列出


@pytest.mark.parametrize("verdict", ["failure", "insufficient"])
def test_non_success_report_contains_no_success_phrasing(tmp_path, verdict):
    run = gr.load_run(make_run_dir(tmp_path / "run", verdict))
    text = gr.render_report(run, generated_at="t")
    for phrase in gr.SUCCESS_ONLY_PHRASES:
        assert phrase not in text, f"{verdict} 档出现了 {phrase}"


def test_guard_catches_success_phrasing_leaked_into_failure(monkeypatch, tmp_path):
    """把「策略有效」塞进失败档的模板 → 守卫必须抛错，而不是照常输出。"""
    tampered = dict(gr.VERDICT_STATEMENTS)
    tampered["failure"] = "本实验证明策略有效。"
    monkeypatch.setattr(gr, "VERDICT_STATEMENTS", tampered)

    run = gr.load_run(make_run_dir(tmp_path / "run", "failure"))
    with pytest.raises(AssertionError, match="不应出现的措辞"):
        gr.render_report(run, generated_at="t")


# --------------------------------------------------------------------------- #
# 数字不漂移
# --------------------------------------------------------------------------- #


def test_reported_numbers_equal_summary_verbatim(tmp_path):
    run = gr.load_run(make_run_dir(tmp_path / "run"))
    text = gr.render_report(run, generated_at="t")
    s = run.summary["main"]

    assert f"{s['mean_d']:+.6f}" in text
    assert f"{s['t_stat']:+.3f}" in text
    assert f"{s['p_value']:.4f}" in text
    assert f"{s['win_ratio'] * 100:.1f}%" in text


def test_report_quotes_prereg_hash_and_run_id(tmp_path):
    d = make_run_dir(tmp_path / "runABC")
    run = gr.load_run(d)
    text = gr.render_report(run, generated_at="t")
    assert PREREG_SHA in text
    assert "runABC" in text
    assert "协议先于结果存在" in text


def test_all_sensitivity_rows_appear(tmp_path):
    run = gr.load_run(make_run_dir(tmp_path / "run"))
    text = gr.render_report(run, generated_at="t")
    for name in [f"S{i}" for i in range(1, 13)]:
        assert f"| {name} |" in text


# --------------------------------------------------------------------------- #
# 四层分离
# --------------------------------------------------------------------------- #


def test_four_layers_are_separated_and_layer4_disclaimed(tmp_path):
    run = gr.load_run(make_run_dir(tmp_path / "run"))
    text = gr.render_report(run, generated_at="t")

    assert "软件功能是否正确" in text
    assert "回测计算是否可信" in text
    assert "策略是否显示统计优势" in text
    assert "是否具备真实交易可行性" in text
    # 第 4 层必须显式否认，不得让读者把统计优势读成可交易
    assert "不得由第 3 层推出" in text


def test_layer3_status_follows_verdict(tmp_path):
    for verdict in ("success", "failure", "insufficient"):
        run = gr.load_run(make_run_dir(tmp_path / f"run_{verdict}", verdict))
        text = gr.render_report(run, generated_at="t")
        assert f"`{verdict}`" in text


def test_control_run_section_appears_only_when_present(tmp_path):
    plain = gr.render_report(
        gr.load_run(make_run_dir(tmp_path / "a", with_control=False)), generated_at="t")
    assert "对照跑" not in plain

    with_ctrl = gr.render_report(
        gr.load_run(make_run_dir(tmp_path / "b", with_control=True)), generated_at="t")
    assert "对照跑" in with_ctrl
    assert "600809.SH" in with_ctrl


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #


def test_write_report_creates_file(tmp_path):
    d = make_run_dir(tmp_path / "run")
    target = gr.write_report(d, generated_at="t")
    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("# Gate 1")


def test_write_report_refuses_overwrite(tmp_path):
    d = make_run_dir(tmp_path / "run")
    gr.write_report(d, generated_at="t")
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        gr.write_report(d, generated_at="t2")


# --------------------------------------------------------------------------- #
# 排版细节（写错会误导：std 带正号看起来像有方向的量）
# --------------------------------------------------------------------------- #


def test_std_is_rendered_without_sign(tmp_path):
    run = gr.load_run(make_run_dir(tmp_path / "run"))
    text = gr.render_report(run, generated_at="t")
    assert "| std(d) | 0.061100 |" in text
    assert "| std(d) | +" not in text


def test_sensitivity_table_is_numbered_before_control_section(tmp_path):
    run = gr.load_run(make_run_dir(tmp_path / "run", with_control=True))
    text = gr.render_report(run, generated_at="t")
    assert "### 4.1" in text
    assert text.index("### 4.1") < text.index("### 4.2")


def test_markdown_has_no_triple_blank_lines(tmp_path):
    run = gr.load_run(make_run_dir(tmp_path / "run"))
    assert "\n\n\n" not in gr.render_report(run, generated_at="t")
