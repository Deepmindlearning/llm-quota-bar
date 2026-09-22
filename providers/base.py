"""Provider 基类。"""
from __future__ import annotations

from datetime import date

from core.models import Snapshot


class Provider:
    name: str = "?"
    interval: int = 300          # 轮询间隔（秒）
    stale_after: int = 900       # 超过该秒数未更新则置灰

    def fetch(self, cfg: dict) -> Snapshot:  # pragma: no cover - 抽象接口
        raise NotImplementedError


def sub_status_line(expires: str, suffix: str = "") -> str:
    """订阅到期状态行（附进 Snapshot.extra 在明细面板显示）：剩 N 天倒计时；过期后显示已停订。
    expires=YYYY-MM-DD（已关自动续费的已付周期截止日，多为约数）。"""
    try:
        end = date.fromisoformat(expires)
    except ValueError:
        return f"订阅截止 {expires}（日期无法解析）"
    days = (end - date.today()).days
    tail = f"（{suffix}）" if suffix else ""
    if days >= 0:
        return f"订阅至 {expires[5:]}（剩 {days} 天）{tail}"
    return f"订阅已于 {expires[5:]} 到期停订{tail}"
