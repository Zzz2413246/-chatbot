"""Application configuration management.

Uses pydantic-settings with a custom YAML source for multi-environment support.
"""
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .yaml_loader import load_yaml_config, resolve_env_file


class _YamlConfigSource(PydanticBaseSettingsSource):
    """Loads config values from deep-merged YAML files at lowest priority."""

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
    """Application global configuration.

    Source priority (lowest to highest):
      1. Field defaults in this class
      2. YAML files  (config.yaml + config.{env}.yaml)
      3. .env file   (.env.{env} or .env fallback)
      4. Environment variables
      5. Constructor kwargs
    """

    model_config = SettingsConfigDict(
        env_file=resolve_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=(),
    )

    # ==================== Multi-API Provider Keys ====================
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    custom_api_key: str = ""
    custom_base_url: str = ""

    # Legacy fallback fields (only for deepseek)
    api_key: str = ""
    base_url: str = ""

    # ==================== Model Definitions ====================
    model_providers: Dict[str, str] = {
        "deepseek-chat": "deepseek",
        "deepseek-reasoner": "deepseek",
        "gpt-4o-mini": "openai",
        "gpt-4o": "openai",
        "gpt-3.5-turbo": "openai",
        "qwen-vl-plus": "custom",
        "qwen-plus": "custom",
    }

    available_models: List[str] = []

    # LLM defaults
    model_name: str = "deepseek-chat"
    max_tokens: int = 2048
    temperature: float = 0.7

    # ==================== ASR ====================
    asr_model: str = "qwen3-asr-flash"
    asr_language: str = "zh"

    # ==================== Database ====================
    database_url: str = "sqlite+aiosqlite:///./chatbot.db"

    # ==================== Logging ====================
    log_level: str = "INFO"

    # ==================== Custom source chain ====================
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            _YamlConfigSource(settings_cls),
        )

    # ==================== Validators ====================
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

    # ==================== Provider resolution ====================
    def get_provider_config(self, model_name: str) -> dict:
        """根据模型名获取对应的 API 提供商配置（api_key + base_url）。

        注意：api_key / base_url 旧字段仅作为 DeepSeek 的 fallback，
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

    def get_available_models(self) -> List[str]:
        """返回可用模型列表。有对应 API key 的才显示。"""
        if self.available_models:
            return self.available_models

        models = []
        for model, provider in self.model_providers.items():
            cfg = self.get_provider_config(model)
            if cfg["api_key"]:
                models.append(model)
        if not models:
            models = list(self.model_providers.keys())
        return models


@lru_cache
def get_settings() -> Settings:
    """获取配置单例。"""
    return Settings()
