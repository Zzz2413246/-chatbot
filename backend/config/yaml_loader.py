"""YAML 配置加载器，支持环境特定覆盖。

用法：
  1. 设置 APP_ENV 环境变量（默认为 dev）
  2. 加载 config.yaml（基础配置）
  3. 加载 config.{APP_ENV}.yaml（环境覆盖），深度合并
"""
import os
from pathlib import Path
from typing import Any, Dict

import yaml


def _find_project_root() -> Path:
    """定位 backend/ 目录"""
    return Path(__file__).resolve().parent.parent


_PROJECT_ROOT = _find_project_root()


def get_app_env() -> str:
    """返回 APP_ENV 环境变量，默认 dev"""
    return os.getenv("APP_ENV", "dev").strip().lower()


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并 override 到 base。标量和列表会覆盖，嵌套 dict 会递归合并。"""
    result = base.copy()
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_yaml_config() -> Dict[str, Any]:
    """加载 config.yaml 并与 config.{env}.yaml 深度合并。

    返回深度合并后的 dict。缺失的文件会被静默跳过。
    """
    env = get_app_env()
    base_path = _PROJECT_ROOT / "config.yaml"
    env_path = _PROJECT_ROOT / f"config.{env}.yaml"

    config: Dict[str, Any] = {}

    if base_path.is_file():
        with open(base_path, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}

    if env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as fh:
            env_config = yaml.safe_load(fh) or {}
        config = deep_merge(config, env_config)

    return config


def resolve_env_file() -> str:
    """根据 APP_ENV 返回对应的 .env 文件路径。

    优先级：
    1. .env.{APP_ENV}  (例如 .env.dev)
    2. .env             (向后兼容的后备方案)
    """
    env = get_app_env()
    env_specific = _PROJECT_ROOT / f".env.{env}"
    fallback = _PROJECT_ROOT / ".env"

    if env_specific.is_file():
        return str(env_specific)
    if fallback.is_file():
        return str(fallback)
    return str(env_specific)
