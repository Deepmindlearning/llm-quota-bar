"""数据模型：一次配额抓取的快照。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class UsageWindow:
    """一个限速/配额窗口，如 5 小时窗、周窗、月窗。"""

    label: str                       # "5h" / "周" / "月"
    used_percent: float | None = None  # 0-100
    resets_at: float | None = None   # unix 秒
    detail: str = ""                 # 例如 "40/1000 次" 或 "本地估算"

    @property
    def remaining_percent(self) -> float | None:
        if self.used_percent is None:
            return None
        return max(0.0, 100.0 - self.used_percent)


@dataclass
class Snapshot:
    provider: str
    ok: bool = False
    windows: list[UsageWindow] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)   # 附加信息行（加量包、plan 类型等）
    error: str = ""
    fetched_at: float = field(default_factory=time.time)
    # 付费订阅是否有效：True=有；False=免费版/已停订（悬浮条默认隐藏该格）；None=接口未给出（照常显示）
    subscribed: bool | None = None

    @property
    def headline_used(self) -> float | None:
        """最紧张（used 最高）的窗口百分比，用于悬浮条主显示。"""
        vals = [w.used_percent for w in self.windows if w.used_percent is not None]
        return max(vals) if vals else None

    def summary(self) -> str:
        """--once 模式的单行摘要，不含任何凭据。"""
        if not self.ok:
            return f"[{self.provider}] 失败: {self.error}"
        parts = []
        for w in self.windows:
            s = f"{w.label} 剩余 {w.remaining_percent:.0f}%" if w.remaining_percent is not None else f"{w.label} 未知"
            if w.detail:
                s += f" ({w.detail})"
            parts.append(s)
        if self.extra:
            parts.extend(self.extra)
        if self.subscribed is False:
            parts.append("未订阅（悬浮条默认隐藏）")
        return f"[{self.provider}] " + "; ".join(parts)
