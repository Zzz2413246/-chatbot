"""LLM 服务：封装 LangChain 与模型交互。

提供流式 / 非流式对话、图片识别、自动标题生成等能力。
全链路异步，使用 asyncio.timeout 与 tenacity 做超时与重试。
"""
import asyncio
import base64
from typing import AsyncIterator, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import get_settings
from logger import get_logger

settings = get_settings()
logger = get_logger(__name__)

# 重试装饰器：针对可恢复异常重试 3 次
_RETRY_CONFIG = dict(
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)


class LLMService:
    """LLM 服务。"""

    def __init__(self) -> None:
        self._default_model = settings.model_name
        self._max_tokens = settings.max_tokens
        self._temperature = settings.temperature

    def _get_model(
        self,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        streaming: bool = False,
    ) -> ChatOpenAI:
        """构造 ChatOpenAI 实例，根据模型名自动选择对应的 API 提供商。"""
        target_model = model_name or self._default_model
        provider = settings.get_provider_config(target_model)

        return ChatOpenAI(
            model=target_model,
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            temperature=temperature if temperature is not None else self._temperature,
            max_tokens=max_tokens or self._max_tokens,
            streaming=streaming,
            timeout=60,
            max_retries=2,
        )

    @staticmethod
    def _build_messages(
        messages: List[dict], system_prompt: Optional[str] = None
    ) -> list:
        """把数据库消息记录转换为 LangChain 消息对象列表。

        messages: [{"role": "user"/"assistant"/"system", "content": "...", "image_url": "..."}]
        """
        result: list = []
        if system_prompt:
            result.append(SystemMessage(content=system_prompt))

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            image_url = msg.get("image_url")

            if role == "system":
                result.append(SystemMessage(content=content))
            elif role == "assistant":
                result.append(AIMessage(content=content))
            else:
                # 用户消息，可能带图片
                if image_url:
                    result.append(
                        HumanMessage(
                            content=[
                                {"type": "text", "text": content or "请描述这张图片。"},
                                {"type": "image_url", "image_url": {"url": image_url}},
                            ]
                        )
                    )
                else:
                    result.append(HumanMessage(content=content))
        return result

    @retry(**_RETRY_CONFIG)
    async def chat(
        self,
        messages: List[dict],
        model_name: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """非流式对话，返回完整回复文本。"""
        model = self._get_model(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=False,
        )
        lc_messages = self._build_messages(messages, system_prompt)

        logger.info(
            "llm.chat.start",
            model=model_name or self._default_model,
            message_count=len(lc_messages),
        )
        try:
            async with asyncio.timeout(120):
                response: AIMessage = await model.ainvoke(lc_messages)
            text = response.content if isinstance(response.content, str) else str(response.content)
            logger.info("llm.chat.done", length=len(text))
            return text
        except asyncio.TimeoutError:
            logger.error("llm.chat.timeout")
            raise
        except Exception:
            logger.exception("llm.chat.error")
            raise

    @retry(**_RETRY_CONFIG)
    async def chat_stream(
        self,
        messages: List[dict],
        model_name: Optional[str] = None,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """流式对话，逐块返回文本内容。"""
        model = self._get_model(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=True,
        )
        lc_messages = self._build_messages(messages, system_prompt)

        logger.info(
            "llm.stream.start",
            model=model_name or self._default_model,
            message_count=len(lc_messages),
        )
        try:
            async with asyncio.timeout(120):
                async for chunk in model.astream(lc_messages):
                    if not isinstance(chunk, AIMessage):
                        continue
                    piece = chunk.content
                    if isinstance(piece, str):
                        if piece:
                            yield piece
                    else:
                        # 部分 SDK 在流式中返回 list/dict
                        text = ""
                        if isinstance(piece, list):
                            for item in piece:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text += item.get("text", "")
                                elif isinstance(item, str):
                                    text += item
                        if text:
                            yield text
            logger.info("llm.stream.done")
        except asyncio.TimeoutError:
            logger.error("llm.stream.timeout")
            raise
        except Exception:
            logger.exception("llm.stream.error")
            raise

    async def recognize_image(
        self,
        image_bytes: bytes,
        prompt: str = "请详细描述这张图片的内容。",
        model_name: Optional[str] = None,
        mime_type: str = "image/jpeg",
    ) -> str:
        """图片识别（多模态）。把图片编码为 data URL 后走多模态消息。"""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64}"

        messages = [
            {
                "role": "user",
                "content": prompt,
                "image_url": data_url,
            }
        ]
        # 图片识别建议使用 gpt-4o 系列，但允许调用方指定
        target_model = model_name or "gpt-4o-mini"
        logger.info("llm.image.recognize", model=target_model, mime=mime_type, size=len(image_bytes))
        return await self.chat(messages=messages, model_name=target_model)

    @retry(**_RETRY_CONFIG)
    async def generate_title(self, first_user_message: str, model_name: Optional[str] = None) -> str:
        """基于第一条用户消息自动生成简短会话标题。"""
        title_prompt = (
            "请根据用户的输入生成一个简短的对话标题（不超过 12 个字，不要使用引号、书名号等符号）。"
            "只返回标题文本本身，不要任何额外说明。\n\n"
            f"用户输入：{first_user_message}"
        )
        messages = [{"role": "user", "content": title_prompt}]
        try:
            async with asyncio.timeout(30):
                title = await self.chat(
                    messages=messages,
                    model_name=model_name or self._default_model,
                    temperature=0.3,
                    max_tokens=30,
                )
            # 清理标题
            title = title.strip().strip("\"'""''「」【】").strip()
            if len(title) > 30:
                title = title[:30]
            logger.info("llm.title.generated", title=title)
            return title or "新对话"
        except Exception:
            logger.warning("llm.title.fallback", reason="generation failed")
            # 回退：取用户输入前 12 字
            fallback = first_user_message.strip()[:12]
            return fallback or "新对话"


# 全局单例
llm_service = LLMService()
