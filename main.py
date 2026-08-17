"""LLM 会员余量桌面悬浮条。

用法：
    python main.py            启动悬浮条 + 托盘
    python main.py --once     无界面抓一轮并打印（验证用，不输出任何凭据）
"""
from __future__ import annotations

import argparse
import sys

from core.config import load_config
from core.models import Snapshot
from providers.base import Provider
from providers.chatgpt import ChatGPTProvider
from providers.claude import ClaudeProvider
from providers.grok import GrokProvider
from providers.kimi import KimiProvider
from providers.qwen import QwenProvider

# Qoder 暂停使用（2026-08-17 起）：恢复时在上方加回
# `from providers.qoder import QoderProvider` 并放进下面列表即可，providers/qoder.py 保留未删。


def build_providers(cfg: dict | None = None) -> list[Provider]:
    providers: list[Provider] = [ClaudeProvider(), KimiProvider(), ChatGPTProvider(), GrokProvider()]
    # 百炼 Coding Plan 按需启用（config.local.toml 里 [qwen] enabled = true）
    if (cfg or {}).get("qwen", {}).get("enabled"):
        providers.append(QwenProvider())
    return providers


def run_once() -> int:
    cfg = load_config()
    for p in build_providers(cfg):
        snap = p.fetch(cfg)
        print(snap.summary())
    return 0


def run_gui() -> int:
    from PySide6.QtWidgets import QApplication

    from ui.bar import MonitorBar

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    cfg = load_config()
    bar = MonitorBar(build_providers(cfg), cfg)
    bar.show()
    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 会员余量桌面悬浮条")
    parser.add_argument("--once", action="store_true", help="抓一轮打印后退出（不输出凭据）")
    args = parser.parse_args()
    if args.once:
        return run_once()
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
