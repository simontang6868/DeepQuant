"""L3aa 的不变量测试：BY 校正与依赖诊断。

分两组：

1. **BY 的数学性质**——这几条是可以纯数学判定的，写错立刻能看出来：
   ``BY ⊆ BH``、m=1 退化、全零效应不放行、q 单调、step-up 的拒绝集合恰为前 k 条。
   其中 ``BY ⊆ BH`` 尤其重要：它是 BY 的签名性质，实现一旦把 H_m 的除法写反，
   这条一定失败（而单看某个 p 值可能看不出来）。

2. **依赖诊断的口径**——重点是「不同失败原因要报不同异常」。
   本项目把「文件不存在」当成「确认没有事件」已经错过 10 次，
   所以「文件不存在 / 筛完为空 / 缺列 / 月份重复」必须是四个不同的异常，
   不能合并成一个「没数据」。

另外用真实产物钉一条口径一致性：``dependence.load_entry`` 与
``citation.rebuild_series`` 对同一条预登记必须给出**完全相同**的数值——
前者留月份索引、后者丢索引，但数值不能有任何差别，否则依赖诊断算的就是另一条序列。
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from deepquant.gates import dependence as dp
from deepquant.gates import multiplicity as mp

REPO = Path(__file__).resolve().parents[3]


def _tmp_csv(tmp_path: Path, name: str, header: list[str], rows: list[list]) -> Path:
    path = tmp_path / name
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join("" if v is None else str(v) for v in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


# --------------------------------------------------------------------------
# 1. BY 的数学性质
# --------------------------------------------------------------------------


def test_harmonic_small_values():
    assert mp.harmonic(1) == pytest.approx(1.0)
    assert mp.harmonic(2) == pytest.approx(1.5)
    assert mp.harmonic(3) == pytest.approx(1 + 0.5 + 1 / 3, rel=0, abs=1e-15)


def test_harmonic_24_known_value():
    assert mp.harmonic(24) == pytest.approx(3.7759581777535067, abs=1e-12)


def test_harmonic_rejects_non_positive():
    for bad in (0, -1):
        with pytest.raises(mp.MultiplicityError):
            mp.harmonic(bad)


def test_harmonic_monotone():
    vals = [mp.harmonic(m) for m in range(1, 30)]
    assert all(b > a for a, b in zip(vals, vals[1:], strict=False))


def test_by_unit_threshold_formula():
    for m in (1, 5, 24, 27):
        assert mp.by_unit_threshold(q=0.05, m=m) == pytest.approx(
            0.05 / (m * mp.harmonic(m)), abs=1e-18
        )


def test_by_unit_threshold_24_value():
    assert mp.by_unit_threshold(q=0.05, m=24) == pytest.approx(0.00055174, abs=1e-8)


def test_by_equals_single_test_at_m1():
    # m=1 时 H_1=1，门槛就是 q 本身
    assert mp.benjamini_yekutieli([0.049], q=0.05, m=1) == [True]
    assert mp.benjamini_yekutieli([0.051], q=0.05, m=1) == [False]


def test_by_rejects_nothing_when_all_p_one():
    assert not any(mp.benjamini_yekutieli([1.0] * 20, q=0.05, m=24))


def test_by_rejects_everything_when_all_p_zero():
    assert all(mp.benjamini_yekutieli([0.0] * 5, q=0.05, m=24))


def test_by_is_subset_of_bh_random():
    """BY ⊆ BH 是数学必然。随机 500 组，任一组违反就是实现错。"""
    rng = random.Random(20261029)
    for _ in range(500):
        n = rng.randint(1, 30)
        pvals = [rng.random() ** rng.choice([1, 2, 3]) for _ in range(n)]
        m = n + rng.randint(0, 5)
        by = mp.benjamini_yekutieli(pvals, q=0.05, m=m)
        bh = mp.bh(pvals, q=0.05, m=m)
        assert not any(a and not b for a, b in zip(by, bh, strict=False))


def test_by_rejected_set_is_top_k():
    """step-up 的定义：拒绝集合必是排序后的前 k 条。"""
    pvals = [0.0001, 0.0020, 0.0040, 0.30, 0.50]
    flags = mp.benjamini_yekutieli(pvals, q=0.05, m=24)
    k = sum(flags)
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    assert set(i for i in range(len(pvals)) if flags[i]) == set(order[:k])


def test_by_monotone_in_q():
    rng = random.Random(7)
    pvals = sorted(rng.random() for _ in range(20))
    prev: set[int] = set()
    for q in (0.01, 0.02, 0.05, 0.10, 0.20):
        cur = {i for i, f in enumerate(mp.benjamini_yekutieli(pvals, q=q, m=24)) if f}
        assert prev <= cur, f"q={q} 时拒绝集合反而变小"
        prev = cur


def test_by_threshold_matches_flags():
    pvals = [0.0001, 0.0020, 0.0040, 0.30, 0.50]
    flags = mp.benjamini_yekutieli(pvals, q=0.05, m=24)
    thr = mp.by_threshold(pvals, q=0.05, m=24)
    if any(flags):
        assert thr == max(p for p, f in zip(pvals, flags, strict=False) if f)
    else:
        assert thr == 0.0


def test_by_can_reject_more_than_bonferroni_when_k_large():
    """BY 不是「处处比 Bonferroni 严」：step-up 在第 k 档会反超。

    m=24、q=0.05 时，BY 第 4 档门槛 0.0022070 高于 Bonferroni 的 0.0020833。
    所以 4 条 p=0.0021 的检验：BY 全拒绝，Bonferroni 一条也不拒绝。
    这条测试的意义是防止把 BY 误当成「Bonferroni 的加强版」——
    它是 FDR 类，不是 FWER 类。
    """
    pvals = [0.0021] * 4
    by = mp.benjamini_yekutieli(pvals, q=0.05, m=24)
    bonf = mp.bonferroni(pvals, alpha=0.05, m=24)
    assert all(by)
    assert not any(bonf)


def test_by_empty_and_invalid_m():
    assert mp.benjamini_yekutieli([], q=0.05, m=24) == []
    with pytest.raises(mp.MultiplicityError):
        mp.by_unit_threshold(q=0.05, m=0)


def test_apply_by_pads_counts_only_with_p_one():
    """只计名额的条目必须照占 m：补 p=1.0，不得从 m 里剔除。"""
    rows = [
        {"key": "a", "recomputed": {"t_nw3": 3.0, "n": 100}},
        {"key": "b", "recomputed": {"t_nw3": 0.5, "n": 100}},
        {
            "key": "c",
            "_counts_only": True,
            "stat": {"applicable": False},
            "recomputed": {"t_nw3": 9.9, "n": 100},
        },
    ]
    run = mp.apply_by(rows, m=10)
    assert run["n_scored"] == 2
    assert run["m_used"] == 10
    assert {i["key"] for i in run["items"]} == {"a", "b"}  # 只计名额的不出 p


def test_apply_by_m_at_least_n_scored():
    rows = [{"key": f"k{i}", "recomputed": {"t_nw3": 1.0 + i, "n": 50}} for i in range(6)]
    run = mp.apply_by(rows, m=2)
    assert run["m_used"] == 6


def test_apply_by_estimator_choice_changes_p():
    rows = [{"key": "a", "recomputed": {"t_nw3": 3.0, "t_iid": 2.0, "n": 100}}]
    a = mp.apply_by(rows, m=24, estimator="t_nw3")["items"][0]["p"]
    b = mp.apply_by(rows, m=24, estimator="t_iid")["items"][0]["p"]
    assert a < b


# --------------------------------------------------------------------------
# 2. 月份索引
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2007-02-28", "2007-02"),
        ("2007-02", "2007-02"),
        ("200702", "2007-02"),
        (" 2016-01-29 ", "2016-01"),
    ],
)
def test_month_key_formats(raw, expected):
    assert dp.month_key(raw) == expected


def test_month_key_rejects_garbage():
    with pytest.raises(dp.DependenceError):
        dp.month_key("not-a-date")


def test_shift_month_across_year_boundary():
    assert dp.shift_month("2007-01", -1) == "2006-12"
    assert dp.shift_month("2007-12", 1) == "2008-01"
    assert dp.shift_month("2007-06", 0) == "2007-06"
    assert dp.shift_month("2007-06", 12) == "2008-06"


# --------------------------------------------------------------------------
# 3. 读取：四种失败必须分得开
# --------------------------------------------------------------------------


def test_load_missing_file_raises_file_missing(tmp_path):
    with pytest.raises(dp.SeriesFileMissing):
        dp.load_monthly(tmp_path, key="x", path="nope.csv", column="d")


def test_load_empty_filter_raises_filter_empty(tmp_path):
    p = _tmp_csv(tmp_path, "a.csv", ["month", "cell", "d"], [["2007-01", "A", 1.0]])
    with pytest.raises(dp.SeriesFilterEmpty):
        dp.load_monthly(tmp_path, key="x", path="a.csv", column="d", filters={"cell": "Z"})


def test_load_missing_column_raises_column_missing(tmp_path):
    p = _tmp_csv(tmp_path, "a.csv", ["month", "d"], [["2007-01", 1.0]])
    with pytest.raises(dp.SeriesColumnMissing):
        dp.load_monthly(tmp_path, key="x", path="a.csv", column="nope")


def test_load_missing_filter_column_is_column_error_not_filter_empty(tmp_path):
    """过滤列不存在 ≠ 筛完为空。前者是口径错，后者是数据空。"""
    p = _tmp_csv(tmp_path, "a.csv", ["month", "d"], [["2007-01", 1.0]])
    with pytest.raises(dp.SeriesColumnMissing):
        dp.load_monthly(tmp_path, key="x", path="a.csv", column="d", filters={"cell": "A"})


def test_load_duplicate_month_raises(tmp_path):
    p = _tmp_csv(
        tmp_path,
        "a.csv",
        ["month", "cell", "d"],
        [["2007-01", "A", 1.0], ["2007-01", "B", 2.0]],
    )
    with pytest.raises(dp.SeriesMonthNotUnique):
        dp.load_monthly(tmp_path, key="x", path="a.csv", column="d")


def test_load_duplicate_month_ok_when_filter_disambiguates(tmp_path):
    p = _tmp_csv(
        tmp_path,
        "a.csv",
        ["month", "cell", "d"],
        [["2007-01", "A", 1.0], ["2007-01", "B", 2.0]],
    )
    s = dp.load_monthly(
        tmp_path, key="x", path="a.csv", column="d", filters={"cell": "A"}
    )
    assert s.n == 1
    assert s.values["2007-01"] == 1.0


def test_load_skips_blank_and_non_numeric(tmp_path):
    p = _tmp_csv(
        tmp_path,
        "a.csv",
        ["month", "d"],
        [["2007-01", 1.0], ["2007-02", None], ["2007-03", "abc"], ["2007-04", 2.0]],
    )
    s = dp.load_monthly(tmp_path, key="x", path="a.csv", column="d")
    assert sorted(s.values) == ["2007-01", "2007-04"]


def test_load_records_derived_date_flag(tmp_path):
    """只有 signal_date 的序列必须被标出来，否则对齐差异会被静默吞掉。"""
    p = _tmp_csv(tmp_path, "b.csv", ["signal_date", "d"], [["2007-01-31", 1.0]])
    s = dp.load_monthly(tmp_path, key="x", path="b.csv", column="d")
    assert s.date_column == "signal_date"
    assert s.derived_date is True

    q = _tmp_csv(tmp_path, "c.csv", ["signal_date", "month", "d"], [["2006-12-29", "2007-01-31", 1.0]])
    s2 = dp.load_monthly(tmp_path, key="y", path="c.csv", column="d")
    assert s2.date_column == "month"
    assert s2.derived_date is False


# --------------------------------------------------------------------------
# 4. 相关性与重叠
# --------------------------------------------------------------------------


def _series(key: str, values: dict[str, float], date_column: str = "month") -> dp.MonthSeries:
    return dp.MonthSeries(
        key=key,
        path=f"{key}.csv",
        column="d",
        date_column=date_column,
        values=values,
    )


def test_pearson_perfect_linear():
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [3.0 * x + 7.0 for x in xs]
    assert dp.pearson(xs, ys) == pytest.approx(1.0)
    assert dp.pearson(xs, [-y for y in ys]) == pytest.approx(-1.0)


def test_pearson_too_short_is_nan_not_zero():
    """样本不足要返回 nan。返回 0 会被下游读成「确认无相关」。"""
    assert math.isnan(dp.pearson([1.0, 2.0], [1.0, 2.0]))
    assert math.isnan(dp.pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))


def test_rho_p_value_matches_t_conversion():
    rho, n = 0.5, 20
    t = rho * math.sqrt(n - 2) / math.sqrt(1 - rho * rho)
    assert dp.rho_p_value(rho, n) == pytest.approx(mp.t_two_sided_p(t, n - 2))


def test_rho_p_value_edge_cases():
    assert dp.rho_p_value(1.0, 10) == 0.0
    assert math.isnan(dp.rho_p_value(float("nan"), 10))


def test_overlap_ratio_uses_shorter_window():
    a = _series("a", {f"2007-{m:02d}": 1.0 for m in range(1, 13)})  # 12 个月
    b = _series("b", {f"2007-{m:02d}": 1.0 for m in range(1, 7)})  # 6 个月，是 a 的子集
    assert dp.overlap(a, b)["ratio"] == pytest.approx(1.0)
    assert dp.overlap(a, b)["n_overlap"] == 6


def test_overlap_zero_when_disjoint():
    a = _series("a", {"2007-01": 1.0, "2007-02": 2.0})
    b = _series("b", {"2020-01": 1.0, "2020-02": 2.0})
    assert dp.overlap(a, b)["ratio"] == 0.0


def test_pairwise_picks_best_shift():
    """把 b 整体平移 1 个月后相关性才显现——主口径必须能取到它。"""
    months = [f"2007-{m:02d}" for m in range(1, 13)] + [f"2008-{m:02d}" for m in range(1, 13)]
    base = {m: float(i % 5) for i, m in enumerate(months)}
    a = _series("a", base)
    shifted = {dp.shift_month(m, 1): v for m, v in base.items()}
    b = _series("b", shifted, date_column="signal_date")
    info = dp.pairwise_dependence(a, b, shifts=(-1, 0, 1), min_overlap=6)
    assert info["max_abs_rho_shift"] == -1
    assert abs(info["max_abs_rho"]) == pytest.approx(1.0)
    assert abs(info["shift0"]["rho"]) < 0.99  # 不对齐时明显更低


def test_pairwise_marks_alignment_warning():
    a = _series("a", {"2007-01": 1.0, "2007-02": 2.0}, date_column="month")
    b = _series("b", {"2007-01": 1.0, "2007-02": 2.0}, date_column="signal_date")
    assert dp.pairwise_dependence(a, b)["alignment_warning"] is True


def test_pairwise_nan_when_overlap_too_small():
    a = _series("a", {"2007-01": 1.0, "2007-02": 2.0})
    b = _series("b", {"2007-01": 3.0, "2007-02": 4.0})
    info = dp.pairwise_dependence(a, b, min_overlap=24)
    assert info["max_abs_rho"] is None


# --------------------------------------------------------------------------
# 5. 有效检验数
# --------------------------------------------------------------------------


def test_li_ji_identity_gives_m():
    m = 6
    corr = [[1.0 if i == j else 0.0 for j in range(m)] for i in range(m)]
    assert dp.li_ji_m_eff(corr)["m_eff"] == pytest.approx(m)


def test_li_ji_all_ones_gives_one():
    m = 6
    corr = [[1.0] * m for _ in range(m)]
    assert dp.li_ji_m_eff(corr)["m_eff"] == pytest.approx(1.0, abs=1e-9)


def test_li_ji_monotone_between_extremes():
    """相关越强，有效检验数越小。"""
    m = 5

    def build(rho: float) -> list[list[float]]:
        return [[1.0 if i == j else rho for j in range(m)] for i in range(m)]

    vals = [dp.li_ji_m_eff(build(r))["m_eff"] for r in (0.0, 0.2, 0.5, 0.8)]
    assert all(b < a for a, b in zip(vals, vals[1:], strict=False))


def test_li_ji_single_dimension():
    assert dp.li_ji_m_eff([[1.0]])["m_eff"] == 1.0


def test_li_ji_rejects_non_square():
    with pytest.raises(dp.DependenceError):
        dp.li_ji_m_eff([[1.0, 0.0]])


def test_complete_submatrix_respects_thresholds():
    long = {f"2007-{m:02d}": float(m) for m in range(1, 13)}
    short = {"2007-01": 1.0, "2007-02": 2.0}
    series = {
        "a": _series("a", long),
        "b": _series("b", long),
        "c": _series("c", short),
    }
    keys, mat = dp.complete_submatrix(series, min_n=6, min_overlap=6)
    assert keys == ["a", "b"]  # c 太短，进不来
    assert len(mat) == 2 and mat[0][0] == 1.0 and mat[0][1] == pytest.approx(1.0)


def test_complete_submatrix_symmetric():
    base = {f"2007-{m:02d}": float((m * 7) % 11) for m in range(1, 13)}
    other = {f"2007-{m:02d}": float((m * 3) % 5) for m in range(1, 13)}
    series = {"a": _series("a", base), "b": _series("b", other)}
    _, mat = dp.complete_submatrix(series, min_n=6, min_overlap=6)
    assert mat[0][1] == pytest.approx(mat[1][0])


# --------------------------------------------------------------------------
# 6. 与 L3u 的口径一致性（用真实产物）
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not (REPO / "docs" / "gates" / "l3u_prereg.json").exists(),
    reason="仓库产物不在（打包环境）",
)
def test_load_entry_matches_rebuild_series_for_pivot_and_terms():
    """带索引的重建必须与 citation.rebuild_series 数值完全一致。

    这两条通路读同一个文件、走不同的代码：一条留月份、一条丢月份。
    数值上差一点都不行——依赖诊断算的必须是判定用的那条序列。
    """
    from deepquant.gates import citation as ct

    prereg = ct.load_prereg(REPO / "docs" / "gates" / "l3u_prereg.json")
    checked = 0
    undatable = []
    for entry in prereg["entries"]:
        if entry["series"]["kind"] not in dp.INDEXED_KINDS:
            continue
        file = REPO / entry["series"]["path"]
        if not file.exists():
            continue
        try:
            mine = dp.load_entry(REPO, entry)
        except dp.DependenceError as exc:
            undatable.append(entry["key"])
            assert isinstance(exc, dp.SeriesColumnMissing), exc
            continue
        theirs = ct.rebuild_series(entry, REPO)
        assert sorted(mine.values.values()) == pytest.approx(sorted(theirs.values)), entry["key"]
        checked += 1
    assert checked >= 10, f"只比对了 {checked} 条，样本不足"
    # 唯一没有日期列的序列是 phasem_spread（文件里只有整数行号）。
    # 钉死这个集合：多出一条「定不了日期」的，说明有产物换了格式，必须查。
    assert undatable == ["phasem_spread"]


@pytest.mark.skipif(
    not (REPO / "docs" / "gates" / "l3u_prereg.json").exists(),
    reason="仓库产物不在（打包环境）",
)
def test_phasep_is_exact_subwindow_of_phaseo():
    """L3aa 的发现：phasep_late 与 phaseo_netBASE 在共同月上逐位相同。

    固定这条事实，防止以后有人「顺手」改了其中一条的口径而没人发现：
    一旦它们不再逐位相同，m 的口径与「同源子窗口」的说法都要重审。
    """
    from deepquant.gates import citation as ct

    prereg = ct.load_prereg(REPO / "docs" / "gates" / "l3u_prereg.json")
    entries = {e["key"]: e for e in prereg["entries"]}
    a = dp.load_entry(REPO, entries["phaseo_netBASE"])
    b = dp.load_entry(REPO, entries["phasep_late"])
    if not (REPO / entries["phaseo_netBASE"]["series"]["path"]).exists():
        pytest.skip("产物不在")
    common = sorted(set(a.values) & set(b.values))
    assert len(common) == 129
    assert all(abs(a.values[m] - b.values[m]) < 1e-12 for m in common)
