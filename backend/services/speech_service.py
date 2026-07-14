"""
语音识别服务：基于 DashScope ASR (qwen3-asr-flash)
- ffmpeg 管道将任意音频转为 16kHz mono WAV（全内存，无磁盘 IO）
- 通过 DashScope OpenAI-compatible 端点进行语音识别
"""
import base64
import os
import platform
import subprocess
from typing import Optional

import httpx

from utils.logger import logger

# 指定 ffmpeg 路径
_FFMPEG = "ffmpeg"
if platform.system() == "Windows":
    _dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ffmpeg")
    _exe = os.path.join(_dir, "ffmpeg.exe")
    if os.path.isfile(_exe):
        _FFMPEG = _exe
        os.environ["PATH"] = _dir + os.pathsep + os.environ.get("PATH", "")


class SpeechService:
    """语音识别服务（单例）"""

    def __init__(self) -> None:
        # 从 settings 读取配置（触发 .env 加载）
        from config.settings import settings
        self._api_key: str = settings.custom_api_key or ""
        self._base_url: str = settings.custom_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self._model: str = settings.asr_model

    @property
    def is_available(self) -> bool:
        """检查 ASR 服务是否可用（需配置 CUSTOM_API_KEY）"""
        return bool(self._api_key)

    async def recognize_any(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
        """接收任意格式音频，转为 WAV 后识别"""
        wav_bytes = self._convert_to_wav(audio_bytes, mime_type)
        return await self.recognize(wav_bytes)

    async def recognize(self, wav_bytes: bytes) -> str:
        """识别 WAV 音频，返回文本"""
        data_url = self._encode_data_url(wav_bytes, "audio/wav")
        return await self._call_asr(data_url)

    @staticmethod
    def _convert_to_wav(audio_bytes: bytes, mime_type: str) -> bytes:
        """用 ffmpeg 管道将任意音频转为 16kHz mono WAV（全内存，无磁盘 IO）"""
        ext_map = {
            "audio/webm": "webm",
            "audio/wav": "wav",
            "audio/wave": "wav",
            "audio/ogg": "ogg",
            "audio/mp4": "mp4",
            "audio/mpeg": "mp3",
        }
        ext = ext_map.get(mime_type, "webm")

        cmd = [
            _FFMPEG,
            "-f", ext,
            "-i", "pipe:0",
            "-f", "wav",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-loglevel", "error",
            "pipe:1",
        ]
        try:
            proc = subprocess.run(cmd, input=audio_bytes, capture_output=True, timeout=15)
        except FileNotFoundError:
            raise RuntimeError(
                "ffmpeg 未安装，请安装 ffmpeg 后重试。"
                "Windows: choco install ffmpeg 或从 https://ffmpeg.org 下载。"
            )
        if proc.returncode != 0:
            raise RuntimeError(f"音频转换失败: {proc.stderr.decode()}")
        return proc.stdout

    @staticmethod
    def _encode_data_url(audio_bytes: bytes, mime_type: str) -> str:
        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return f"data:{mime_type};base64,{b64}"

    async def _call_asr(self, data_url: str) -> str:
        """调用 DashScope OpenAI-compatible 端点进行语音识别"""
        if not self._api_key:
            raise RuntimeError("语音识别未配置 CUSTOM_API_KEY，请在 .env 中设置")

        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "audio", "audio": data_url}
                    ],
                }
            ],
        }

        logger.info(f"语音识别请求 | model={self._model}")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                logger.info(f"语音识别完成 | text_length={len(text)}")
                return text.strip()
        except httpx.HTTPError as e:
            logger.error(f"语音识别请求失败: {e}")
            raise RuntimeError(f"语音识别请求失败: {e}") from e
        except (KeyError, IndexError) as e:
            logger.error(f"语音识别响应解析失败: {e}")
            raise RuntimeError(f"语音识别响应解析失败: {e}") from e


# 全局单例
speech_service = SpeechService()
