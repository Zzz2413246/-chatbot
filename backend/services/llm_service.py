"""
LLM 服务
- 基于 LangChain ChatOpenAI
- 支持多模型运行时切换
- 流式输出（streaming）
- 超时与重试机制（仅重试连接错误，不重试超时）
- 图片识别（多模态，base64 图片）
- 异步架构 + HTTP 连接池复用
- 上下文长度管理（截断旧消息）
- Token 用量统计
- 多模型并行对比
- 工具调用（Tool Calling）
"""
import asyncio
from typing import AsyncGenerator, List, Dict, Optional, Any, Tuple

import httpx
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    BaseMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from config.settings import settings, get_model_config, MODEL_PRESETS
from utils.logger import logger

# ========== 全局 HTTP 客户端（连接池复用，避免每次请求重新握手）==========
_shared_sync_client: Optional[httpx.Client] = None
_shared_async_client: Optional[httpx.AsyncClient] = None


def _get_sync_http_client() -> httpx.Client:
    """获取同步 httpx Client（ChatOpenAI 的 http_client 参数要求同步客户端）"""
    global _shared_sync_client
    if _shared_sync_client is None or _shared_sync_client.is_closed:
        _shared_sync_client = httpx.Client(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30.0,
            ),
            timeout=httpx.Timeout(
                settings.request_timeout,
                connect=10.0,
            ),
        )
    return _shared_sync_client


def _get_async_http_client() -> httpx.AsyncClient:
    """获取异步 httpx AsyncClient（ChatOpenAI 的 http_async_client 参数）"""
    global _shared_async_client
    if _shared_async_client is None or _shared_async_client.is_closed:
        _shared_async_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30.0,
            ),
            timeout=httpx.Timeout(
                settings.request_timeout,
                connect=10.0,
            ),
        )
    return _shared_async_client


