"""语音识别 API。"""
from fastapi import APIRouter, File, HTTPException, UploadFile

from logger import get_logger
from services.speech_service import speech_service

router = APIRouter(prefix="/api/speech", tags=["speech"])
logger = get_logger(__name__)

MAX_AUDIO_SIZE = 10 * 1024 * 1024  # 10 MB


@router.post("/recognize")
async def recognize(file: UploadFile = File(...)) -> dict:
    """接收音频文件（支持 WAV / WebM / OGG 等格式），自动转换后识别。"""
    audio_bytes = await file.read()
    mime = file.content_type or "audio/wav"

    if len(audio_bytes) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=413, detail="音频文件不能超过 10MB")
    if len(audio_bytes) < 1000:
        raise HTTPException(status_code=400, detail="音频过短")

    logger.info("speech.recognize.request", filename=file.filename, size=len(audio_bytes), mime=mime)
    try:
        text = await speech_service.recognize_any(audio_bytes, mime)
    except Exception as e:
        logger.exception("speech.recognize.error")
        raise HTTPException(status_code=500, detail=f"语音识别失败: {e}")

    return {"text": text}
