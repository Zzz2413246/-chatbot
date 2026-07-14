"""
配置管理模块
- 使用 pydantic-settings 管理环境变量配置
- 支持多模型配置（DeepSeek、OpenAI、通义千问等）
- 每个模型有独立的 base_url、api_key、model_name
- 支持 YAML 多环境配置（config.yaml + config.{env}.yaml）
- 支持多 API 提供商独立密钥
"""
import os
from typing import Dict, Optional, Tuple, Type, Any

from pydantic import field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource

from .yaml_loader import load_yaml_config, resolve_env_file


# 后端根目录（backend/）
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _YamlConfigSource(PydanticBaseSettingsSource):
    """从 YAML 文件加载配置（最低优先级）"""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data: Dict[str, Any] = load_yaml_config()

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Optional[Any], str, bool]:
        if field_name in self._data:
            return self._data[field_name], f"yaml:{field_name}", False
        return None, "", False

    def __call__(self) -> Dict[str, Any]:
        return self._data


class Settings(BaseSettings):
    """应用全局配置，从 .env 文件和 YAML 文件读取"""

    model_config = SettingsConfigDict(
        env_file=resolve_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        protected_namespaces=(),
    )

    # ==================== 多 API 提供商密钥 ====================
    # DeepSeek（默认提供商）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    # OpenAI
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    # 自定义（通义千问 DashScope 等 OpenAI 兼容接口）
    custom_api_key: str = ""
    custom_base_url: str = ""

    # 兼容旧配置字段（仅作为 DeepSeek 的后备方案）
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"

    # ==================== 模型提供商映射 ====================
    model_providers: Dict[str, str] = {
        "deepseek-chat": "deepseek",
        "deepseek-reasoner": "deepseek",
        "deepseek-coder": "deepseek",
        "gpt-4o-mini": "openai",
        "gpt-4o": "openai",
        "gpt-3.5-turbo": "openai",
        "qwen-vl-plus": "custom",
        "qwen-plus": "custom",
    }

    # ==================== 默认模型配置 ====================
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

    # 微信小程序登录（预留）
    wechat_appid: str = ""
    wechat_secret: str = ""

    # 微信开放平台（预留）
    wechat_web_appid: str = ""
    wechat_web_secret: str = ""
    wechat_web_redirect_uri: str = "http://localhost/wechat/callback"

    # 认证
    token_secret: str = ""
    token_expire_hours: int = 72

    # ==================== 语音识别 (ASR) ====================
    asr_model: str = "qwen3-asr-flash"
    asr_language: str = "zh"

    # ==================== 配置来源自定义 ====================
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """配置源优先级（从低到高）：
        1. Field defaults
        2. YAML config files
        3. .env file
        4. Environment variables
        5. Constructor kwargs
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            _YamlConfigSource(settings_cls),
        )

    # ==================== 校验器 ====================
    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("temperature 必须在 0.0 到 2.0 之间")
        return v

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_tokens 必须大于 0")
        return v

    # ==================== 提供商配置解析 ====================
    def get_provider_config(self, model_name: str) -> dict:
        """根据模型名获取对应的 API 提供商配置（api_key + base_url）。

        注意：api_key / base_url 旧字段仅作为 DeepSeek 的后备方案，
        不混用到 openai / custom 提供商。
        """
        provider = self.model_providers.get(model_name, "deepseek")

        if provider == "openai":
            api_key = self.openai_api_key
            base_url = self.openai_base_url
        elif provider == "custom":
            api_key = self.custom_api_key
            base_url = self.custom_base_url
        else:  # deepseek (默认)
            api_key = self.deepseek_api_key or self.api_key
            base_url = self.deepseek_base_url or self.base_url

        return {"api_key": api_key, "base_url": base_url}


settings = Settings()


# ========== 预设模型列表 ==========
# 每个模型有独立的 base_url / api_key / model_name
# api_key 留空时自动从对应 provider 配置获取
# MODEL_PRESETS 中的 api_key 优先级高于 provider 级配置
MODEL_PRESETS: Dict[str, dict] = {
    "deepseek-chat": {
        "model_name": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": settings.deepseek_api_key or settings.api_key,
        "description": "DeepSeek 通用对话模型，擅长中文科普对话",
        "supports_image": False,
    },
    "deepseek-reasoner": {
        "model_name": "deepseek-reasoner",
        "base_url": "https://api.deepseek.com",
        "api_key": settings.deepseek_api_key or settings.api_key,
        "description": "DeepSeek 推理模型，深度思考，适合复杂问题",
        "supports_image": False,
    },
    "deepseek-coder": {
        "model_name": "deepseek-coder",
        "base_url": "https://api.deepseek.com",
        "api_key": settings.deepseek_api_key or settings.api_key,
        "description": "DeepSeek 代码模型，擅长编程与技术解释",
        "supports_image": False,
    },
    "qwen-vl-plus": {
        "model_name": "qwen-vl-plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": settings.custom_api_key,
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
