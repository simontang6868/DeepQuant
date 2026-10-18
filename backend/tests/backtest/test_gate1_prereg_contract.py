"""预登记文件本身的契约测试。

为什么要有这个文件：`test_gate1_run_contract.py` 里那条
`test_prereg_hash_prefix_matches_frozen_file` 只比对**脚本常量**与
**冻结文件第一行**，它管不住下面两件事：

1. 有人改了 `GATE1_PREREGISTRATION.md` 却没更新 `.sha256`
   → 旧测试仍绿，因为常量与 sha 文件都没变，只有 md 变了。
2. 预登记漏写某个**决定结果**的参数
   → 没有任何测试会红。本文件就是因为 v1.1 漏写「初始资金」而补的。

所以这里测的不是代码，是**协议文本与代码是否同一份**。
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "gate1_run.py"
PREREG = REPO_ROOT / "docs" / "GATE1_PREREGISTRATION.md"
SHA_FILE = REPO_ROOT / "docs" / "GATE1_PREREGISTRATION.sha256"


def _load_script():
    spec = importlib.util.spec_from_file_location("gate1_run_for_prereg_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


g1 = _load_script()


def _prereg_text() -> str:
    if not PREREG.exists():  # pragma: no cover - 仓库内必然存在
        pytest.skip("预登记文件不存在")
    return PREREG.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 1. 冻结哈希：md 与 .sha256 必须真的对得上
# --------------------------------------------------------------------------- #


def test_frozen_hash_matches_document_bytes():
    """`.sha256` 里的哈希必须等于当前 md 的哈希。

    这条会红的场景：改了预登记正文但忘了重新冻结。那意味着「结果报告引用的
    协议」与「磁盘上实际这份协议」不是同一份——旧的前缀比对测试抓不到这种。
    """
    if not SHA_FILE.exists():  # pragma: no cover
        pytest.skip("冻结文件不存在")
    frozen = SHA_FILE.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    assert actual == frozen, (
        "预登记正文与冻结哈希不一致：改完正文必须重新计算并写入 "
        f"docs/GATE1_PREREGISTRATION.sha256\n  磁盘实际 {actual}\n  冻结记录 {frozen}"
    )


def test_frozen_hash_prefix_matches_script_constant():
    """脚本里记的前缀必须与冻结文件一致（双保险，与旧测试不同源）。"""
    frozen = SHA_FILE.read_text(encoding="utf-8").split()[0]
    assert frozen.startswith(g1.PREREG_SHA256)


# --------------------------------------------------------------------------- #
# 2. 版本号：文档表头、脚本常量、报告生成器必须同版本
# --------------------------------------------------------------------------- #


def test_version_string_consistent_everywhere():
    text = _prereg_text()
    assert f"`{g1.PREREG_VERSION}`" in text, (
        f"预登记表头应写 `{g1.PREREG_VERSION}`；版本号不同步会让报告引用错协议"
    )
    report_src = (
        REPO_ROOT / "backend" / "src" / "deepquant" / "performance" / "gate1_report.py"
    ).read_text(encoding="utf-8")
    assert g1.PREREG_VERSION in report_src, "报告生成器没有引用当前预登记版本"


# --------------------------------------------------------------------------- #
# 3. 决定结果的参数必须在协议里有字（v1.1 漏写「初始资金」的直接后果）
# --------------------------------------------------------------------------- #


def test_initial_cash_is_recorded_in_prereg_and_matches_code():
    """本金必须写进预登记，且与代码常量逐位相同。

    本金不是排版细节：10 万元下，1 手 13,517~35,204 元的 `600809.SH` 永远买不进
    （单只预算仅 1 万元），`--exclude-symbols` 的对照跑会退化成主实验的复制。
    这条参数一旦只存在于代码里，「按预登记执行」就是一句空话。
    """
    text = _prereg_text()
    rows = [ln for ln in text.splitlines() if "初始资金" in ln]
    assert rows, "预登记第 5 节必须有一行「初始资金」"

    numbers = re.findall(r"([0-9][0-9,]{4,})", " ".join(rows))
    assert numbers, "「初始资金」行里没有可解析的金额"
    documented = float(numbers[0].replace(",", ""))
    assert documented == pytest.approx(g1.INITIAL_CASH), (
        f"预登记写 {documented:,.0f}，代码是 {g1.INITIAL_CASH:,.0f}——两者必须是同一个数"
    )


def test_unaffordable_names_would_be_blocked_at_historical_capital():
    """10 万元本金下「买不起 1 手」的判定必须成立，用于锁定附录 B 的论证。

    这里不读行情、不跑回测，只用附录 B 里已落盘的 1 手成本区间做算术：
    单只预算 = 本金 / 持仓数。它证明「提高本金」不是偏好，而是让对照跑有意义的前提。
    """
    per_name_at_100k = 100_000 / 10
    per_name_at_1m = g1.INITIAL_CASH / 10
    # 600809.SH 在 OOS（2021-2024）的 1 手成本区间，见附录 B
    lot_cost_min, lot_cost_max = 13_517, 35_204
    assert lot_cost_min > per_name_at_100k, "10 万元下应买不进"
    assert lot_cost_max < per_name_at_1m, "100 万元下应买得进"


# --------------------------------------------------------------------------- #
# 4. §6 与实际实现的对齐（v1.3 附录 C 的落点）
# --------------------------------------------------------------------------- #


def test_price_limit_is_flat_ten_percent_for_every_symbol():
    """组合层接的是**单一** 0.10，不是 board 模式（创业板 20%）。

    §6 表格写的是 board 模式，实现是 flat 10%（附录 C.1）。这条测试把「实现到底是哪个」
    钉住：否则某天有人把 `a_share_price_limit_rate` 接进来，文档说的和跑的又不是一回事。
    """
    from deepquant.market.rules import PRICE_LIMIT_MODE_FLAT, a_share_price_limit_rate

    cfg = g1.make_portfolio_config(["600000.SH", "300059.SZ"], g1.MAIN_FEE)
    limit = cfg.fee_config.market_rules.price_limit
    assert limit is not None, "涨跌停必须启用；若改为 None，§6 需要整段重写"
    assert limit.rate == pytest.approx(0.10)
    assert limit.rate == pytest.approx(g1.PORTFOLIO_PRICE_LIMIT_RATE)
    # 与 board 模式不同：board 模式对创业板会给 0.20，证明两者不是同一个东西
    assert a_share_price_limit_rate("300059.SZ", mode=PRICE_LIMIT_MODE_FLAT) == pytest.approx(0.10)


def test_run_script_never_calls_board_mode_resolver():
    """board 模式的解析函数不得在运行脚本里被调用（v1.3 前它是死 import）。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "a_share_price_limit_rate(" not in src
    assert "a_share_price_limit_rate" not in src, (
        "只 import 不调用会让「按 board 模式实现」这句话看起来有代码支撑"
    )


def test_prereg_records_flat_10pct_deviation_in_appendix_c():
    """§6 的偏差声明必须有一条文本纠偏（附录 C）。

    这条防的是「文档说了偏差方向、代码走了反方向」：§6 说 2017~2020 偏松（乐观），
    而 flat 10% 在那段是正确值，真正的偏差在 2020-08-24 之后、且是偏严（保守）。
    """
    text = _prereg_text()
    assert "## 附录 C" in text, "§6 的实现偏差必须有附录 C 记录"
    assert "PORTFOLIO_PRICE_LIMIT_RATE" in text
    assert "偏严" in text and "偏松" in text, "附录 C 必须把两个方向的差别写清"
    assert "2020-08-24" in text
