"""
工具服务
- 定义若干实用工具函数，供 LLM Agent 调用
- 使用 LangChain @tool 装饰器
- 工具列表：
    - calculator: 数学计算
    - get_current_time: 获取当前时间
    - web_search: 模拟网络搜索
"""
from datetime import datetime

from langchain_core.tools import tool

from utils.logger import logger


@tool
def calculator(expression: str) -> str:
    """
    数学计算器工具。
    输入一个数学表达式字符串（如 "2 + 3 * 4" 或 "(1+2)*3"），返回计算结果。
    支持基本四则运算、幂运算（**）和括号。
    不允许执行任意 Python 代码，仅做表达式求值。
    """
    if not expression or not expression.strip():
        return "错误：表达式为空"
    expr = expression.strip()
    # 安全检查：仅允许数字、运算符、括号、小数点、空格
    allowed_chars = set("0123456789+-*/().% \t")
    # 幂运算用 ** 已包含在 * 中
    if not all(c in allowed_chars for c in expr):
        return f"错误：表达式包含非法字符，仅允许数字与 + - * / ( ) % "
    try:
        # eval 仅在受限字符集下执行，相对安全
        result = eval(expr, {"__builtins__": {}}, {})
        logger.info(f"计算器工具 | expr={expr} | result={result}")
        return str(result)
    except ZeroDivisionError:
        return "错误：除以零"
    except Exception as e:
        logger.warning(f"计算器工具失败 | expr={expr} | error={e}")
        return f"错误：无法计算「{expr}」，请检查表达式格式"


@tool
def get_current_time() -> str:
    """
    获取当前时间工具。
    无需参数，返回当前系统时间（北京时间，ISO 格式）。
    """
    now = datetime.utcnow()
    # 北京时间 = UTC + 8
    from datetime import timedelta
    beijing = now + timedelta(hours=8)
    time_str = beijing.strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"时间工具 | 返回={time_str}")
    return f"当前北京时间：{time_str}"


@tool
def web_search(query: str) -> str:
    """
    网络搜索工具（模拟）。
    输入搜索关键词，返回模拟的搜索结果提示信息。
    注意：此工具为模拟实现，不进行真实网络请求。
    """
    if not query or not query.strip():
        return "错误：搜索关键词为空"
    keyword = query.strip()
    logger.info(f"网络搜索工具（模拟）| query={keyword}")
    # 模拟搜索结果
    result = (
        f"已为您搜索「{keyword}」（模拟结果）。\n"
        f"这是一个模拟的网络搜索工具，未连接真实搜索引擎。\n"
        f"如需真实搜索能力，请接入搜索 API（如 SerpAPI、Bing Search 等）。\n"
        f"建议您根据搜索意图自行核实相关信息。"
    )
    return result


# 工具列表（供 Agent 使用）
DEFAULT_TOOLS = [calculator, get_current_time, web_search]


def get_default_tools():
    """获取默认工具列表"""
    return DEFAULT_TOOLS
