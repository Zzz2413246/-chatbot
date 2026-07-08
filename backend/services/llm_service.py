"""
LLM 服务
- 基于 LangChain ChatOpenAI
- 支持多模型运行时切换
- 流式输出（streaming）
- 超时与重试机制（仅重试连接错误，不重试超时）
- 图片识别（多模态，base64 图片）
- 异步架构 + HTTP 连接池复用
- 上下文长度管理（截断旧消息）
"""
from typing import AsyncGenerator, List, Dict, Optional, Any

import httpx
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    BaseMessage,
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
_shared_http_client: Optional[httpx.AsyncClient] = None


def _get_http_client() -> httpx.AsyncClient:
    """获取共享的 httpx AsyncClient，复用 TCP/TLS 连接"""
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = httpx.AsyncClient(
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
    return _shared_http_client


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
            http_client=_get_http_client(),  # 复用连接池
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
    ) -> str:
        """非流式对话，返回完整回复"""
        model = model_name or self.default_model
        logger.info(f"非流式对话 | model={model} | message={message[:50]}...")

        chat_model = self._create_chat_model(model, streaming=False, temperature=temperature)
        messages = self._build_messages(history, message, system_prompt, image_data)

        response = await chat_model.ainvoke(messages)
        content = response.content if isinstance(response.content, str) else str(response.content)
        logger.info(f"非流式对话完成 | 响应长度={len(content)}")
        return content

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
    ) -> AsyncGenerator[str, None]:
        """流式对话，逐块产出文本"""
        model = model_name or self.default_model
        logger.info(f"流式对话 | model={model} | message={message[:50]}...")

        chat_model = self._create_chat_model(model, streaming=True, temperature=temperature)
        messages = self._build_messages(history, message, system_prompt, image_data)

        async for chunk in chat_model.astream(messages):
            if chunk.content:
                text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
                yield text

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
            title = await self.chat(
                history=[],
                message=first_message,
                model_name=model,
                system_prompt=system_prompt,
                temperature=0.3,
            )
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
