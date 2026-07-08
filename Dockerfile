FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY backend-python/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY backend-python/ .
COPY .env .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
