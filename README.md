# 科普助手 · 科科 🚀

一个基于 AI 大模型的智能科普聊天机器人，用通俗易懂的方式讲解科学知识。

## ✨ 功能

- 🤖 **AI 智能对话** — 支持流式输出（SSE）和 WebSocket，打字机效果实时响应
- 🎭 **多种人设预设** — 科科（通用科普）、物理学家、生物学家、天文学家、化学家
- 🔄 **多模型切换** — DeepSeek / OpenAI GPT / 通义千问，运行时热切换
- 🖼 **图片识别** — 支持多模态模型识别图片内容
- 📝 **会话管理** — 自动生成标题、搜索历史、导出对话（JSON/Markdown/TXT）
- 🔐 **微信登录** — 支持微信扫码登录和小程序登录
- 🐳 **Docker 部署** — 一键启动前端 + 后端

## 🛠 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python · FastAPI · LangChain · SQLAlchemy (async) |
| 前端 | Vue 3 (CDN) · 原生 CSS · Marked.js |
| 数据库 | SQLite (aiosqlite) |
| LLM | DeepSeek / OpenAI / 通义千问 (OpenAI 兼容 API) |
| 部署 | Docker · Nginx · Uvicorn |

## 🚀 快速启动

### 本地开发

```bash
# 1. 启动后端
cd backend
pip install -r requirements.txt
python -m src.main

# 2. 启动前端（另一个终端）
cd frontend
python -m http.server 80
```

访问 `http://localhost` 即可使用。

### Docker 部署

```bash
docker-compose up -d
```

- 前端 Nginx → `http://localhost:80`
- 后端 API → `http://localhost:8000`

## 📡 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 非流式对话 |
| POST | `/api/chat/stream` | SSE 流式对话 |
| POST | `/api/chat/image` | 图片识别 |
| WS | `/api/chat/ws` | WebSocket 对话 |
| GET | `/api/sessions` | 会话列表 |
| POST | `/api/session` | 创建会话 |
| GET | `/api/models` | 可用模型列表 |
| GET | `/api/presets` | 预设人设列表 |

完整文档见 `/docs`（Swagger UI）。

## 📁 项目结构

```
├── backend/            # 主 Python 后端（FastAPI + LangChain）
│   ├── src/main.py     # 应用入口，所有 API 路由
│   ├── config/         # 配置管理（pydantic-settings）
│   ├── services/       # LLM、会话、认证、导出服务
│   ├── database/       # SQLAlchemy 模型与数据库初始化
│   ├── prompts/        # 预设人设 Prompt 模板
│   └── utils/          # 日志工具
├── frontend/           # Vue 3 前端（纯静态）
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── docker-compose.yml  # Docker 编排
├── Dockerfile          # 后端镜像构建
└── nginx.conf          # Nginx 反向代理配置
```

## 🔧 环境变量

在 `backend/.env` 或项目根目录 `.env` 中配置：

```env
API_KEY=your-api-key          # DeepSeek API Key
BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-chat
MAX_TOKENS=2048
TEMPERATURE=0.7
WECHAT_WEB_APPID=             # 微信开放平台 AppID（可选）
WECHAT_WEB_SECRET=            # 微信开放平台 Secret（可选）
```

## 📄 License

MIT
