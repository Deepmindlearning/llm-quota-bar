#!/bin/bash
# LLM quota bar launcher for macOS (double-click in Finder)
cd "$(dirname "$0")"
exec ./.venv/bin/python main.py
