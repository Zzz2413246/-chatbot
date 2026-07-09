"""
预设角色管理服务
- 系统内置预设：从 prompts/presets.py 读取（不入库）
- 自定义预设：存入数据库 presets 表
- list_presets 合并内置 + 自定义预设
- 支持 CRUD：创建、查询、更新、删除（仅自定义预设可改/删）
"""
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Preset
from prompts.presets import PRESETS, DEFAULT_PRESET_ID, get_preset
from utils.logger import logger


class PresetService:
    """预设角色服务（内置 + 自定义）"""

    # ---------- 内置预设辅助 ----------
    def _builtin_list(self) -> List[dict]:
        """获取内置预设列表（用于合并展示）"""
        result = []
        for p in PRESETS.values():
            result.append({
                "id": p["id"],
                "name": p["name"],
                "description": p.get("description", ""),
                "system_prompt": p.get("system_prompt", ""),
                "icon": p.get("icon", "🤖"),
                "is_builtin": True,
                "user_id": None,
            })
        return result

    def _builtin_detail(self, preset_id: str) -> Optional[dict]:
        """获取单个内置预设详情"""
        p = get_preset(preset_id)
        if not p:
            return None
        return {
            "id": p["id"],
            "name": p["name"],
            "description": p.get("description", ""),
            "system_prompt": p.get("system_prompt", ""),
            "icon": p.get("icon", "🤖"),
            "is_builtin": True,
            "user_id": None,
        }

    # ---------- 查询预设列表（合并内置 + 自定义） ----------
    async def list_presets(self, db: AsyncSession, user_id: Optional[int] = None) -> List[dict]:
        """
        列出所有可用预设（内置 + 当前用户的自定义预设）
        :param user_id: 用户 ID；None 则只返回内置预设
        """
        presets = self._builtin_list()

        # 查询该用户的自定义预设
        if user_id is not None:
            stmt = (
                select(Preset)
                .where(Preset.user_id == user_id)
                .order_by(Preset.created_at.desc())
            )
            result = await db.execute(stmt)
            custom = result.scalars().all()
            for p in custom:
                presets.append(p.to_dict())

        return presets

    # ---------- 查询单个预设 ----------
    async def get_preset(self, db: AsyncSession, preset_id: str, user_id: Optional[int] = None) -> Optional[dict]:
        """
        获取预设详情（内置或自定义）
        :param preset_id: 内置预设的字符串 ID 或自定义预设的数字 ID（字符串形式）
        """
        # 1. 先查内置预设
        builtin = self._builtin_detail(preset_id)
        if builtin:
            return builtin

        # 2. 尝试作为自定义预设 ID 查询
        try:
            db_id = int(preset_id)
        except (TypeError, ValueError):
            return None

        stmt = select(Preset).where(Preset.id == db_id)
        result = await db.execute(stmt)
        preset = result.scalar_one_or_none()
        if not preset:
            return None
        # 自定义预设仅所属用户可读
        if user_id is not None and preset.user_id != user_id:
            return None
        return preset.to_dict()

    # ---------- 获取系统提示词（便捷方法） ----------
    async def get_system_prompt(self, db: AsyncSession, preset_id: Optional[str]) -> Optional[str]:
        """根据 preset_id 获取系统提示词；内置优先，回退查数据库"""
        if not preset_id:
            preset_id = DEFAULT_PRESET_ID
        # 内置预设
        builtin = get_preset(preset_id)
        if builtin:
            return builtin.get("system_prompt")
        # 自定义预设
        try:
            db_id = int(preset_id)
        except (TypeError, ValueError):
            return None
        stmt = select(Preset.system_prompt).where(Preset.id == db_id)
        result = await db.execute(stmt)
        row = result.one_or_none()
        return row[0] if row else None

    # ---------- 创建自定义预设 ----------
    async def create_preset(
        self,
        db: AsyncSession,
        user_id: int,
        name: str,
        system_prompt: str,
        description: str = "",
        icon: str = "🤖",
    ) -> dict:
        """创建自定义预设"""
        if not name or not name.strip():
            raise ValueError("预设名称不能为空")
        if not system_prompt or not system_prompt.strip():
            raise ValueError("系统提示词不能为空")

        preset = Preset(
            user_id=user_id,
            name=name.strip()[:64],
            description=(description or "").strip()[:256],
            system_prompt=system_prompt,
            icon=(icon or "🤖")[:16],
            is_builtin=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(preset)
        await db.flush()
        logger.info(f"创建自定义预设 | id={preset.id} | user_id={user_id} | name={name}")
        return preset.to_dict()

    # ---------- 更新自定义预设 ----------
    async def update_preset(
        self,
        db: AsyncSession,
        preset_id: str,
        user_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        system_prompt: Optional[str] = None,
        icon: Optional[str] = None,
    ) -> Optional[dict]:
        """更新自定义预设（仅所属用户可改）"""
        try:
            db_id = int(preset_id)
        except (TypeError, ValueError):
            return None

        stmt = select(Preset).where(Preset.id == db_id)
        result = await db.execute(stmt)
        preset = result.scalar_one_or_none()
        if not preset:
            return None
        if preset.user_id != user_id:
            return None

        if name is not None:
            if not name.strip():
                raise ValueError("预设名称不能为空")
            preset.name = name.strip()[:64]
        if description is not None:
            preset.description = description.strip()[:256]
        if system_prompt is not None:
            if not system_prompt.strip():
                raise ValueError("系统提示词不能为空")
            preset.system_prompt = system_prompt
        if icon is not None:
            preset.icon = (icon or "🤖")[:16]
        preset.updated_at = datetime.utcnow()
        await db.flush()
        logger.info(f"更新自定义预设 | id={preset.id} | user_id={user_id}")
        return preset.to_dict()

    # ---------- 删除自定义预设 ----------
    async def delete_preset(self, db: AsyncSession, preset_id: str, user_id: int) -> bool:
        """删除自定义预设（仅所属用户可删）"""
        try:
            db_id = int(preset_id)
        except (TypeError, ValueError):
            return False

        stmt = select(Preset).where(Preset.id == db_id)
        result = await db.execute(stmt)
        preset = result.scalar_one_or_none()
        if not preset:
            return False
        if preset.user_id != user_id:
            return False
        await db.delete(preset)
        await db.flush()
        logger.info(f"删除自定义预设 | id={preset_id} | user_id={user_id}")
        return True
