"""股债利差研究：数据装载、清洗、对齐与覆盖率诊断。

设计要点：
- 原始数据与清洗后数据严格分离：本模块只读清洗后的 CSV，原始文件另存于
  ``data/equity_bond_spread/raw/``，并对两者都计算 SHA-256 以便复现。
- 缺失值一律用 NaN 表示，**不做任何插值填充**（不得用未来数据填补过去）。
- 国债收益率按"向前取最近可得值"匹配，且限制最大回溯自然日数，
  超限时置为 NaN 而不是拿很久以前的数据冒充当日收益率。
- 复用既有 ``deepquant.data.validation.checks`` 的 ``ValidationReport`` /
  ``ValidationIssue`` 结构，保持与项目其他数据链路一致的报告形态。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from deepquant.data.validation.checks import ERROR, WARNING, ValidationIssue, ValidationReport

from .config import DataConfig

#: 清洗后面板的标准列名（顺序固定，便于测试与序列化）
PANEL_COLUMNS = ("price", "pe", "pb", "div_yield", "bond_10y")


def sha256_file(path: str | Path) -> str:
    """计算文件 SHA-256（分块读取，避免大文件一次性入内存）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在，无法计算摘要: {p}")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


@dataclass
class CleaningReport:
    """一次清洗过程的完整记录（写入实验日志，保证可追溯）。"""

    source_path: str = ""
    sha256: str = ""
    n_input_rows: int = 0
    n_output_rows: int = 0
    n_bad_date: int = 0
    n_duplicate_date: int = 0
    n_out_of_range: dict[str, int] = field(default_factory=dict)
    date_min: str | None = None
    date_max: str | None = None
    missing_rate: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "sha256": self.sha256,
            "n_input_rows": self.n_input_rows,
            "n_output_rows": self.n_output_rows,
            "n_bad_date": self.n_bad_date,
            "n_duplicate_date": self.n_duplicate_date,
            "n_out_of_range": dict(self.n_out_of_range),
            "date_min": self.date_min,
            "date_max": self.date_max,
            "missing_rate": {k: round(v, 6) for k, v in self.missing_rate.items()},
        }


def load_equity_csv(path: str | Path, cfg: DataConfig | None = None) -> tuple[pd.DataFrame, CleaningReport]:
    """装载沪深300 行情与估值序列。

    期望列：``date, price, pe, pb, div_yield``（``div_yield`` 为百分数）。
    缺失或非法的 PE/PB/股息率一律置 NaN，不做插值。
    """
    cfg = cfg or DataConfig()
    p = Path(path)
    raw = pd.read_csv(p)
    rep = CleaningReport(source_path=str(p), sha256=sha256_file(p), n_input_rows=len(raw))

    if "date" not in raw.columns:
        raise ValueError(f"缺少 date 列：{list(raw.columns)}")

    df = raw.copy()
    df["date"] = _to_datetime(df["date"])
    rep.n_bad_date = int(df["date"].isna().sum())
    df = df.dropna(subset=["date"])

    dup = int(df["date"].duplicated().sum())
    rep.n_duplicate_date = dup
    if dup:
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")

    df = df.set_index("date").sort_index()

    for col in ("price", "pe", "pb", "div_yield"):
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 价格必须为正
    bad_price = int(((df["price"] <= 0) | ~df["price"].notna()).sum() - (~df["price"].notna()).sum())
    df.loc[df["price"] <= 0, "price"] = pd.NA
    rep.n_out_of_range["price_nonpositive"] = max(bad_price, 0)

    # PE：0 / 负值 / 缺失 / 越界 → NaN（不得取倒数当作有效信号）
    pe = df["pe"]
    invalid_pe = (pe <= 0) | (pe < cfg.pe_valid_min) | (pe > cfg.pe_valid_max) | pe.isna()
    rep.n_out_of_range["pe_invalid"] = int(invalid_pe.sum())
    df.loc[invalid_pe, "pe"] = pd.NA

    # PB / 股息率：负值或越界视为异常
    for col, lo, hi in (("pb", 0.0, 50.0), ("div_yield", 0.0, 30.0)):
        s = df[col]
        bad = (s < lo) | (s > hi)
        rep.n_out_of_range[f"{col}_invalid"] = int((bad & s.notna()).sum())
        df.loc[bad | s.isna(), col] = pd.NA
    # pb=0 视为缺失（与行情源缺值编码一致）
    df.loc[df["pb"] == 0, "pb"] = pd.NA

    df = df[list(PANEL_COLUMNS[:4])]
    rep.n_output_rows = len(df)
    if len(df):
        rep.date_min = str(df.index.min().date())
        rep.date_max = str(df.index.max().date())
    rep.missing_rate = {c: float(df[c].isna().mean()) for c in df.columns}
    return df, rep


