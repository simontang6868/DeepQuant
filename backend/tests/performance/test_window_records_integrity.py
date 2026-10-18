"""A-3：W1 结果的读回层完整性测试（跨实验 + 可追溯 + 篡改检测）。

与 ``test_window_records.py``（B-1 的 23 条）的分工：
- B-1 测的是「写对」「读对」「配对不错位」——单文件、单次实验；
- 本文件测的是「汇总层的可信度」——多实验一起读、参数可追溯、
  以及**内容被改动后必须被发现**。后者是 B-1 没有的能力：
  ``read_records`` 只校验表头，改一个数字读回来毫无察觉。

这些测试的共同前提：结果文件会跨会话、跨人传递，所以「这份 CSV 就是当初跑出来的
那份」必须能被证明，而不能靠「看起来很合理」。
"""

from __future__ import annotations

import csv
import json
from dataclasses import fields
from pathlib import Path

import pytest

from deepquant.performance.window_records import (
    CSV_NAME,
    SCHEMA_VERSION,
    STATUS_OK,
    STATUS_SELECTION_FAILED,
    WindowRecord,
    write_records,
)
from deepquant.performance import window_records_integrity as integ


# ===================================================================== #
# 构造工具
# ===================================================================== #


def make_record(**overrides) -> WindowRecord:
    """以「全字段 None」为底，覆盖关键字段。

    用 ``fields()`` 遍历而不是手写 60 个参数：schema 增字段时这里自动跟上，
    不会因为漏填一个字段就让测试在某次 schema 变更后静默失效。
    """
    base = {f.name: None for f in fields(WindowRecord)}
    base.update(
        schema_version=SCHEMA_VERSION,
        experiment_id="expA",
        window_key="expA|600000|ma_cross|broker_reference_common_2024|w0000",
        design_version="oos75-v3",
        symbol="600000",
        strategy_name="ma_cross",
        strategy_version="1.0.0",
        params_json='{"fast": 3, "slow": 10}',
        dataset_digest="d" * 64,
        dataset_rows=1900,
        dataset_start="2017-01-03",
        dataset_end="2024-12-31",
        universe_id="csi300_50",
        universe_size=50,
        window_index=0,
        train_start="2023-01-03",
        train_end="2023-12-29",
        oos_start="2024-01-02",
        oos_end="2024-06-28",
        train_periods=150,
        oos_periods=75,
        warmup_days=0,
        initial_cash=100_000.0,
        fee_source="broker_reference_common_2024",
        commission_rate=0.00025,
        min_commission=5.0,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_buy=0.001,
        slippage_sell=0.001,
        price_limit_mode="board",
        price_limit_rate=0.10,
        status=STATUS_OK,
        selection_success=True,
        oos_success=True,
        oos_is_complete=True,
        strategy_final_equity=110_000.0,
        strategy_cumulative_return=0.10,
        trade_count=3,
        n_fills=2,
        fills_json="[]",
        n_orders=2,
        orders_json="[]",
    )
    base.update(overrides)
    return WindowRecord(**base)


def make_series(exp_id: str = "expA", n: int = 4, **overrides) -> list[WindowRecord]:
    """一个实验的 n 个窗口（window_index / window_key 必须唯一）。"""
    out = []
    for i in range(n):
        rec = make_record(
            experiment_id=exp_id,
            window_index=i,
            window_key=f"{exp_id}|600000|ma_cross|broker_reference_common_2024|w{i:04d}",
            **overrides,
        )
        out.append(rec)
    return out


def write_experiment(records: list[WindowRecord], root: Path) -> Path:
    csv_path = write_records(records, root)
    integ.write_manifest(records, root)
    return csv_path.parent


