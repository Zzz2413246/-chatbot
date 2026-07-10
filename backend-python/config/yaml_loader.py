"""YAML config loader with environment-specific overrides."""
import os
from pathlib import Path
from typing import Any, Dict

import yaml


def _find_project_root() -> Path:
    """Locate the backend-python directory (parent of config/)."""
    return Path(__file__).resolve().parent.parent


_PROJECT_ROOT = _find_project_root()


def get_app_env() -> str:
    """Return APP_ENV from environment, defaulting to 'dev'."""
    return os.getenv("APP_ENV", "dev").strip().lower()


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge override into base. Returns a new dict.

    Scalars and lists in override replace those in base.
    Nested dicts are merged recursively.
    """
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
    """Load config.yaml and merge with config.{env}.yaml.

    Returns the deep-merged dict. Missing files are silently skipped.
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
    """Return the appropriate .env file path based on APP_ENV.

    Priority:
    1. .env.{APP_ENV}  (e.g. .env.dev)
    2. .env            (backward compat fallback)
    """
    env = get_app_env()
    env_specific = _PROJECT_ROOT / f".env.{env}"
    fallback = _PROJECT_ROOT / ".env"

    if env_specific.is_file():
        return str(env_specific)
    if fallback.is_file():
        return str(fallback)
    return str(env_specific)
