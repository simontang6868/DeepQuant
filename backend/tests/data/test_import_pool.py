"""``scripts/import_pool.py`` 的契约测试。

脚本出错的样子通常不是报错，而是**看起来成功了**：
- 路径写错 → 一个都没导入，退出码 0；
- 指纹不同也照导 → 界面上的数据与研究文档对不上，看不出差异；
- 已有数据集被覆盖 → 用户手上正在跑的标的在背后换成了另一版行情；
- 重复跑 → mtime 全变，无法判断「到底有没有新导入」。

所以这里逐条钉住：不猜路径、先验指纹、幂等、不覆盖、留 manifest。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "import_pool.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("import_pool_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pool = _load_script()


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def _csv_text(start_price: float = 10.0, rows: int = 30, *, negative: bool = False) -> str:
    lines = ["date,open,high,low,close,volume"]
    for i in range(rows):
        close = start_price + i * 0.1
        open_ = close - 0.05
        if negative and i == 3:
            close = -1.0  # 复权缺陷的真实形态：整段减法复权后出现负价
        lines.append(
            f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d},{open_:.2f},{close + 0.1:.2f},"
            f"{min(open_, close) - 0.1:.2f},{close:.2f},{1000 + i}"
        )
    return "\n".join(lines) + "\n"


def _write_pool(tmp_path: Path, spec: dict[str, dict]) -> Path:
    """按 {code: {name, negative, sha}} 生成池目录与清单。"""
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    stocks = []
    for code, info in spec.items():
        path = pool_dir / f"{code}.qfq.csv"
        path.write_text(_csv_text(negative=info.get("negative", False)), encoding="utf-8")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        stocks.append(
            {
                "code": code,
                "name": info.get("name", ""),
                "sha256": info.get("sha", sha),
            }
        )
    summary = {"stocks": stocks, "skipped": []}
    (pool_dir / "step75_research_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )
    return pool_dir


def _meta(data_root: Path, symbol: str) -> dict:
    return json.loads((data_root / "metadata" / f"{symbol}.meta.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 1. 不写盘 / 不猜路径
# --------------------------------------------------------------------------- #


def test_dry_run_writes_nothing(tmp_path) -> None:
    pool_dir = _write_pool(tmp_path, {"600000.SH": {"name": "浦发银行"}})
    data_root = tmp_path / "data"

    result = pool.import_pool(pool_dir, data_root, dry_run=True)

    assert result.counts == {"would_import": 1}
    assert not data_root.exists()


def test_missing_pool_dir_is_usage_error(tmp_path, capsys) -> None:
    code = pool.main(["--pool-dir", str(tmp_path / "nope"), "--data-root", str(tmp_path / "d")])
    assert code == pool.EXIT_USAGE
    assert "池目录不存在" in capsys.readouterr().err


def test_missing_summary_is_usage_error(tmp_path, capsys) -> None:
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    code = pool.main(["--pool-dir", str(pool_dir), "--data-root", str(tmp_path / "d")])
    assert code == pool.EXIT_USAGE
    assert "池清单不存在" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# 2. 正常导入 + manifest
# --------------------------------------------------------------------------- #


def test_imports_and_writes_manifest(tmp_path) -> None:
    pool_dir = _write_pool(
        tmp_path,
        {"600000.SH": {"name": "浦发银行"}, "000001.SZ": {"name": "平安银行"}},
    )
    data_root = tmp_path / "data"

    result = pool.import_pool(pool_dir, data_root)

    assert result.counts == {"imported": 2}
    assert _meta(data_root, "600000.SH")["instrument"]["display_name"] == "浦发银行"
    # 行情数据本身未改动：只多了元数据字段
    assert (data_root / "processed" / "600000.SH.daily.parquet").is_file()

    code = pool.main(["--pool-dir", str(pool_dir), "--data-root", str(data_root)])
    manifest = json.loads((data_root / "pool_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pool"]["n_listed"] == 2
    assert manifest["gate"]["unchanged"] == 2
    assert {s["code"] for s in manifest["symbols"]} == {"600000.SH", "000001.SZ"}
    assert manifest["disagreements"] == []
    assert code == pool.EXIT_OK


def test_unlisted_files_are_reported_not_imported(tmp_path) -> None:
    pool_dir = _write_pool(tmp_path, {"600000.SH": {"name": "浦发银行"}})
    (pool_dir / "999999.SH.qfq.csv").write_text(_csv_text(), encoding="utf-8")

    result = pool.import_pool(pool_dir, tmp_path / "data")

    assert result.n_unlisted_files == 1
    assert [o.code for o in result.outcomes] == ["600000.SH"]


# --------------------------------------------------------------------------- #
# 3. 指纹与闸门：不「修一修再导入」
# --------------------------------------------------------------------------- #


def test_sha256_mismatch_is_rejected_without_writing(tmp_path) -> None:
    pool_dir = _write_pool(tmp_path, {"600000.SH": {"name": "浦发银行", "sha": "0" * 64}})
    data_root = tmp_path / "data"

    result = pool.import_pool(pool_dir, data_root)

    assert result.counts == {"rejected": 1}
    assert result.outcomes[0].reason.startswith("sha256_mismatch")
    assert not (data_root / "metadata").exists()
    assert not (data_root / "processed").exists()


def test_negative_price_is_rejected_by_gate(tmp_path) -> None:
    pool_dir = _write_pool(tmp_path, {"600809.SH": {"name": "山西汾酒", "negative": True}})
    data_root = tmp_path / "data"

    result = pool.import_pool(pool_dir, data_root)

    assert result.counts == {"rejected": 1}
    # 负价同时破坏了 OHLC 关系，所以原因里会并列两个错误码；
    # 这里只要求「负价」必须出现（闸门识别到了真正的病根）。
    assert result.outcomes[0].reason.startswith("invalid_price(")
    # 原子性：被拒的标的不能在数据仓库里留下任何**文件**。
    # （`ingest_daily_csv` 在校验前就先建了三个目录，空目录会留下，但不含任何数据；
    #   这里断言的是「没有产出物」，不是「没有目录」。）
    for sub in ("raw", "processed", "metadata"):
        directory = data_root / sub
        assert not directory.exists() or not any(directory.iterdir())


def test_gate_disagreement_with_research_is_surfaced(tmp_path) -> None:
    """研究说这只可用、导入闸门却拒了——必须报出来，而不是悄悄少一只。"""
    pool_dir = _write_pool(tmp_path, {"600809.SH": {"name": "山西汾酒", "negative": True}})
    data_root = tmp_path / "data"

    result = pool.import_pool(pool_dir, data_root)

    assert len(result.disagreements) == 1
    disagreement = result.disagreements[0]
    assert disagreement["code"] == "600809.SH"
    assert disagreement["research"] == "usable"
    assert disagreement["import_gate"] == "rejected"
    assert disagreement["reason"].startswith("invalid_price(")
    code = pool.main(["--pool-dir", str(pool_dir), "--data-root", str(data_root)])
    assert code == pool.EXIT_PROBLEM


def test_research_skipped_symbol_that_passes_gate_is_also_a_disagreement(tmp_path) -> None:
    """反向不一致同样要报：研究剔掉的样本，界面却能选，样本范围就说不清了。"""
    pool_dir = _write_pool(tmp_path, {"600000.SH": {"name": "浦发银行"}})
    summary_path = pool_dir / "step75_research_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["skipped"] = [{"code": "600000.SH", "reason": "data_quality_gate"}]
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    result = pool.import_pool(pool_dir, tmp_path / "data")

    assert result.counts == {"imported": 1}
    assert result.disagreements[0]["research"] == "rejected"


# --------------------------------------------------------------------------- #
# 4. 幂等 / 不覆盖 / 只补名称
# --------------------------------------------------------------------------- #


def test_second_run_is_idempotent(tmp_path) -> None:
    pool_dir = _write_pool(tmp_path, {"600000.SH": {"name": "浦发银行"}})
    data_root = tmp_path / "data"
    pool.import_pool(pool_dir, data_root)
    meta_path = data_root / "metadata" / "600000.SH.meta.json"
    before = meta_path.stat().st_mtime_ns

    result = pool.import_pool(pool_dir, data_root)

    assert result.counts == {"unchanged": 1}
    assert meta_path.stat().st_mtime_ns == before


def test_existing_dataset_with_same_data_only_gets_a_name(tmp_path) -> None:
    """手挑数据集（已导入、无名称）不应被重写数据，只回填名称。"""
    pool_dir = _write_pool(tmp_path, {"600900.SH": {"name": "长江电力"}})
    data_root = tmp_path / "data"
    from deepquant.data.pipeline import ingest_daily_csv

    ingest_daily_csv(
        pool_dir / "600900.SH.qfq.csv",
        symbol="600900.SH",
        data_root=data_root,
        source_name="step75/600900.SH.qfq.csv",
    )
    original = _meta(data_root, "600900.SH")
    assert "instrument" not in original

    result = pool.import_pool(pool_dir, data_root)

    assert result.counts == {"enriched": 1}
    updated = _meta(data_root, "600900.SH")
    assert updated["instrument"]["display_name"] == "长江电力"
    # 数据与溯源信息一字不动
    assert updated["source"] == original["source"]
    assert updated["rows"] == original["rows"]


def test_existing_dataset_with_different_data_is_not_overwritten(tmp_path) -> None:
    pool_dir = _write_pool(tmp_path, {"600900.SH": {"name": "长江电力"}})
    data_root = tmp_path / "data"
    from deepquant.data.pipeline import ingest_daily_csv

    other = tmp_path / "other" / "600900.SH.qfq.csv"
    other.parent.mkdir()
    other.write_text(_csv_text(start_price=20.0), encoding="utf-8")
    ingest_daily_csv(other, symbol="600900.SH", data_root=data_root)
    before = _meta(data_root, "600900.SH")["source"]["sha256"]

    result = pool.import_pool(pool_dir, data_root)

    assert result.counts == {"conflict": 1}
    assert "existing_sha256_differs" in (result.outcomes[0].reason or "")
    assert _meta(data_root, "600900.SH")["source"]["sha256"] == before


def test_extra_names_do_not_override_pool_names(tmp_path) -> None:
    """两处名称不一致时以池清单为准：清单是研究结论的出处。"""
    pool_dir = _write_pool(tmp_path, {"600000.SH": {"name": "浦发银行"}})
    data_root = tmp_path / "data"

    result = pool.import_pool(
        pool_dir, data_root, extra_names={"600000.SH": "另一个名字"}
    )

    assert result.outcomes[0].name == "浦发银行"
    assert _meta(data_root, "600000.SH")["instrument"]["display_name"] == "浦发银行"


def test_extra_names_apply_to_registered_dataset_outside_pool(tmp_path) -> None:
    pool_dir = _write_pool(tmp_path, {"600000.SH": {"name": "浦发银行"}})
    data_root = tmp_path / "data"
    from deepquant.data.pipeline import ingest_daily_csv

    outside = tmp_path / "outside" / "600519.SH.qfq.csv"
    outside.parent.mkdir()
    outside.write_text(_csv_text(start_price=200.0), encoding="utf-8")
    ingest_daily_csv(outside, symbol="600519.SH", data_root=data_root)

    result = pool.import_pool(
        pool_dir, data_root, extra_names={"600519.SH": "贵州茅台"}
    )

    statuses = {o.code: o.status for o in result.outcomes}
    assert statuses == {"600000.SH": "imported", "600519.SH": "enriched"}
    assert _meta(data_root, "600519.SH")["instrument"]["display_name"] == "贵州茅台"


def test_extra_names_are_normalized(tmp_path) -> None:
    """名称文件里的首尾空格不该原样落盘。"""
    pool_dir = _write_pool(tmp_path, {"600000.SH": {"name": ""}})
    data_root = tmp_path / "data"

    pool.import_pool(pool_dir, data_root, extra_names={"600000.SH": "  浦发银行 "})

    assert _meta(data_root, "600000.SH")["instrument"]["display_name"] == "浦发银行"


# --------------------------------------------------------------------------- #
# 5. 退出码
# --------------------------------------------------------------------------- #


def test_exit_code_is_nonzero_when_nothing_succeeded(tmp_path) -> None:
    """一只都没导入却返回 0，会让「路径写错」看起来和「全都已完成」一样。"""
    pool_dir = _write_pool(tmp_path, {"600000.SH": {"name": "浦发银行", "sha": "0" * 64}})
    result = pool.import_pool(pool_dir, tmp_path / "data")
    assert pool._exit_code(result) == pool.EXIT_PROBLEM  # noqa: SLF001


def test_exit_code_ok_when_research_also_skipped(tmp_path) -> None:
    """研究线自己也剔掉了这只 → 不是分歧，退出码不能因为「少一只」而变成 1。"""
    pool_dir = _write_pool(
        tmp_path,
        {"600000.SH": {"name": "浦发银行"}, "600809.SH": {"name": "山西汾酒", "negative": True}},
    )
    summary_path = pool_dir / "step75_research_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["skipped"] = [{"code": "600809.SH", "error_codes": ["invalid_price"]}]
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    result = pool.import_pool(pool_dir, tmp_path / "data")

    assert pool._exit_code(result) == pool.EXIT_OK  # noqa: SLF001


def test_summary_with_empty_stocks_is_rejected(tmp_path) -> None:
    """清单是「导入哪些标的」的唯一来源；空清单不能静默变成「零导入、退出码 0」。"""
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    (pool_dir / "step75_research_summary.json").write_text(
        json.dumps({"stocks": []}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        pool.import_pool(pool_dir, tmp_path / "data")