def _tamper_cell(csv_path: Path, row_index: int, column: str, new_value: str) -> None:
    """改 CSV 里指定行、指定列的值，其余原样保留。"""
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)
    rows[row_index][column] = new_value
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _drop_row(csv_path: Path, row_index: int) -> None:
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)
    rows.pop(row_index)
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _failed_names(verdict: integ.ManifestVerdict) -> set[str]:
    return {c.name for c in verdict.failures}


# ===================================================================== #
# 一、正常路径：写 → 读 → 校验
# ===================================================================== #


def test_clean_experiment_passes_all_checks(tmp_path):
    records = make_series(n=3)
    exp_dir = write_experiment(records, tmp_path)

    verdict = integ.verify_experiment(exp_dir)
    assert verdict.ok, verdict.explain()
    assert verdict.verified is True, "有 manifest 时必须真的做了内容校验"
    assert not verdict.failures
    # 关键检查项一个都不能少（少了等于漏检）
    names = {c.name for c in verdict.checks}
    assert {"csv_sha256", "n_rows", "non_null_counts", "status_counts", "window_keys"} <= names


def test_manifest_is_byte_identical_across_runs(tmp_path):
    """可复现性的最低要求：同一批记录 → 同一份 manifest。"""
    records = make_series(n=3)
    write_records(records, tmp_path)
    p1 = integ.write_manifest(records, tmp_path)
    first = p1.read_bytes()
    p2 = integ.write_manifest(records, tmp_path)   # 幂等，不覆盖
    assert p2.read_bytes() == first

    # 换一个目录重新生成，内容必须一致（说明没有时间戳/路径混进去）
    other = tmp_path / "again"
    write_records(records, other)
    p3 = integ.write_manifest(records, other)
    assert p3.read_bytes() == first


# ===================================================================== #
# 二、篡改必须被发现（B-1 不具备的能力）
# ===================================================================== #


def test_changed_number_is_detected(tmp_path):
    """把某窗的收益从 0.10 改成 0.90——文件级指纹必须立刻报警。"""
    records = make_series(n=3)
    exp_dir = write_experiment(records, tmp_path)

    _tamper_cell(exp_dir / CSV_NAME, row_index=1, column="strategy_cumulative_return", new_value="0.9")

    verdict = integ.verify_experiment(exp_dir)
    assert not verdict.ok
    assert "csv_sha256" in _failed_names(verdict)


def test_deleted_window_row_is_detected(tmp_path):
    """少一行失败窗口 = 分母悄悄变小 = 正收益率虚高。必须报错。"""
    records = make_series(n=3)
    exp_dir = write_experiment(records, tmp_path)

    _drop_row(exp_dir / CSV_NAME, row_index=2)

    verdict = integ.verify_experiment(exp_dir)
    assert not verdict.ok
    assert {"n_rows", "window_keys"} & _failed_names(verdict)
    # 行数不符要能说出具体数字，不能只说「不一致」
    detail = " ".join(c.detail for c in verdict.failures)
    assert "3" in detail and "2" in detail


def test_emptied_param_column_is_detected(tmp_path):
    """参数列被整列清空——最隐蔽的事故：文件能读、行数没变，但已无法追溯参数。"""
    records = make_series(n=3)
    exp_dir = write_experiment(records, tmp_path)

    csv_path = exp_dir / CSV_NAME
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["params_json"] = ""
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    verdict = integ.verify_experiment(exp_dir)
    assert not verdict.ok
    assert "non_null_counts" in _failed_names(verdict)
    detail = " ".join(c.detail for c in verdict.failures)
    assert "params_json" in detail, "必须指出是哪一列出了问题，而不是只报计数不符"


