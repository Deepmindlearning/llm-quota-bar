"""配置与凭据辅助。

纪律（遵 AGENTS.md）：token/cookie 运行时现读现用，不回显、不写日志；
写回凭据文件一律原子替换，避免与各家 CLI 并发写冲突。
"""
from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.local.toml"

HTTP_TIMEOUT = 15.0


def load_config() -> dict:
    if CONFIG_PATH.is_file():
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


def read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def atomic_write_json(path: Path, data: dict) -> None:
    """写临时文件后 os.replace，避免与 CLI 并发写造成截断。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def home() -> Path:
    return Path.home()
