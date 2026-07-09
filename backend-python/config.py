"""应用配置管理模块。

使用 pydantic-settings 从环境变量和 .env 文件加载配置。
支持多模型多 API 提供商：DeepSeek / OpenAI / 自定义兼容接口。
"""
from functools import lru_cache
from typing import Dict, List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==================== 多 API 提供商 ====================
    # DeepSeek（默认）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    # OpenAI（或任何兼容接口）
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    # 通用（其他兼容接口共用）
    custom_api_key: str = ""
    custom_base_url: str = ""

    # 兼容旧配置字段（仅作为 DeepSeek 的 fallback）
    api_key: str = ""
    base_url: str = ""

    # ==================== 模型定义 ====================
    # 每个模型需指定所属 provider: "deepseek" | "openai" | "custom"
    model_providers: Dict[str, str] = {
        "deepseek-chat": "deepseek",
        "deepseek-reasoner": "deepseek",
        "gpt-4o-mini": "openai",
        "gpt-4o": "openai",
        "gpt-3.5-turbo": "openai",
        "qwen-vl-plus": "custom",
        "qwen-plus": "custom",
    }

    # 可用模型列表（从 model_providers 的 key 自动生成，或手动指定）
    available_models: List[str] = []

    # LLM 默认参数
    model_name: str = "deepseek-chat"
    max_tokens: int = 2048
    temperature: float = 0.7

    # ==================== 数据库 ====================
    database_url: str = "sqlite+aiosqlite:///./chatbot.db"

    # ==================== 日志 ====================
    log_level: str = "INFO"

    # ==================== 兼容旧配置 ====================
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

    def get_provider_config(self, model_name: str) -> dict:
        """根据模型名获取对应的 API 提供商配置（api_key + base_url）。"""
        provider = self.model_providers.get(model_name, "deepseek")

        if provider == "openai":
            api_key = self.openai_api_key or self.api_key
            base_url = self.openai_base_url
        elif provider == "custom":
            api_key = self.custom_api_key or self.api_key
            base_url = self.custom_base_url
        else:  # deepseek (默认)
            api_key = self.deepseek_api_key or self.api_key
            base_url = self.deepseek_base_url

        return {"api_key": api_key, "base_url": base_url}

    def get_available_models(self) -> List[str]:
        """返回可用模型列表。有对应 API key 的才显示。"""
        if self.available_models:
            return self.available_models

        models = []
        for model, provider in self.model_providers.items():
            cfg = self.get_provider_config(model)
            if cfg["api_key"]:
                models.append(model)
        # 至少保留默认模型
        if not models:
            models = list(self.model_providers.keys())
        return models


@lru_cache
def get_settings() -> Settings:
    """获取配置单例。"""
    return Settings()
