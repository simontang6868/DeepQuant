"""L3ae 的闸门测试：成本档补全。

重点不在「数字对不对」（那由产物哈希保证），而在**口径与表述**：

- 成本必须是月度、必须是 `2×单边换手×费率`（本项目在这里错过一次）；
- `se_iid` 必须用 ddof=1（用 ddof=0 差 0.2%，足以让闸门通过而口径不同）；
- 两套 t 不能混用，判定口径不能因为 NW(3) 更大就被替换；
- 清单不能出现「成本吸收已被推翻」「可实盘」这类禁止表述；
- 预登记与冻结输入被改动时必须拒跑，而不是静默继续。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from deepquant.gates import cost_tiers as ct

REPO = Path(__file__).resolve().parents[3]
PREREG = REPO / "docs" / "gates" / "l3ae_prereg.json"
SUMMARY = REPO / "phasei_out" / "8f5c35121fdb" / "summary.json"
MONTHLY = REPO / "phasei_out" / "8f5c35121fdb" / "monthly.csv"
SHEET = REPO / "docs" / "DELIVERABLE_QUOTE_SHEET_L3AE.md"
REPORT = REPO / "reports" / "L3AE_PHASEI_COST_TIERS.md"

needs_artifacts = pytest.mark.skipif(
    not (SUMMARY.exists() and MONTHLY.exists() and PREREG.exists()),
    reason="L3ae 冻结输入缺失",
)


@pytest.fixture(scope="module")
def res() -> dict:
    return ct.build(REPO)


# ==================== 1 费率构成
def test_equivalent_single_side_matches_phase_o_constant() -> None:
    """等价对称单边必须是 0.051% —— 与 `test_phase_o` 同一个数、同一个来源。"""
    assert ct.equivalent_single_side() == pytest.approx(0.00051, abs=1e-12)


def test_stamp_duty_is_sell_side_only() -> None:
    """把印花按双边算会得到 0.076%，是错的。"""
    wrong = ct.COMM_RATE + ct.TRANSFER_RATE + ct.STAMP_RATE
    assert ct.equivalent_single_side() < wrong
    assert ct.equivalent_single_side() == pytest.approx(
        (2 * (ct.COMM_RATE + ct.TRANSFER_RATE) + ct.STAMP_RATE) / 2, abs=1e-15
    )


# ==================== 2 成本公式
@needs_artifacts
def test_cost_is_monthly_not_annualized(res: dict) -> None:
    """月度成本 = 2×换手×费率×100。乘 12 会得到 0.4527pp（年化），本项目的旧错。"""
    t = next(x for x in res["tiers"] if x["id"] == "T_0p1")
    want = 2 * res["turnover_one_side"] * 0.001 * 100
    assert t["cost_pp_month"] == pytest.approx(want, rel=1e-12)
    # 年化值是 12 倍，单独存在另一个字段里，两者不能混
    assert t["cost_pp_year"] == pytest.approx(t["cost_pp_month"] * 12, rel=1e-12)
    assert t["cost_pp_year"] > 10 * t["cost_pp_month"]


@needs_artifacts
def test_cost_is_linear_in_rate(res: dict) -> None:
    """同换手下 cost/rate 必须是常数——这是「只换费率、不重跑」能被允许的前提。"""
    ratios = [t["cost_pp_month"] / t["rate"] for t in res["tiers"]]
    assert max(ratios) - min(ratios) == pytest.approx(0.0, abs=1e-12)


@needs_artifacts
def test_net_identity_holds_per_tier(res: dict) -> None:
    for t in res["tiers"]:
        assert t["net_pp_month"] == pytest.approx(
            res["mean_gross_pp"] - t["cost_pp_month"], abs=1e-12
        )


# ==================== 3 标准误
@needs_artifacts
def test_iid_se_uses_sample_stdev_not_population(res: dict) -> None:
    """ddof 必须是 1。

    用总体标准差会得到 0.048312 而不是 0.048414——相对差 0.2%，
    大于 Phase-O 类闸门常用的 1e-3 相对容差，但小于 1e-2，
    所以「差不多」的检查抓不住它。这里直接钉死两个数的区别。
    """
    import statistics

    from deepquant.gates.cost_tiers import monthly_values

    vals = monthly_values(MONTHLY, "alpha_v1_pp")
    ddof1 = statistics.stdev(vals) / math.sqrt(len(vals))
    ddof0 = statistics.pstdev(vals) / math.sqrt(len(vals))
    assert res["se_iid_pp"] == pytest.approx(ddof1, rel=1e-12)
    # 两者必须可分辨，否则这条测试没有意义
    assert abs(ddof1 - ddof0) / ddof1 > 1e-3


@needs_artifacts
def test_se_does_not_vary_with_rate(res: dict) -> None:
    """phasei 把成本当确定性扣减，所以 se 与费率无关。

    这不是「正确」，是「已知简化」——它让 t 成为上界。测试钉住的是
    「实现与 phasei 一致」，而不是「这个简化可接受」。
    """
    se_by_tier = {t["id"]: t["net_pp_month"] / t["t_iid"] for t in res["tiers"]}
    vals = list(se_by_tier.values())
    assert max(vals) - min(vals) == pytest.approx(0.0, abs=1e-12)


@needs_artifacts
def test_t_ratio_equals_se_ratio(res: dict) -> None:
    want = res["se_iid_pp"] / res["se_nw3_pp"]
    for t in res["tiers"]:
        assert t["t_nw3"] / t["t_iid"] == pytest.approx(want, rel=1e-12)


@needs_artifacts
def test_nw3_se_matches_phasei_summary(res: dict) -> None:
    """重算的 NW(3) 标准误必须等于 phasei 自己报的 0.036086。"""
    assert res["se_nw3_pp"] == pytest.approx(res["summary_se_nw_pp"], rel=1e-3)


# ==================== 4 复现与「判据不可满足」
@needs_artifacts
def test_existing_tiers_reproduce_within_storage_floor(res: dict) -> None:
    """既有两档必须落在「产物 6 位小数」的舍入范围内。

    注意这里用 `storage_floor` 而不是 1e-6：预登记的 1e-6 **不可满足**，
    见下一条测试。这条只要求「差异能被四舍五入解释」。
    """
    for r in res["reproduction"]:
        floor = ct.storage_floor(r["net_ref"])
        assert r["rel_diff"] <= floor * 1.5, f"{r['tier']}.{r['field']} 超出存储下限"


@needs_artifacts
def test_g1_tolerance_is_provably_unsatisfiable(res: dict) -> None:
    """预登记 G1 的 1e-6 不可满足 —— 这是缺陷 D1，必须可被独立复算。

    证明：产物 net 存 6 位小数 ⇒ 半个末位 5e-7 ⇒ 对 net≈0.1257 就是 4e-6 的相对误差。
    所以「相对偏差 ≤ 1e-6」在任何正确实现上都达不到。
    """
    assert res["g1_floor"] > 1e-6, "若下限不超过 1e-6，那 D1 不成立，报告要改"
    # 实测偏差必须落在**该行**下限内。注意不能统一用 g1_floor（那是全表最小值），
    # 0.3% 档的 net 更小，它的存储下限本来就是 0.1% 档的 2.5 倍。
    for r in res["reproduction"]:
        assert r["rel_diff"] <= ct.storage_floor(r["net_ref"]) * 1.5, (
            f"{r['tier']}.{r['field']} 偏差 {r['rel_diff']:.3e} 超出该档下限"
        )


@needs_artifacts
def test_cost_reproduces_at_machine_precision(res: dict) -> None:
    """`cost` 必须机器精度复现——它只依赖换手与费率，与均值无关。

    这一条同时证明「成本公式我抄对了」：若公式写成年度成本或双边印花，
    这里会差 12 倍或 1.5 倍，不会停在 1e-16。

    `net` 不在此列：它 = 均值 − cost，而均值来自逐月文件、产物存的是 6 位小数，
    所以 net 天然带存储级偏差（≈4e-6）。要求 net 也到 1e-16 就只能去抄产物的均值，
    那是循环论证——见 `test_mean_comes_from_monthly_not_summary`。
    """
    for r in res["reproduction"]:
        if r["field"] == "cost_pp_month":
            assert r["rel_diff"] < 1e-12, f"{r['tier']}.{r['field']} 偏差 {r['rel_diff']:.3e}"


@needs_artifacts
def test_mean_comes_from_monthly_not_summary(res: dict) -> None:
    """均值必须来自逐月数据，不是 summary 的 `mean_pp`。

    若取 summary 的均值：「复现产物」就变成「用产物的数算产物」，G1 的一致性没有信息量。
    这条钉住的是方法，不是数值——两者只差 5e-7，肉眼看不出来。
    """
    assert res["mean_gross_pp"] != res["summary_mean_pp"]
    assert res["mean_gross_pp"] == pytest.approx(res["summary_mean_pp"], abs=1e-6)
    assert "逐月" in res["mean_source"]


# ==================== 5 最低佣金
def test_min_commission_threshold_is_20000_yuan() -> None:
    assert ct.MIN_COMMISSION / ct.COMM_RATE == pytest.approx(20_000.0, rel=1e-12)


def test_min_commission_only_bites_below_threshold() -> None:
    """高于 2 万元时实际费率等于名义费率；低于时严格更高。"""
    assert ct.effective_rate_with_min_commission(20_000.0) == pytest.approx(
        ct.COMM_RATE, rel=1e-12
    )
    assert ct.effective_rate_with_min_commission(50_000.0) == pytest.approx(
        ct.COMM_RATE, rel=1e-12
    )
    assert ct.effective_rate_with_min_commission(5_000.0) > ct.COMM_RATE * 3


def test_min_commission_rejects_non_positive_notional() -> None:
    with pytest.raises(ct.CostTierError):
        ct.effective_rate_with_min_commission(0.0)


@needs_artifacts
def test_equivalent_rate_falls_back_at_large_capital(res: dict) -> None:
    """1 亿档的单笔金额超过 2 万 ⇒ 等效费率必须回落到 0.051%（闸门 G8 的内容）。"""
    big = next(m for m in res["min_commission"] if m["tier"] == "T_base_1e8")
    assert big["notional_per_stock"] >= ct.MIN_COMMISSION / ct.COMM_RATE
    assert big["equivalent_single_side"] == pytest.approx(
        ct.equivalent_single_side(), abs=1e-9
    )


@needs_artifacts
def test_smaller_capital_has_strictly_higher_effective_rate(res: dict) -> None:
    """资金越小等效费率越高——单调，且 1000 万档必须是 1 亿档的 2 倍以上。"""
    by = {m["tier"]: m["equivalent_single_side"] for m in res["min_commission"]}
    assert by["T_base_1e7"] > by["T_base_3e7"] > by["T_base_1e8"]
    assert by["T_base_1e7"] / by["T_base_1e8"] > 2.0


# ==================== 6 判定不得被改
@needs_artifacts
def test_phasei_verdict_untouched(res: dict) -> None:
    assert res["phasei_verdict"] == "EXQ5_COST_ABSORBED"
    assert res["phasei_criteria"]["C_cost"] is False
    assert res["phasei_primary_cost"] == pytest.approx(0.003, rel=1e-12)


@needs_artifacts
def test_pressure_tier_stays_insignificant_under_both_se(res: dict) -> None:
    """0.3% 档在 iid 与 NW(3) 下都不显著——换口径不能把它变成显著。"""
    t = next(x for x in res["tiers"] if x["id"] == "T_0p3")
    assert t["t_iid"] < 1.96
    assert t["t_nw3"] < 1.96


@needs_artifacts
def test_status_is_not_blocked_despite_g1_noise(res: dict) -> None:
    """G1 的噪声属于判据缺陷（D1），不该把阶段判成阻塞。

    这一条防的是「一看到 FAIL 就报阻塞」——那会把一个正确结果废掉。
    真正该阻塞的是 G1b（超出存储下限）、G5、G7。
    """
    codes = {g["code"] for g in res["gates"]}
    assert "G1B_BEYOND_STORAGE_FLOOR" not in codes
    assert res["status"] == "COST_TIERS_COMPLETED_WITH_DEFECTS"


# ==================== 7 拒跑
@needs_artifacts
def test_prereg_hash_is_enforced(tmp_path: Path) -> None:
    """预登记被改一个字节就必须拒跑，而不是「大致相符就行」。"""
    bad = tmp_path / "docs" / "gates"
    bad.mkdir(parents=True)
    text = PREREG.read_text(encoding="utf-8").replace("0.003", "0.004", 1)
    (bad / "l3ae_prereg.json").write_text(text, encoding="utf-8")
    with pytest.raises(ct.CostTierError, match="预登记被改动"):
        ct.load_prereg(tmp_path)


@needs_artifacts
def test_frozen_input_hash_is_enforced(tmp_path: Path) -> None:
    """冻结输入被改动也必须拒跑。"""
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    (tmp_path / "docs" / "gates").mkdir(parents=True)
    (tmp_path / "docs" / "gates" / "l3ae_prereg.json").write_bytes(PREREG.read_bytes())
    # 只造一个内容不符的文件
    target = tmp_path / prereg["inputs_frozen"]["phasei_summary"]["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(ct.CostTierError, match="冻结输入被改动"):
        ct.load_inputs(tmp_path, prereg)


@needs_artifacts
def test_missing_column_raises_instead_of_filling(tmp_path: Path) -> None:
    """缺列必须报错，不许用 0 或 NaN 静默补全。"""
    f = tmp_path / "m.csv"
    f.write_text("signal_date,other\n2020-01-31,1.0\n", encoding="utf-8")
    with pytest.raises(ct.CostTierError, match="不存在"):
        ct.monthly_values(f, "alpha_v1_pp")


@needs_artifacts
def test_empty_cell_raises_instead_of_filling(tmp_path: Path) -> None:
    f = tmp_path / "m.csv"
    f.write_text("signal_date,alpha_v1_pp\n2020-01-31,\n", encoding="utf-8")
    with pytest.raises(ct.CostTierError, match="缺失"):
        ct.monthly_values(f, "alpha_v1_pp")


# ==================== 8 表述
@needs_artifacts
def test_sheet_forbids_overclaiming() -> None:
    """清单不得出现「成本吸收已被推翻」「可实盘」这类表述。"""
    text = SHEET.read_text(encoding="utf-8")
    assert "成本吸收已被推翻" in text  # 只允许出现在「不能写」表里
    # 必须以否定形式出现，不能作为结论
    assert "「成本吸收已被推翻」 |" in text
    assert "可实盘" in text
    assert "「可实盘」 |" in text


@needs_artifacts
def test_sheet_always_pairs_rate_with_capital() -> None:
    """凡是出现 0.051% 的地方，同一行必须带资金档或等价说明。"""
    text = SHEET.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "0.051%" in line and line.strip().startswith("|"):
            assert ("1 亿" in line) or ("资金档" in line) or ("费率" in line), line


@needs_artifacts
def test_sheet_reports_both_t_conventions() -> None:
    """清单必须同时给 iid 与 NW(3)，不许只挑大的那个。"""
    text = SHEET.read_text(encoding="utf-8")
    assert "iid" in text
    assert "NW(3)" in text or "NW3" in text
    assert "t 被高估" in text or "上界" in text


@needs_artifacts
def test_report_states_sample_is_in_sample() -> None:
    text = REPORT.read_text(encoding="utf-8")
    assert "样本内" in text
    assert "前瞻" in text


# ==================== 9 数值合理性（独立手算）
@needs_artifacts
def test_base_tier_matches_hand_calculation(res: dict) -> None:
    """0.051% 档独立手算一遍，防止实现内部自洽但整体偏移。"""
    to = 0.18862
    mean = 0.16341053586497892
    cost = 2 * to * 0.00051 * 100
    net = mean - cost
    t = net / 0.048414
    got = next(x for x in res["tiers"] if x["id"] == "T_base_1e8")
    assert got["cost_pp_month"] == pytest.approx(cost, rel=1e-6)
    assert got["net_pp_month"] == pytest.approx(net, rel=1e-6)
    assert got["t_iid"] == pytest.approx(t, rel=1e-4)
