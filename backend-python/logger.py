"""结构化日志配置（基于 structlog）。"""
import logging
import sys

import structlog

from config import get_settings

settings = get_settings()


def setup_logging() -> None:
    """初始化日志配置。"""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # 标准库 logging 配置
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    # structlog 配置
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """获取一个绑定 logger。"""
    return structlog.get_logger(name)
