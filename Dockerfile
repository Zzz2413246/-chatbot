FROM python:3.12-slim

WORKDIR /app

# 安装 ffmpeg（语音识别需要）
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制功能完整的后端代码
COPY backend/ .

EXPOSE 8000

# 使用 backend/src/main.py 作为入口
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
