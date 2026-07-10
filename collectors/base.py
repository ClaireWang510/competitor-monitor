"""
采集器基类 —— 所有 Collector 的统一接口
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from loguru import logger

from config.competitors import SourceConfig
from models.data_models import RawItem


class BaseCollector(ABC):
    """采集器抽象基类"""

    @abstractmethod
    async def collect(self, source: SourceConfig, **kwargs) -> List[RawItem]:
        """
        从指定数据源采集数据，返回原始条目列表。
        子类必须实现此方法。
        """
        ...

    async def safe_collect(self, source: SourceConfig, **kwargs) -> List[RawItem]:
        """带异常保护的采集入口，避免单个源失败导致整体中断"""
        try:
            items = await self.collect(source, **kwargs)
            logger.info(
                f"[{self.__class__.__name__}] {source.name}: 采集到 {len(items)} 条"
            )
            return items
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] {source.name} 采集失败: {e}")
            return []