def test_status_flip_is_detected_even_when_row_count_unchanged(tmp_path):
    """把 selection_failed 改成 ok——行数、参数都没变，只有状态分布变了。"""
    records = [
        make_record(window_index=0, window_key="expA|...|w0000"),
        make_record(
            window_index=1, window_key="expA|...|w0001",
            status=STATUS_SELECTION_FAILED, selection_success=False,
            oos_success=None, oos_is_complete=None,
            strategy_final_equity=None, strategy_cumulative_return=None,
            params_json=None, trade_count=None, n_fills=0, n_orders=0,
        ),
    ]
    exp_dir = write_experiment(records, tmp_path)
    assert integ.verify_experiment(exp_dir).ok

    _tamper_cell(exp_dir / CSV_NAME, row_index=1, column="status", new_value=STATUS_OK)

    verdict = integ.verify_experiment(exp_dir)
    assert not verdict.ok
    assert "status_counts" in _failed_names(verdict)


def test_swapped_experiment_id_is_detected(tmp_path):
    """把整份 CSV 换成另一个实验的——目录名与内容不再对应。"""
    records = make_series("expA", n=2)
    exp_dir = write_experiment(records, tmp_path)

    csv_path = exp_dir / CSV_NAME
    text = csv_path.read_text(encoding="utf-8")
    csv_path.write_text(text.replace("expA", "expB"), encoding="utf-8")

    verdict = integ.verify_experiment(exp_dir)
    assert not verdict.ok
    assert {"single_experiment", "dir_matches_experiment_id", "csv_sha256"} & _failed_names(verdict)


# ===================================================================== #
# 三、manifest 缺失 / 被伪造
# ===================================================================== #


def test_missing_manifest_fails_strict_mode_but_readable_in_permissive(tmp_path):
    records = make_series(n=2)
    exp_dir = write_experiment(records, tmp_path)
    (exp_dir / integ.MANIFEST_NAME).unlink()

    strict = integ.verify_experiment(exp_dir, require_manifest=True)
    assert not strict.ok
    assert "manifest_exists" in _failed_names(strict)

    loose = integ.verify_experiment(exp_dir, require_manifest=False)
    assert loose.ok, "放宽模式仍应做结构校验"
    assert loose.verified is False, "没有 manifest 就不能声称做过内容校验"


def test_forged_manifest_cannot_hide_a_changed_row(tmp_path):
    """同时改 CSV 和 manifest 的 csv_sha256：行数/计数/窗口键仍会露馅。

    这测的是「只重算 sha256 不够」——manifest 里那些可从数据重算的量，
    才是让人真正无法蒙混的部分。
    """
    records = make_series(n=3)
    exp_dir = write_experiment(records, tmp_path)

    _drop_row(exp_dir / CSV_NAME, row_index=0)
    mpath = exp_dir / integ.MANIFEST_NAME
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    manifest["csv_sha256"] = integ._csv_digest(exp_dir / CSV_NAME)
    manifest["csv_bytes"] = (exp_dir / CSV_NAME).stat().st_size
    mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                     encoding="utf-8")

    verdict = integ.verify_experiment(exp_dir)
    assert not verdict.ok, "改完指纹就想蒙混过关，说明校验设计不够"
    assert {"n_rows", "window_keys"} & _failed_names(verdict)


def test_write_manifest_rejects_records_that_do_not_match_csv(tmp_path):
    """内存记录与磁盘 CSV 不是同一批 → 拒绝生成指纹。

    否则会产出「描述 A、实际配 B」的假证据，比没有 manifest 更危险。
    """
    records = make_series(n=2)
    write_records(records, tmp_path)
    integ.write_manifest(records, tmp_path)

    changed = make_series(n=2)   # 第 0 条改了收益，但磁盘 CSV 仍是原内容
    changed[0] = make_record(
        window_index=0,
        window_key="expA|600000|ma_cross|broker_reference_common_2024|w0000",
        strategy_cumulative_return=0.55,
    )
    with pytest.raises(ValueError, match="不一致"):
        integ.write_manifest(changed, tmp_path)


