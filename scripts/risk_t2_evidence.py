"""T2 验收取证脚本：四类检查点覆盖、拒绝原因码、可检索性、旁路与渗透结果。

对应《推进计划》5.2 T2 的四条验收标准：

1. 四类检查点覆盖率 100%；
2. 每次拒绝均有原因码且可在工件中检索；
3. 渗透测试中未发现绕过网关的路径；
4. 拒绝不得依赖「人工记得查」（由中间件承担，覆盖率由对账证明）。

用法::

    PY="C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe"
    PYTHONPATH=backend/src $PY scripts/risk_t2_evidence.py

只写 ``reports/RISK_T2_EVIDENCE.json``；临时目录用完即删，不触碰任何既有产物。
"""

from __future__ import annotations

import json
import logging
import platform
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from deepquant.api.app import create_app  # noqa: E402
from deepquant.api.config import get_data_root  # noqa: E402
from deepquant.audit.store import AuditStore  # noqa: E402
from deepquant.data.pipeline import ingest_daily_csv  # noqa: E402
from deepquant.risk.bypass import bypass_report  # noqa: E402
from deepquant.risk.checkpoints import category_coverage, default_checkpoints  # noqa: E402
from deepquant.risk.gateway import MODE_ENFORCE, MODE_OBSERVE, RISK_MODE_ENV  # noqa: E402
from deepquant.risk.reason_codes import REASON_CODES, coverage as code_coverage  # noqa: E402

OUT_PATH = REPO_ROOT / "reports" / "RISK_T2_EVIDENCE.json"
OPERATOR = "ops@evidence"

#: 四类拒绝场景：每个类别至少一条真实请求。
SCENARIOS = (
    ("identity", "identity_missing", {"method": "POST", "path": "/backtests"}, {}),
    ("limit", "limit_cash_range", {"method": "POST", "path": "/backtests"}, {"initial_cash": 1e12}),
    ("state", "state_dataset_unavailable", {"method": "POST", "path": "/backtests"}, {"dataset_symbol": "NOPE"}),
    ("field", "field_missing", {"method": "POST", "path": "/backtests"}, {"drop": "strategy_id"}),
)

#: 路径变体（渗透测试）。
VARIANTS = (
    "/backtests",
    "/backtests/",
    "//backtests",
    "/BackTests",
    "/backtests?symbol=TEST01",
    "/backtests/compare",
    "/portfolio-backtests",
    "/api/backtests",
)


def _make_csv(path: Path, n: int = 60) -> Path:
    dates = pd.date_range("2020-01-01", periods=n, freq="D")
    closes = []
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


def _payload() -> dict:
    return {
        "dataset_symbol": "TEST01",
        "strategy_id": "ma_cross",
        "strategy_params": {"fast_window": 5, "slow_window": 20},
        "initial_cash": 100000,
    }