def load_bond_csv(path: str | Path, cfg: DataConfig | None = None) -> tuple[pd.Series, CleaningReport]:
    """装载中国 10 年期国债收益率序列（百分数）。"""
    cfg = cfg or DataConfig()
    p = Path(path)
    raw = pd.read_csv(p)
    rep = CleaningReport(source_path=str(p), sha256=sha256_file(p), n_input_rows=len(raw))

    df = raw.copy()
    df["date"] = _to_datetime(df["date"])
    rep.n_bad_date = int(df["date"].isna().sum())
    df = df.dropna(subset=["date"])
    dup = int(df["date"].duplicated().sum())
    rep.n_duplicate_date = dup
    if dup:
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    df = df.set_index("date").sort_index()

    ycol = "yield_10y" if "yield_10y" in df.columns else df.columns[0]
    s = pd.to_numeric(df[ycol], errors="coerce")
    bad = (s < cfg.bond_valid_min) | (s > cfg.bond_valid_max)
    rep.n_out_of_range["bond_out_of_range"] = int((bad & s.notna()).sum())
    s = s.mask(bad)
    s.name = "bond_10y"

    rep.n_output_rows = int(s.notna().sum())
    if len(s):
        rep.date_min = str(s.index.min().date())
        rep.date_max = str(s.index.max().date())
    rep.missing_rate = {"bond_10y": float(s.isna().mean())}
    return s, rep


def match_bond(
    equity_index: pd.DatetimeIndex,
    bond: pd.Series,
    cfg: DataConfig | None = None,
) -> tuple[pd.Series, dict]:
    """把国债收益率匹配到股票观测日。

    规则（可配置）：
    - ``exact``：只取同一天的值，取不到即 NaN；
    - ``forward_fill``：取不晚于该观测日的最近一个可得值；若该值与观测日
      相隔超过 ``bond_max_fill_days`` 个自然日，则置 NaN 并记录。

    绝不使用观测日之后的数据（前视偏差）。
    """
    cfg = cfg or DataConfig()
    bond = bond.dropna().sort_index()
    aligned = bond.reindex(bond.index.union(equity_index)).sort_index()
    if cfg.bond_match_rule == "exact":
        out = aligned.reindex(equity_index)
        stats = {"rule": "exact", "n_exact": int(out.notna().sum()),
                 "n_filled": 0, "n_missing": int(out.isna().sum()), "max_fill_days": 0}
        return out.rename("bond_10y"), stats

    filled = aligned.ffill().reindex(equity_index)
    # 计算每个观测日实际使用的国债数据日期
    src_dates = aligned.notna()
    src_index = aligned.index[src_dates]
    pos = pd.Series(range(len(aligned.index)), index=aligned.index)
    obs_pos = pos.reindex(equity_index).astype("Int64")
    use_pos = pd.Series(
        pd.Index(src_index).searchsorted(equity_index, side="right") - 1, index=equity_index
    )
    lag_days = []
    filled_out = []
    n_exact = n_filled = n_missing = 0
    max_fill = 0
    for d in equity_index:
        i = int(use_pos.loc[d]) if pd.notna(use_pos.loc[d]) else -1
        if i < 0 or i >= len(src_index):
            filled_out.append(float("nan"))
            n_missing += 1
            continue
        src_date = src_index[i]
        gap = (d - src_date).days
        val = float(aligned.loc[src_date])
        if gap > cfg.bond_max_fill_days:
            filled_out.append(float("nan"))
            n_missing += 1
            continue
        filled_out.append(val)
        lag_days.append(gap)
        if gap == 0:
            n_exact += 1
        else:
            n_filled += 1
        max_fill = max(max_fill, gap)
    out = pd.Series(filled_out, index=equity_index, name="bond_10y")
    stats = {
        "rule": "forward_fill",
        "n_exact": n_exact,
        "n_filled": n_filled,
        "n_missing": n_missing,
        "max_fill_days": max_fill,
        "mean_fill_days": (sum(lag_days) / len(lag_days)) if lag_days else 0.0,
    }
    return out, stats


