"""健康检查路由（保持既有契约不变）。"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """健康检查：返回服务存活状态。"""
    return {"status": "ok"}
