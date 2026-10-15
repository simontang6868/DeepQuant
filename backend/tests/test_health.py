"""验证 API 最小链路：health 接口可被调用且返回预期结构。"""

from fastapi.testclient import TestClient

from deepquant.api.app import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