def test_write_manifest_refuses_to_overwrite_after_csv_changed(tmp_path):
    """CSV 被改动后，用「读回来的新内容」生成指纹也必须拒覆盖原 manifest。"""
    from deepquant.performance.window_records import read_records

    records = make_series(n=2)
    exp_dir = write_experiment(records, tmp_path)

    _tamper_cell(exp_dir / CSV_NAME, 0, "strategy_cumulative_return", "0.99")
    new_records = read_records(exp_dir / CSV_NAME)   # 重新读出，与 CSV 自洽

    with pytest.raises(RuntimeError, match="拒绝覆盖"):
        integ.write_manifest(new_records, tmp_path)


# ===================================================================== #
# 四、跨实验加载
# ===================================================================== #


def test_load_experiments_reads_all_and_keeps_failed_windows(tmp_path):
    a = make_series("expA", n=3)
    b = make_series("expB", n=3)
    b[0] = make_record(
        experiment_id="expB", window_index=0,
        window_key="expB|600000|ma_cross|broker_reference_common_2024|w0000",
        status=STATUS_SELECTION_FAILED, selection_success=False,
        oos_success=None, oos_is_complete=None,
        strategy_final_equity=None, strategy_cumulative_return=None,
        params_json=None,
    )
    write_experiment(a, tmp_path)
    write_experiment(b, tmp_path)

    loaded = integ.load_experiments(tmp_path)
    assert set(loaded) == {"expA", "expB"}
    assert len(loaded["expA"]) == 3
    assert len(loaded["expB"]) == 3, "失败窗口必须照样读回来，不能被过滤掉"
    statuses = sorted(r.status for r in loaded["expB"])
    assert STATUS_SELECTION_FAILED in statuses


def test_load_experiments_fails_whole_batch_on_one_bad_dir(tmp_path):
    """不做「跳过坏的继续汇总」——那会把「有一份不可信」从报告里抹掉。"""
    write_experiment(make_series("expA", n=2), tmp_path)
    write_experiment(make_series("expB", n=2), tmp_path)
    _tamper_cell(tmp_path / "expB" / CSV_NAME, 0, "strategy_cumulative_return", "0.99")

    with pytest.raises(integ.IntegrityError) as exc:
        integ.load_experiments(tmp_path)
    assert "expB" in str(exc.value)


def test_load_experiments_rejects_duplicate_experiment_id(tmp_path):
    """两个目录声称同一实验 → 来源不明，不能任选一份。"""
    write_experiment(make_series("expA", n=2), tmp_path)
    dup_dir = tmp_path / "expA_copy"
    dup_dir.mkdir()
    src = tmp_path / "expA"
    (dup_dir / CSV_NAME).write_bytes((src / CSV_NAME).read_bytes())
    (dup_dir / integ.MANIFEST_NAME).write_bytes((src / integ.MANIFEST_NAME).read_bytes())

    with pytest.raises(integ.IntegrityError) as exc:
        integ.load_experiments(tmp_path)
    msg = str(exc.value)
    assert "expA" in msg
    # 副本目录名与 experiment_id 不符，先被目录校验拦住也是合规的
    assert ("同时出现在两个目录" in msg) or ("目录名" in msg)


def test_load_experiment_raises_on_tampered_file(tmp_path):
    records = make_series(n=2)
    exp_dir = write_experiment(records, tmp_path)
    _tamper_cell(exp_dir / CSV_NAME, 0, "strategy_cumulative_return", "0.99")

    with pytest.raises(integ.IntegrityError):
        integ.load_experiment(exp_dir)


def test_load_experiments_requires_directory_with_data(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="没有任何含"):
        integ.load_experiments(empty)


# ===================================================================== #
# 五、分组汇总：分母守恒 + 失败窗口留在组内
# ===================================================================== #


def test_group_records_conserves_all_rows():
    records = (
        make_series("expA", n=3)
        + make_series("expB", n=2)
    )
    groups = integ.group_records(records, ["experiment_id"])
    assert sum(len(v) for v in groups.values()) == len(records)


