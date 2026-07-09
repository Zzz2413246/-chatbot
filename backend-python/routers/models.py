"""模型 API：获取可用模型列表。"""
from fastapi import APIRouter

from config import get_settings

router = APIRouter(prefix="/api", tags=["models"])
settings = get_settings()

# 支持视觉（多模态）的模型集合
_VISION_MODELS = {"qwen-vl-plus", "qwen-vl-max", "gpt-4o", "gpt-4o-mini"}


@router.get("/models")
async def list_models() -> dict:
    """获取可用模型列表（仅返回已配置 API Key 的模型）。

    返回两种结构供前端兼容：
      - models: 字符串数组（旧前端）
      - models_detail: 对象数组，含 model_name / supports_image / is_default
    """
    available = settings.get_available_models()
    default_model = settings.model_name

    models_detail = [
        {
            "model_name": m,
            "supports_image": m in _VISION_MODELS,
            "is_default": m == default_model,
        }
        for m in available
    ]
    return {
        "models": available,
        "models_detail": models_detail,
        "default_model": default_model,
    }