def main() -> int:
    logging.getLogger("deepquant").setLevel(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="risk_t2_") as tmp:
        base = Path(tmp)
        data_root = base / "data"
        data_root.mkdir()
        src = base / "src"
        src.mkdir()
        ingest_daily_csv(_make_csv(src / "TEST01.csv"), symbol="TEST01", data_root=data_root)

        import os

        os.environ["DEEPQUANT_DB_PATH"] = str(base / "backtests.db")
        os.environ[RISK_MODE_ENV] = MODE_ENFORCE
        os.environ["DEEPQUANT_AUDIT_DIR"] = str(base / "audit")

        app = create_app()
        app.dependency_overrides[get_data_root] = lambda: data_root.resolve()

        # ---- 1. 四类场景：状态码 + 原因码 + 可检索性 ----
        scenario_results = []
        with TestClient(app) as client:
            for category, expected_code, req, mutate in SCENARIOS:
                body = _payload()
                if "drop" in mutate:
                    body.pop(mutate["drop"], None)
                else:
                    body.update({k: v for k, v in mutate.items() if k != "drop"})
                headers = {} if category == "identity" else {"X-Operator": OPERATOR}
                response = client.post(req["path"], json=body, headers=headers)
                payload = response.json() if response.content else {}
                actual_code = (payload.get("error") or {}).get("code")
                scenario_results.append(
                    {
                        "category": category,
                        "expected_code": expected_code,
                        "actual_code": actual_code,
                        "status_code": response.status_code,
                        "code_in_table": actual_code in REASON_CODES,
                        "matches_expected": actual_code == expected_code,
                        "body_excerpt": response.text[:160],
                    }
                )

            # ---- 3. 渗透测试：路径变体 ----
            variants = []
            for path in VARIANTS:
                response = client.post(path, json=_payload())
                body = response.json() if response.content else {}
                code = (body.get("error") or {}).get("code")
                variants.append(
                    {
                        "path": path,
                        "status_code": response.status_code,
                        "reason_code": code,
                        "blocked": response.status_code != 200,
                        "gated_or_not_routed": response.status_code in {404, 405, 307, 308}
                        or code in REASON_CODES,
                    }
                )

            # ---- 2. 可检索性：按原因码回查工件 ----
            store = AuditStore(base / "audit")
            retrievable = {}
            for _category, code, _req, _mutate in SCENARIOS:
                hits = store.query(reason_code=code)
                retrievable[code] = {
                    "n_cards": len(hits),
                    "status_in_card": hits[0]["outcome"]["status"] if hits else None,
                    "checkpoint_in_card": (hits[0]["outcome"].get("risk") or {}).get("checkpoint")
                    if hits
                    else None,
                }

            # ---- 4. observe 模式：不拦截但记录 ----
            os.environ[RISK_MODE_ENV] = MODE_OBSERVE
            observe_response = client.post("/backtests", json=_payload())
            observe_header = observe_response.headers.get("X-DeepQuant-Risk")
            observe_card = store.query(action="backtest.run", status="ok")
            os.environ[RISK_MODE_ENV] = MODE_ENFORCE

        app.dependency_overrides.clear()

        # ---- 覆盖率与旁路 ----
        cp_cov = category_coverage(default_checkpoints())
        code_cov = code_coverage()
        bypass = bypass_report(create_app())
        chain_ok = store.verify().ok

    criteria = {
        "criterion_1_checkpoint_coverage": {
            "ratio": cp_cov["ratio"],
            "missing": cp_cov["missing"],
            "n_checkpoints": cp_cov["n_checkpoints"],
            "reason_code_categories": code_cov["required_categories"],
            "reason_codes_missing_categories": code_cov["missing_categories"],
            "pass": cp_cov["ratio"] == 1.0 and not code_cov["missing_categories"],
        },
        "criterion_2_reason_codes": {
            "scenarios": scenario_results,
            "all_have_code": all(s["code_in_table"] for s in scenario_results),
            "all_match_expected": all(s["matches_expected"] for s in scenario_results),
            "retrievable": retrievable,
            "all_retrievable": all(v["n_cards"] >= 1 for v in retrievable.values()),
            "pass": all(s["code_in_table"] and s["matches_expected"] for s in scenario_results)
            and all(v["n_cards"] >= 1 for v in retrievable.values()),
        },
        "criterion_3_no_bypass": {
            "bypass_report": bypass,
            "penetration": variants,
            "all_blocked": all(v["gated_or_not_routed"] for v in variants),
            "pass": bypass["ok"] and all(v["gated_or_not_routed"] for v in variants),
        },
        "criterion_4_not_manual": {
            "mechanism": "网关为全局中间件，操作类型由路由表驱动；未登记的可变更接口由旁路对账检出",
            "uncovered_mutating": bypass["uncovered_mutating"],
            "pass": bypass["ok"] and bypass["uncovered_mutating"] == [],
        },
        "observe_mode": {
            "status_code": observe_response.status_code,
            "header": observe_header,
            "n_ok_cards": len(observe_card),
            "pass": observe_response.status_code == 200 and observe_header == "would-reject",
        },
        "audit_chain_ok": chain_ok,
    }
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "criteria": criteria,
        "overall_pass": all(
            criteria[k]["pass"]
            for k in (
                "criterion_1_checkpoint_coverage",
                "criterion_2_reason_codes",
                "criterion_3_no_bypass",
                "criterion_4_not_manual",
                "observe_mode",
            )
        )
        and chain_ok,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    print("=" * 74)
    print(f"检查点覆盖 : {cp_cov['n_checkpoints']} 个 / 缺 {cp_cov['missing']} → {cp_cov['ratio']:.0%}")
    print(f"原因码     : {len(REASON_CODES)} 个，四类缺 {code_cov['missing_categories']}")
    print("-" * 74)
    for s in scenario_results:
        flag = "PASS" if s["matches_expected"] and s["code_in_table"] else "FAIL"
        print(f"  [{flag}] {s['category']:9s} {s['status_code']} {s['actual_code']}")
    for code, info in retrievable.items():
        flag = "PASS" if info["n_cards"] >= 1 else "FAIL"
        print(f"  [{flag}] 可检索 {code:28s} 工件 {info['n_cards']} 张 / checkpoint={info['checkpoint_in_card']}")
    print("-" * 74)
    print(f"旁路对账   : ok={bypass['ok']} 未覆盖可变更接口 {bypass['uncovered_mutating']}")
    for v in variants:
        flag = "PASS" if v["gated_or_not_routed"] else "FAIL"
        print(f"  [{flag}] {v['path']:22s} → {v['status_code']} {v['reason_code'] or ''}")
    print("-" * 74)
    print(f"观察期     : {observe_response.status_code} / header={observe_header}")
    print(f"审计链     : ok={chain_ok}")
    print(f"总体       : {'PASS' if evidence['overall_pass'] else 'FAIL'}")
    print(f"证据文件   : {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0 if evidence["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
