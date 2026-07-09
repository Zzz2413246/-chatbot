"""
配置管理模块
- 使用 pydantic-settings 管理环境变量配置
- 支持多模型配置（DeepSeek、OpenAI、通义千问等）
- 每个模型有独立的 base_url、api_key、model_name
"""
import os
from typing import Dict, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


# 后端根目录（backend/）
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BaseSettings):
    """应用全局配置，从 .env 文件读取"""

    model_config = SettingsConfigDict(
        env_file=os.path.join(BACKEND_DIR, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        protected_namespaces=(),
    )

    # 默认模型配置（与 .env 中的 API_KEY / BASE_URL / MODEL_NAME 对应）
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-chat"

    # 通用参数
    max_context_length: int = 8192
    max_tokens: int = 2048
    temperature: float = 0.7
    request_timeout: int = 120

    # 服务配置
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./data/chatbot.db"

    # 微信小程序登录
    wechat_appid: str = ""
    wechat_secret: str = ""

    # 微信开放平台（网站应用扫码登录）
    # 在 https://open.weixin.qq.com 注册网站应用后获取
    wechat_web_appid: str = ""
    wechat_web_secret: str = ""
    # 扫码登录回调地址（需在微信开放平台配置相同的授权回调域）
    # 例如: http://localhost/wechat/callback
    wechat_web_redirect_uri: str = "http://localhost/wechat/callback"

    # 认证
    token_secret: str = "chatbot-secret-key-2024"
    token_expire_hours: int = 72


settings = Settings()


# ========== 预设模型列表 ==========
# 每个模型有独立的 base_url / api_key / model_name
# api_key 默认复用 .env 中的 API_KEY（DeepSeek），
# 其它厂商留空时可在运行时通过环境变量覆盖或回退到默认 API_KEY。
MODEL_PRESETS: Dict[str, dict] = {
    "deepseek-chat": {
        "model_name": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": settings.api_key,
        "description": "DeepSeek 通用对话模型，擅长中文科普对话",
        "supports_image": False,
    },
    "deepseek-reasoner": {
        "model_name": "deepseek-reasoner",
        "base_url": "https://api.deepseek.com",
        "api_key": settings.api_key,
        "description": "DeepSeek 推理模型，深度思考，适合复杂问题",
        "supports_image": False,
    },
    "deepseek-coder": {
        "model_name": "deepseek-coder",
        "base_url": "https://api.deepseek.com",
        "api_key": settings.api_key,
        "description": "DeepSeek 代码模型，擅长编程与技术解释",
        "supports_image": False,
    },
    "qwen-vl-plus": {
        # 注意：qwen-plus 是纯文本模型，不支持图片；
        # 视觉理解需使用 qwen-vl-plus / qwen-vl-max / qwen3.x-vl-plus 等多模态模型
        "model_name": "qwen-vl-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",  # 请在 .env 中通过 QWEN_API_KEY 设置
        "description": "通义千问 VL Plus，阿里云视觉模型，支持图片识别",
        "supports_image": True,
    },
}


def get_model_config(model_name: str) -> Optional[dict]:
    """根据模型名获取模型配置；不存在则返回 None"""
    return MODEL_PRESETS.get(model_name)


def get_available_models() -> list:
    """获取可用模型列表（用于 /api/models 接口）"""
    return [
        {
            "model_name": name,
            "base_url": cfg["base_url"],
            "description": cfg["description"],
            "supports_image": cfg["supports_image"],
        }
        for name, cfg in MODEL_PRESETS.items()
    ]