class LLMService:
    """LLM 服务，支持多模型切换、流式输出、图片识别"""

    def __init__(self, model_name: Optional[str] = None):
        self.default_model = model_name or settings.model_name
        logger.info(f"LLMService 初始化，默认模型: {self.default_model}")

    # ---------- 模型实例创建 ----------
    def _create_chat_model(
        self,
        model_name: str,
        streaming: bool = False,
        temperature: Optional[float] = None,
    ) -> ChatOpenAI:
        """根据模型名创建 ChatOpenAI 实例（复用全局 HTTP 连接池）"""
        cfg = get_model_config(model_name)
        if cfg is None:
            logger.warning(f"模型 {model_name} 不在预设列表中，使用默认配置")
            api_key = settings.api_key
            base_url = settings.base_url
            actual_model = model_name
        else:
            api_key = cfg["api_key"] or settings.api_key
            base_url = cfg["base_url"]
            actual_model = cfg["model_name"]

        return ChatOpenAI(
            model=actual_model,
            api_key=api_key,
            base_url=base_url,
            streaming=streaming,
            temperature=temperature if temperature is not None else settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.request_timeout,
            max_retries=0,  # 由 tenacity 控制重试
            http_client=_get_sync_http_client(),        # 同步客户端
            http_async_client=_get_async_http_client(),  # 异步客户端
        )

    # ---------- 消息构建 ----------
    def _build_messages(
        self,
        history: List[Dict[str, str]],
        user_message: str,
        system_prompt: Optional[str] = None,
        image_data: Optional[str] = None,
    ) -> List[BaseMessage]:
        """将历史记录与当前消息构建为 LangChain 消息列表"""
        messages: List[BaseMessage] = []

        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))

        # 截断历史消息以控制上下文长度
        truncated = self._truncate_history(history)
        for msg in truncated:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
            elif role == "system" and not system_prompt:
                messages.append(SystemMessage(content=content))

        # 当前用户消息（可能含图片）
        if image_data:
            logger.info(f"构建图片消息 | image_data长度={len(image_data)} | 前50字符={image_data[:50]}")
            content_parts: List[Dict[str, Any]] = [{"type": "text", "text": user_message}]
            image_url = self._build_image_url(image_data)
            content_parts.append({"type": "image_url", "image_url": {"url": image_url}})
            messages.append(HumanMessage(content=content_parts))
        else:
            messages.append(HumanMessage(content=user_message))

        return messages

    def _truncate_history(self, history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """截断历史消息以控制上下文长度（粗略按字符数估算）"""
        if not history:
            return []
        max_chars = settings.max_context_length
        total = 0
        kept: List[Dict[str, str]] = []
        # 从最新消息向前保留
        for msg in reversed(history):
            content = msg.get("content", "")
            total += len(content)
            if total > max_chars:
                break
            kept.insert(0, msg)
        return kept

    def _build_image_url(self, image_data: str) -> str:
        """构建图片 URL，支持裸 base64 或 data URI"""
        if image_data.startswith("data:"):
            return image_data
        # 默认按 jpeg 处理
        return f"data:image/jpeg;base64,{image_data}"

    def _extract_usage(self, response: Any) -> Dict[str, int]:
        """
        从 LangChain 响应中提取 token 用量
        - 优先使用 usage_metadata（langchain 标准字段）
        - 回退到 response_metadata.token_usage（OpenAI 原始字段）
        - 提取失败返回 0
        """
        prompt_tokens = 0
        completion_tokens = 0
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage:
                # langchain 新版字段名
                prompt_tokens = int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0)
            else:
                resp_meta = getattr(response, "response_metadata", {}) or {}
                token_usage = resp_meta.get("token_usage", {}) or resp_meta.get("usage", {}) or {}
                prompt_tokens = int(token_usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(token_usage.get("completion_tokens", 0) or 0)
        except Exception as e:
            logger.warning(f"提取 token 用量失败: {e}")
        return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}

    # ---------- 非流式对话 ----------
    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.RemoteProtocolError)),
        reraise=True,
    )
    async def chat(
        self,
        history: List[Dict[str, str]],
        message: str,
        model_name: Optional[str] = None,
        system_prompt: Optional[str] = None,
        image_data: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        非流式对话
        返回 {"content": str, "usage": {"prompt_tokens": int, "completion_tokens": int}}
        """
        model = model_name or self.default_model
        logger.info(f"非流式对话 | model={model} | message={message[:50]}...")

        chat_model = self._create_chat_model(model, streaming=False, temperature=temperature)
        messages = self._build_messages(history, message, system_prompt, image_data)

        response = await chat_model.ainvoke(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)
        usage = self._extract_usage(response)
        logger.info(f"非流式对话完成 | 响应长度={len(content)} | usage={usage}")
        return {"content": content, "usage": usage}

    # ---------- 流式对话 ----------
    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.RemoteProtocolError)),
        reraise=True,
    )
    async def chat_stream(
        self,
        history: List[Dict[str, str]],
        message: str,
        model_name: Optional[str] = None,
        system_prompt: Optional[str] = None,
        image_data: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> AsyncGenerator[Tuple[str, Any], None]:
        """
        流式对话，逐块产出元组 (event_type, payload)
        - ("chunk", text)  —— 文本块
        - ("usage", dict)  —— 流结束后输出的 token 用量
        """
        model = model_name or self.default_model
        logger.info(f"流式对话 | model={model} | message={message[:50]}...")

        chat_model = self._create_chat_model(model, streaming=True, temperature=temperature)
        messages = self._build_messages(history, message, system_prompt, image_data)

        usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        async for chunk in chat_model.astream(messages):
            # 提取 token 用量（通常在最后一个 chunk）
            chunk_usage = self._extract_usage(chunk)
            if chunk_usage["prompt_tokens"] or chunk_usage["completion_tokens"]:
                usage = chunk_usage
            if chunk.content:
                text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                yield ("chunk", text)

        # 流结束后输出 token 用量
        yield ("usage", usage)
        logger.info(f"流式对话完成 | usage={usage}")

    # ---------- 标题生成 ----------
    async def generate_title(self, first_message: str, model_name: Optional[str] = None) -> str:
        """根据第一条消息生成 3-5 字标题"""
        model = model_name or self.default_model
        logger.info(f"生成标题 | message={first_message[:50]}...")

        system_prompt = (
            "你是一个标题生成助手。请根据用户的消息生成一个简短的中文标题，"
            "要求 3-5 个字，概括用户意图，不要加引号、标点符号或多余文字，只输出标题本身。"
        )
        try:
            result = await self.chat(
                history=[],
                message=first_message,
                model_name=model,
                system_prompt=system_prompt,
                temperature=0.3,
            )
            title = result["content"] if isinstance(result, dict) else str(result)
            title = title.strip().strip('"\'""').strip()
            # 截断到合理长度
            if len(title) > 20:
                title = title[:20]
            logger.info(f"标题生成完成: {title}")
            return title
        except Exception as e:
            logger.error(f"标题生成失败: {e}")
            # 回退：取前 10 个字符
            return first_message[:10].strip() or "新对话"

    # ---------- 模型可用性 ----------
    def list_models(self) -> List[dict]:
        """列出所有可用模型"""
        result = []
        for name, cfg in MODEL_PRESETS.items():
            result.append({
                "model_name": name,
                "base_url": cfg["base_url"],
                "description": cfg["description"],
                "supports_image": cfg["supports_image"],
                "is_default": name == self.default_model,
            })
        return result

    def model_supports_image(self, model_name: str) -> bool:
        """检查模型是否支持图片识别"""
        cfg = get_model_config(model_name)
        if cfg:
            return cfg.get("supports_image", False)
        return False

    # ---------- 多模型并行对比 ----------
    async def _compare_single_model(
        self,
        message: str,
        model_name: str,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """单个模型的对比调用（用于 compare_models 内部并行）"""
        try:
            result = await self.chat(
                history=[],
                message=message,
                model_name=model_name,
                system_prompt=system_prompt,
            )
            return {
                "model_name": model_name,
                "content": result.get("content", ""),
                "error": None,
                "usage": result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0}),
            }
        except Exception as e:
            logger.error(f"对比模型 {model_name} 调用失败: {e}")
            return {
                "model_name": model_name,
                "content": "",
                "error": str(e),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }

    async def compare_models(
        self,
        message: str,
        model_names: List[str],
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        多模型并行对比
        - 用 asyncio.gather 并行调用多个模型
        - 返回 [{model_name, content, error, usage}]
        """
        if not model_names:
            return []
        logger.info(f"多模型对比 | models={model_names} | message={message[:50]}...")

        tasks = [
            self._compare_single_model(message, name, system_prompt)
            for name in model_names
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        logger.info(f"多模型对比完成 | 数量={len(results)}")
        return results

    # ---------- 工具调用对话（Agent） ----------
    async def chat_with_tools(
        self,
        history: List[Dict[str, str]],
        message: str,
        model_name: Optional[str] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Any]] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        使用工具的对话（非流式）
        - 通过 ChatOpenAI.bind_tools 绑定工具
        - 手动执行工具调用循环（兼容性更好，不依赖 AgentExecutor）
        - 返回 {"content": str, "usage": {...}, "tool_calls": [...]}
        """
        model = model_name or self.default_model
        logger.info(f"工具对话 | model={model} | message={message[:50]}...")

        chat_model = self._create_chat_model(model, streaming=False, temperature=temperature)
        messages = self._build_messages(history, message, system_prompt)

        if not tools:
            # 无工具则退化为普通对话
            result = await self.chat(history, message, model_name, system_prompt, temperature=temperature)
            return {**result, "tool_calls": []}

        # 绑定工具
        bound_model = chat_model.bind_tools(tools)
        # 工具名 -> 工具对象 的映射
        tool_map = {t.name: t for t in tools}

        total_prompt = 0
        total_completion = 0
        tool_calls_log: List[Dict[str, Any]] = []
        max_iterations = 5  # 防止无限循环

        for _ in range(max_iterations):
            response = await bound_model.ainvoke(messages)
            usage = self._extract_usage(response)
            total_prompt += usage["prompt_tokens"]
            total_completion += usage["completion_tokens"]

            # 无工具调用，直接返回
            if not getattr(response, "tool_calls", None):
                content = response.content if isinstance(response.content, str) else str(response.content)
                logger.info(f"工具对话完成 | 无更多工具调用 | usage={total_prompt + total_completion}")
                return {
                    "content": content,
                    "usage": {"prompt_tokens": total_prompt, "completion_tokens": total_completion},
                    "tool_calls": tool_calls_log,
                }

            # 有工具调用：把 AI 消息加入历史，执行工具
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {}) or {}
                logger.info(f"工具调用 | name={tool_name} | args={tool_args}")
                tool_func = tool_map.get(tool_name)
                if tool_func is None:
                    tool_result = f"工具 {tool_name} 不存在"
                else:
                    try:
                        tool_result = await tool_func.ainvoke(tool_args) if hasattr(tool_func, "ainvoke") else tool_func.invoke(tool_args)
                    except Exception as e:
                        tool_result = f"工具执行失败: {e}"
                tool_calls_log.append({
                    "name": tool_name,
                    "args": tool_args,
                    "result": str(tool_result),
                })
                messages.append(ToolMessage(content=str(tool_result), tool_call_id=tc.get("id", "")))

        # 达到最大迭代次数，做一次无工具调用收尾
        logger.warning(f"工具对话达到最大迭代次数 {max_iterations}")
        final_response = await chat_model.ainvoke(messages)
        usage = self._extract_usage(final_response)
        total_prompt += usage["prompt_tokens"]
        total_completion += usage["completion_tokens"]
        content = final_response.content if isinstance(final_response.content, str) else str(final_response.content)
        return {
            "content": content,
            "usage": {"prompt_tokens": total_prompt, "completion_tokens": total_completion},
            "tool_calls": tool_calls_log,
        }
