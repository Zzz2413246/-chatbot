"""模型 API：获取可用模型列表。"""
from fastapi import APIRouter

from config import get_settings

router = APIRouter(prefix="/api", tags=["models"])
settings = get_settings()


@router.get("/models")
async def list_models() -> dict:
    """获取可用模型列表（仅返回已配置 API Key 的模型）。"""
    return {
        "models": settings.get_available_models(),
        "default_model": settings.model_name,
    }