@dataclass
class ResearchPanel:
    """研究面板：对齐后的股票 + 估值 + 国债收益率。"""

    frame: pd.DataFrame
    equity_report: CleaningReport
    bond_report: CleaningReport
    bond_match_stats: dict

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.frame.index

    def coverage(self) -> dict:
        """各字段有效覆盖率（非缺失比例）与样本数。"""
        n = len(self.frame)
        if n == 0:
            return {"n_obs": 0}
        out = {"n_obs": int(n)}
        for c in self.frame.columns:
            out[f"coverage_{c}"] = round(float(self.frame[c].notna().mean()), 6)
        out["date_min"] = str(self.index.min().date())
        out["date_max"] = str(self.index.max().date())
        return out

    def to_dict(self) -> dict:
        return {
            "equity": self.equity_report.to_dict(),
            "bond": self.bond_report.to_dict(),
            "bond_match": self.bond_match_stats,
            "coverage": self.coverage(),
        }


def build_panel(
    equity_csv: str | Path,
    bond_csv: str | Path,
    cfg: DataConfig | None = None,
) -> ResearchPanel:
    """装载并组装研究面板。"""
    cfg = cfg or DataConfig()
    eq, eq_rep = load_equity_csv(equity_csv, cfg)
    bd, bd_rep = load_bond_csv(bond_csv, cfg)
    matched, stats = match_bond(eq.index, bd, cfg)
    frame = eq.copy()
    frame["bond_10y"] = matched
    return ResearchPanel(
        frame=frame[list(PANEL_COLUMNS)],
        equity_report=eq_rep,
        bond_report=bd_rep,
        bond_match_stats=stats,
    )


def validate_panel(panel: ResearchPanel, min_obs: int = 1) -> ValidationReport:
    """用既有校验结构产出面板层面的问题清单。"""
    df = panel.frame
    report = ValidationReport(n_rows=int(len(df)))

    if len(df) < min_obs:
        report.issues.append(
            ValidationIssue(ERROR, "insufficient_obs",
                            f"有效观测不足：{len(df)} < 要求 {min_obs}", int(len(df)))
        )
    if len(df) == 0:
        report.issues.append(ValidationIssue(ERROR, "empty", "面板为空（0 行）"))
        return report
    if df.index.has_duplicates:
        report.issues.append(
            ValidationIssue(ERROR, "duplicate_dates", "存在重复日期",
                            int(df.index.duplicated().sum()))
        )
    if not df.index.is_monotonic_increasing:
        report.issues.append(ValidationIssue(WARNING, "unsorted_dates", "日期未按升序排列"))

    for col in PANEL_COLUMNS:
        miss = int(df[col].isna().sum())
        if miss:
            report.issues.append(
                ValidationIssue(WARNING, f"missing_{col}", f"{col} 存在缺失值", miss)
            )
    if df["bond_10y"].isna().all():
        report.issues.append(
            ValidationIssue(ERROR, "missing_bond", "国债收益率全部缺失，无法计算股债利差")
        )
    return report