def test_group_by_symbol_and_strategy_keeps_failure_rows():
    a = make_series("expA", n=2)
    b = [
        make_record(
            experiment_id="expB", symbol="000001", strategy_name="mean_reversion",
            window_index=i, window_key=f"expB|000001|mean_reversion|ref|w{i:04d}",
            status=STATUS_SELECTION_FAILED, selection_success=False,
            oos_success=None, oos_is_complete=None,
            strategy_final_equity=None, strategy_cumulative_return=None,
            params_json=None,
        )
        for i in range(3)
    ]
    records = a + b
    rows = integ.summarize_by(records, ["symbol", "strategy_name"])

    assert len(rows) == 2
    by_key = {tuple(sorted(r["group"].items())): r for r in rows}
    mr = next(r for r in rows if r["group"]["strategy_name"] == "mean_reversion")
    assert mr["n_windows"] == 3
    assert mr["status_counts"][STATUS_SELECTION_FAILED] == 3
    assert sum(r["n_windows"] for r in rows) == len(records)
    assert len(by_key) == 2


def test_summarize_by_rejects_unknown_field():
    with pytest.raises(ValueError, match="未知分组字段"):
        integ.summarize_by(make_series(n=1), ["not_a_column"])


def test_group_records_rejects_empty_key():
    with pytest.raises(ValueError, match="key_fields"):
        integ.group_records(make_series(n=1), [])


# ===================================================================== #
# 六、数据版本与参数可追溯
# ===================================================================== #


def test_dataset_digest_params_and_design_version_survive_roundtrip(tmp_path):
    """数据集指纹 / 参数 / 口径版本必须能从 CSV 原样还原并比对。"""
    records = make_series(n=2)
    exp_dir = write_experiment(records, tmp_path)

    loaded = integ.load_experiment(exp_dir)
    for orig, back in zip(records, loaded):
        assert back.dataset_digest == orig.dataset_digest
        assert back.dataset_rows == orig.dataset_rows
        assert back.dataset_start == orig.dataset_start
        assert back.dataset_end == orig.dataset_end
        assert back.design_version == orig.design_version
        assert back.params_json == orig.params_json
        assert back.universe_id == orig.universe_id
        assert back.universe_size == orig.universe_size
        assert back.fee_source == orig.fee_source

    # 参数能被真正解析成结构（不是只剩一串文本）
    assert json.loads(loaded[0].params_json) == {"fast": 3, "slow": 10}


def test_different_params_yield_different_experiment_identity():
    """同标的、同策略、参数不同 → 必须是两个实验，汇总时不能相加。"""
    base = dict(design_version="oos75-v3", symbol="600000", strategy_name="ma_cross",
                fee_source="broker_reference_common_2024", price_limit_mode="board",
                dataset_digest="d" * 64, request_range=("2017-01-01", "2024-12-31"))
    from deepquant.performance.window_records import make_experiment_id

    id1 = make_experiment_id(params_grid={"fast": [3], "slow": [10]}, **base)
    id2 = make_experiment_id(params_grid={"fast": [5], "slow": [10]}, **base)
    id3 = make_experiment_id(params_grid={"fast": [3], "slow": [10]}, **base)
    assert id1 != id2
    assert id1 == id3, "同配置必须稳定，否则重跑会落到新目录、破坏幂等"


def test_manifest_records_traceability_fields(tmp_path):
    """manifest 必须留下「这份结果基于哪份数据、哪个口径」的痕迹。"""
    records = make_series(n=2)
    exp_dir = write_experiment(records, tmp_path)
    manifest = json.loads((exp_dir / integ.MANIFEST_NAME).read_text(encoding="utf-8"))

    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["experiment_id"] == "expA"
    assert manifest["n_rows"] == 2
    assert manifest["non_null_counts"]["dataset_digest"] == 2
    assert manifest["non_null_counts"]["params_json"] == 2
    assert manifest["columns"] == integ.record_columns()
