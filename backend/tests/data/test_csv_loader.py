"""CSV 导入层测试：编码兼容、错误行为与读写往返。"""

import pandas as pd
import pytest

from deepquant.data.loaders.csv_loader import (
    DEFAULT_WRITE_ENCODING,
    DataLoadError,
    load_daily_csv,
)
from deepquant.data.storage import io as storage_io

CLEAN_CSV = (
    "date,open,high,low,close,volume\n"
    "2024-01-02,10.0,10.5,9.8,10.2,1000\n"
    "2024-01-03,10.2,10.6,10.0,10.4,1200\n"
)

# 含中文列名与文本内容的 GBK 行情（Windows 中文 Excel 导出的典型形态）
GBK_CSV = (
    "date,open,high,low,close,volume,名称\n"
    "2024-01-02,10.0,10.5,9.8,10.2,1000,贵州茅台\n"
    "2024-01-03,10.2,10.6,10.0,10.4,1200,贵州茅台\n"
)


def _write(tmp_path, text, name="sample.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_load_normalizes_columns_and_sorts(tmp_path):
    # 列名大小写混乱、行顺序颠倒，应被规范化为小写并按日期升序
    text = (
        "Date,Open,HIGH,low,Close,Volume\n"
        "2024-01-03,10.2,10.6,10.0,10.4,1200\n"
        "2024-01-02,10.0,10.5,9.8,10.2,1000\n"
    )
    df = load_daily_csv(_write(tmp_path, text))
    assert list(df.columns)[:6] == ["date", "open", "high", "low", "close", "volume"]
    assert df["date"].is_monotonic_increasing
    assert df["close"].tolist() == [10.2, 10.4]


def test_load_parses_dates_and_numbers(tmp_path):
    df = load_daily_csv(_write(tmp_path, CLEAN_CSV))
    assert str(df["date"].dtype).startswith("datetime64")
    assert df["volume"].tolist() == [1000, 1200]


def test_load_unparsable_values_become_missing(tmp_path):
    # 非法日期/价格应转为 NaT/NaN 交给校验层，而不是直接抛异常
    text = (
        "date,open,high,low,close,volume\n"
        "not-a-date,10.0,10.5,9.8,10.2,1000\n"
        "2024-01-03,abc,10.6,10.0,10.4,1200\n"
    )
    df = load_daily_csv(_write(tmp_path, text))
    assert int(df["date"].isna().sum()) == 1
    assert int(df["open"].isna().sum()) == 1


def test_load_missing_column_raises(tmp_path):
    text = "date,open,high,low,close\n2024-01-02,10.0,10.5,9.8,10.2\n"
    with pytest.raises(DataLoadError):
        load_daily_csv(_write(tmp_path, text))


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(DataLoadError):
        load_daily_csv(tmp_path / "nope.csv")


# ===================================================================== #
# 编码兼容（Windows 中文乱码修复）
# ===================================================================== #


def test_load_utf8_with_bom_strips_bom(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbf" + CLEAN_CSV.encode("utf-8"))
    df = load_daily_csv(path)
    # BOM 不得污染首个列名
    assert list(df.columns)[:6] == ["date", "open", "high", "low", "close", "volume"]
    assert df["close"].tolist() == [10.2, 10.4]


def test_load_gb18030_chinese_csv(tmp_path):
    path = tmp_path / "gbk.csv"
    path.write_bytes(GBK_CSV.encode("gb18030"))
    df = load_daily_csv(path)  # 默认应回退到 gb18030 解码
    assert df["close"].tolist() == [10.2, 10.4]
    assert "名称" in df.columns  # 中文列名按原样保留
    assert df["名称"].tolist() == ["贵州茅台", "贵州茅台"]


def test_load_explicit_encoding(tmp_path):
    path = tmp_path / "gbk.csv"
    path.write_bytes(GBK_CSV.encode("gb18030"))
    df = load_daily_csv(path, encoding="gb18030")
    assert "名称" in df.columns


def test_load_wrong_explicit_encoding_raises(tmp_path):
    path = tmp_path / "gbk.csv"
    path.write_bytes(GBK_CSV.encode("gb18030"))
    with pytest.raises(DataLoadError, match="无法解码"):
        load_daily_csv(path, encoding="utf-8")


def test_load_unknown_encoding_raises(tmp_path):
    with pytest.raises(DataLoadError, match="未知编码"):
        load_daily_csv(_write(tmp_path, CLEAN_CSV), encoding="not-a-real-encoding")


def test_load_undecodable_file_raises(tmp_path):
    # UTF-16LE BOM 开头：在 utf-8-sig 与 gb18030 下均无法解码
    path = tmp_path / "utf16.csv"
    path.write_bytes(b"\xff\xfe\x00\x00date,open\n")
    with pytest.raises(DataLoadError, match="无法用受支持的编码"):
        load_daily_csv(path)


def test_load_empty_file_raises(tmp_path):
    # 空文件必须明确报错，绝不静默返回空数据
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    with pytest.raises(DataLoadError, match="文件为空"):
        load_daily_csv(path)


def test_load_header_only_returns_empty_frame(tmp_path):
    # 仅表头：返回 0 行的规范表，交由 validation 层判为 empty（不是静默吞掉）
    path = _write(tmp_path, "date,open,high,low,close,volume\n")
    df = load_daily_csv(path)
    assert len(df) == 0
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]


def test_load_accepts_str_path(tmp_path):
    # 跨平台路径：str 与 pathlib.Path 均可（Windows 用户常以字符串传路径）
    path = _write(tmp_path, CLEAN_CSV)
    by_str = load_daily_csv(str(path))
    by_path = load_daily_csv(path)
    assert by_str["close"].tolist() == by_path["close"].tolist() == [10.2, 10.4]


def test_load_preserves_missing_values_as_nan(tmp_path):
    # 缺失数值保持 NaN（不得填 0），交由下游按既有规则处理
    text = "date,open,high,low,close,volume\n2024-01-02,10.0,,9.8,10.2,1000\n"
    df = load_daily_csv(_write(tmp_path, text))
    assert pd.isna(df["high"].iloc[0])
    assert df["open"].iloc[0] == 10.0


# ===================================================================== #
# 写出 / 读回往返
# ===================================================================== #


def test_write_then_read_round_trip(tmp_path):
    source = load_daily_csv(_write(tmp_path, CLEAN_CSV))
    out = tmp_path / "out" / "600000.csv"
    storage_io.write_daily_csv(source, out)

    # 明确写出 UTF-8（不依赖系统默认编码）
    assert DEFAULT_WRITE_ENCODING == "utf-8"
    assert out.read_bytes().decode("utf-8")  # 可被 UTF-8 解码

    reloaded = load_daily_csv(out)
    assert list(reloaded.columns) == list(source.columns)
    assert reloaded["date"].tolist() == source["date"].tolist()
    for col in ("open", "high", "low", "close", "volume"):
        assert reloaded[col].tolist() == source[col].tolist()
    assert str(reloaded["date"].dtype).startswith("datetime64")


def test_write_then_read_round_trip_with_chinese_content(tmp_path):
    # 若原始数据含中文额外列，写出/读回后中文内容应保持一致
    src = tmp_path / "gbk.csv"
    src.write_bytes(GBK_CSV.encode("gb18030"))
    df = load_daily_csv(src)
    out = tmp_path / "chinese.csv"
    storage_io.write_daily_csv(df, out)
    reloaded = load_daily_csv(out)
    assert reloaded["名称"].tolist() == ["贵州茅台", "贵州茅台"]
    assert reloaded["close"].tolist() == df["close"].tolist()


# ===================================================================== #
# Windows 路径兼容（Step 5.5 验收补充）
# ===================================================================== #


def test_read_write_with_space_in_path(tmp_path):
    # Windows 常见带空格目录/文件名（如 D:\My Data\）：读写往返应保持一致
    sp_dir = tmp_path / "my data dir"
    sp_dir.mkdir()
    src = sp_dir / "sample data.csv"
    src.write_text(CLEAN_CSV, encoding="utf-8")
    df = load_daily_csv(src)
    out = sp_dir / "out file.csv"
    storage_io.write_daily_csv(df, out)
    reloaded = load_daily_csv(out)
    assert reloaded["close"].tolist() == df["close"].tolist() == [10.2, 10.4]


def test_read_write_with_chinese_path(tmp_path):
    # 中文目录/文件名（如 D:\量化数据\日线.csv）：读写往返应保持一致
    cn_dir = tmp_path / "量化数据"
    cn_dir.mkdir()
    src = cn_dir / "日线行情.csv"
    src.write_text(CLEAN_CSV, encoding="utf-8")
    df = load_daily_csv(src)
    out = cn_dir / "输出结果.csv"
    storage_io.write_daily_csv(df, out)
    reloaded = load_daily_csv(out)
    assert reloaded["close"].tolist() == df["close"].tolist() == [10.2, 10.4]
