"""渗透测试：尝试用路径变体绕过网关。

《推进计划》T2 的验收标准之一是「渗透测试中未发现绕过网关的路径」。
这条不能靠"我们装了全局中间件"来回答——真实绕过从来不是删中间件，而是让
**路径对不上**：网关按路径查操作类型，查不到就静默放行，而日志里看不出异常
（"没被拒绝"和"没被检查"长得一样）。

因此本文件不是随机 fuzz，而是针对"路径归一化差异"这一个已知弱点逐个试探：

| 变体 | 想验证的事 |
|---|---|
| ``/backtests/`` | 尾斜杠触发 307 前是否已被拦截 |
| ``//backtests`` | 双斜杠是否会命中路由但绕过查表 |
| ``/BackTests`` | 大小写差异 |
| ``/backtests?x=1`` | 查询串是否影响查表 |
| ``/api/backtests`` | 单端口形态下前缀剥离后是否重新经过网关 |
| ``/backtests/compare`` | 同前缀的兄弟路由是否各自受检 |

判定标准不是"返回非 200"就通过，而是**业务没有被执行**：
- 被网关拒绝（4xx + 原因码），或
- 根本没命中路由（404/405/307）。

若出现 200，或出现一张 ``status=ok`` 的 T1 工件，就是真实绕过，测试必须失败。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from deepquant.api.app import app, create_app
from deepquant.api.config import get_data_root
from deepquant.audit.operations import AUDIT_DIR_ENV
from deepquant.audit.store import AuditStore
from deepquant.data.pipeline import ingest_daily_csv
from deepquant.risk.gateway import MODE_ENFORCE, RISK_MODE_ENV
from deepquant.risk.reason_codes import REASON_CODES

OPERATOR = "ops@deepquant"


def _make_daily_csv(path: Path, *, n: int = 60) -> Path:
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    closes: list[float] = []
    for i in range(n):
        if i < 15:
            closes.append(10.0 - 0.1 * i)
        elif i < 45:
            closes.append(8.6 + 0.1 * (i - 14))
        else:
            closes.append(11.6 - 0.1 * (i - 44))
    lines = ["date,open,high,low,close,volume"]
    for d, c in zip(dates, closes):
        lines.append(f"{d:%Y-%m-%d},{c - 0.05:.2f},{c + 0.1:.2f},{c - 0.1:.2f},{c:.2f},10000")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPQUANT_DB_PATH", str(tmp_path / "backtests.db"))
    monkeypatch.setenv(RISK_MODE_ENV, MODE_ENFORCE)
    monkeypatch.delenv(AUDIT_DIR_ENV, raising=False)


@pytest.fixture
def dataset(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    src = tmp_path / "src"
    src.mkdir()
    ingest_daily_csv(_make_daily_csv(src / "TEST01.csv"), symbol="TEST01", data_root=data_root)
    app.dependency_overrides[get_data_root] = lambda: data_root.resolve()
    yield "TEST01"
    app.dependency_overrides.clear()


@pytest.fixture
def client(dataset: str):
    with TestClient(app) as c:
        yield c


def _payload() -> dict:
    return {
        "dataset_symbol": "TEST01",
        "strategy_id": "ma_cross",
        "strategy_params": {"fast_window": 5, "slow_window": 20},
        "initial_cash": 100000,
    }


def _is_gated_or_not_routed(response) -> bool:
    if response.status_code == 200:
        return False
    if response.status_code in {404, 405, 307, 308}:
        return True  # 没命中业务路由，不算绕过
    body = response.json() if response.content else {}
    code = (body.get("error") or {}).get("code")
    return code in REASON_CODES


@pytest.mark.parametrize(
    "path",
    [
        "/backtests",
        "/backtests/",
        "//backtests",
        "/BackTests",
        "/backtests?symbol=TEST01",
        "/backtests/compare",
        "/portfolio-backtests",
    ],
)
def test_path_variant_cannot_bypass_gate(client, path: str) -> None:
    """无身份时，任何路径变体都不得让回测真正执行。"""
    response = client.post(path, json=_payload())
    assert _is_gated_or_not_routed(response), (
        f"{path} 绕过了风控：status={response.status_code} body={response.text[:200]}"
    )


def test_no_successful_audit_card_for_blocked_attempts(
    client, dataset: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """更强的判据：即使某个变体返回了非 200，也不得留下"执行成功"的工件。

    只看状态码会漏掉一种情况：请求被执行了，但业务自己返回了 4xx。
    工件里的 ``status=ok`` 才是"真的跑过"的铁证。
    """
    audit_root = tmp_path / "audit"
    monkeypatch.setenv(AUDIT_DIR_ENV, str(audit_root))

    for path in ("/backtests", "/backtests/", "//backtests", "/api/backtests"):
        client.post(path, json=_payload())

    store = AuditStore(audit_root)
    if not store.index_path.exists():
        pytest.skip("没有任何请求进入审计层（全部在路由层被拒绝）")
    ok_cards = [c for c in store.iter_all() if (c.get("outcome") or {}).get("status") == "ok"]
    assert ok_cards == [], f"存在执行成功的工件，说明闸门被绕过：{ok_cards[:2]}"


def test_api_prefix_is_gated_when_ui_is_mounted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """单端口形态：``/api/backtests`` 经前缀剥离后仍必须经过网关。

    前缀剥离是纯 ASGI 中间件、直接改写 scope["path"] 再分发。若它被装配在
    网关**内层**，改写后的请求就绕过了查表——那是本系统唯一真实存在的绕过面。
    这里用独立的 app 实例（UI 挂载在 create_app 时决定）验证装配顺序。
    """
    from deepquant.api import ui

    monkeypatch.setenv(ui.UI_ENV_VAR, "1")
    if not (ui.ui_dist_dir() / "index.html").is_file():
        pytest.skip("前端产物不存在，无法构造单端口形态")

    fresh = create_app()
    names = [item.cls.__name__ for item in fresh.user_middleware]
    assert names.index("StripApiPrefix") < names.index("RiskGateMiddleware"), (
        "前缀剥离必须在网关外层，否则 /api/* 会绕过检查"
    )

    data_root = tmp_path / "data"
    data_root.mkdir()
    src = tmp_path / "src"
    src.mkdir()
    ingest_daily_csv(_make_daily_csv(src / "TEST01.csv"), symbol="TEST01", data_root=data_root)
    fresh.dependency_overrides[get_data_root] = lambda: data_root.resolve()

    with TestClient(fresh) as c:
        response = c.post("/api/backtests", json=_payload())
    fresh.dependency_overrides.clear()

    assert response.status_code == 403, f"/api 前缀绕过网关：{response.status_code} {response.text[:120]}"
    assert response.json()["error"]["code"] == "identity_missing"


def test_authorized_request_still_works_under_enforce(client) -> None:
    """反向断言：渗透测试不能把正常业务一起打死。"""
    response = client.post("/backtests", json=_payload(), headers={"X-Operator": OPERATOR})
    assert response.status_code == 200, response.text
