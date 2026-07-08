"""
会话导出服务
- 导出为 JSON 格式
- 导出为 Markdown 格式
- 导出为 TXT 格式
"""
from typing import List, Dict, Optional


class ExportService:
    """会话导出服务"""

    ROLE_LABELS = {
        "user": "用户",
        "assistant": "助手",
        "system": "系统",
    }

    def export(
        self,
        session: Dict,
        messages: List[Dict],
        fmt: str = "markdown",
    ) -> tuple:
        """
        导出会话
        :param session: 会话信息 dict
        :param messages: 消息列表
        :param fmt: json / markdown / txt
        :return: (content, media_type, file_extension)
        """
        fmt = (fmt or "markdown").lower()
        if fmt == "json":
            return self._export_json(session, messages), "application/json", "json"
        elif fmt == "txt":
            return self._export_txt(session, messages), "text/plain", "txt"
        else:
            return self._export_markdown(session, messages), "text/markdown", "md"

    # ---------- JSON ----------
    def _export_json(self, session: Dict, messages: List[Dict]) -> str:
        import json
        data = {
            "session": session,
            "messages": messages,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    # ---------- Markdown ----------
    def _export_markdown(self, session: Dict, messages: List[Dict]) -> str:
        lines = []
        title = session.get("title", "对话记录")
        lines.append(f"# {title}\n")
        lines.append(f"- **会话ID**: {session.get('session_id', '')}")
        lines.append(f"- **模型**: {session.get('model_name', '')}")
        lines.append(f"- **创建时间**: {session.get('created_at', '')}")
        lines.append(f"- **最后活跃**: {session.get('last_active', '')}")
        lines.append(f"- **消息数**: {session.get('message_count', 0)}\n")
        lines.append("---\n")

        for msg in messages:
            role = msg.get("role", "user")
            label = self.ROLE_LABELS.get(role, role)
            timestamp = msg.get("timestamp", "")
            content = msg.get("content", "")
            lines.append(f"### {label}")
            if timestamp:
                lines.append(f"*{timestamp}*\n")
            lines.append(f"{content}\n")
            if msg.get("image_data"):
                lines.append("*（包含图片附件）*\n")
            lines.append("---\n")

        return "\n".join(lines)

    # ---------- TXT ----------
    def _export_txt(self, session: Dict, messages: List[Dict]) -> str:
        lines = []
        title = session.get("title", "对话记录")
        lines.append(f"会话标题: {title}")
        lines.append(f"会话ID: {session.get('session_id', '')}")
        lines.append(f"模型: {session.get('model_name', '')}")
        lines.append(f"创建时间: {session.get('created_at', '')}")
        lines.append(f"最后活跃: {session.get('last_active', '')}")
        lines.append(f"消息数: {session.get('message_count', 0)}")
        lines.append("=" * 50)
        lines.append("")

        for msg in messages:
            role = msg.get("role", "user")
            label = self.ROLE_LABELS.get(role, role)
            timestamp = msg.get("timestamp", "")
            content = msg.get("content", "")
            header = f"[{label}]"
            if timestamp:
                header += f" {timestamp}"
            lines.append(header)
            lines.append(content)
            if msg.get("image_data"):
                lines.append("（包含图片附件）")
            lines.append("-" * 50)
            lines.append("")

        return "\n".join(lines)
