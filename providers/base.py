"""Provider 基类。"""
from __future__ import annotations

from core.models import Snapshot


class Provider:
    name: str = "?"
    interval: int = 300          # 轮询间隔（秒）
    stale_after: int = 900       # 超过该秒数未更新则置灰

    def fetch(self, cfg: dict) -> Snapshot:  # pragma: no cover - 抽象接口
        raise NotImplementedError
