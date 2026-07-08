"""预设角色 API。"""
from fastapi import APIRouter

from prompts.presets import DEFAULT_PRESET_NAME, PRESETS

router = APIRouter(prefix="/api", tags=["presets"])


@router.get("/presets")
async def list_presets() -> dict:
    """获取预设角色列表。"""
    return {
        "presets": [p.to_dict() for p in PRESETS],
        "default": DEFAULT_PRESET_NAME,
    }
