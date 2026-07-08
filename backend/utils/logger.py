"""
结构化日志模块
- 使用 loguru 输出 JSON 格式日志
- 记录请求、响应、错误
- 日志级别：DEBUG、INFO、WARNING、ERROR
"""
import os
import sys
import json
from typing import Any

from loguru import logger as _logger


# 日志目录
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BACKEND_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def json_serializer(record: dict) -> str:
    """将日志记录序列化为 JSON 字符串"""
    subset = {
        "timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
    }
    # 合并 extra 字段
    if record.get("extra"):
        for k, v in record["extra"].items():
            if k not in subset:
                subset[k] = _safe_json(v)
    if record.get("exception"):
        subset["exception"] = str(record["exception"])
    return json.dumps(subset, ensure_ascii=False, default=str) + "\n"


def _safe_json(v: Any) -> Any:
    """安全转换为可序列化对象"""
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except (TypeError, ValueError):
        return str(v)


def setup_logging(debug: bool = False) -> None:
    """配置日志系统"""
    _logger.remove()

    level = "DEBUG" if debug else "INFO"

    # 控制台输出（彩色，便于开发查看）
    _logger.add(
        sys.stdout,
        level=level,
        backtrace=True,
        diagnose=debug,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>",
    )

    # 文件输出（JSON 格式）
    _logger.add(
        os.path.join(LOG_DIR, "app_{time:YYYY-MM-DD}.log"),
        level=level,
        rotation="00:00",
        retention="14 days",
        compression="zip",
        serialize=True,
    )

    # 错误日志单独输出
    _logger.add(
        os.path.join(LOG_DIR, "error_{time:YYYY-MM-DD}.log"),
        level="ERROR",
        rotation="00:00",
        retention="30 days",
        compression="zip",
        serialize=True,
    )


# 导出全局 logger 实例
logger = _logger


# 便捷方法：记录请求日志
def log_request(method: str, path: str, params: dict = None, body: Any = None) -> None:
    logger.bind(
        event="request",
        method=method,
        path=path,
        query_params=params,
        body=body,
    ).info(f"{method} {path}")


def log_response(method: str, path: str, status_code: int, duration_ms: float) -> None:
    logger.bind(
        event="response",
        method=method,
        path=path,
        status_code=status_code,
        duration_ms=round(duration_ms, 2),
    ).info(f"{method} {path} -> {status_code} ({duration_ms:.2f}ms)")


def log_error(error: Exception, context: dict = None) -> None:
    logger.bind(event="error", context=context).exception(f"{type(error).__name__}: {error}")
