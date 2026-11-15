# Xiaochen-AI

**An open-source AI coding assistant for embedded development — hand your Keil projects, serial ports, and flashers to the AI.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Chinese-first workflow · SiliconFlow / DeepSeek / OpenAI / Ollama · Native Windows (GBK-aware) · Bundled Web UI

[中文](README.md) | English

## What is this

A locally-deployed AI coding agent purpose-built for **embedded / MCU development**:

- **Keil command-line build & flash** — dedicated `keil_build_flash` tool with UV4 single-instance deadlock prevention, 180s timeout guard, artifact validation with auto-retry (handles the "0 errors but no .axf" trap), GBK log parsing
- **GBK source files just work** — reads and edits Chinese-commented firmware sources (ALIENTEK/Wildfire examples) without conversion
- **Multi-provider** — SiliconFlow / DeepSeek / OpenAI / Anthropic / Ollama (local); configure via Web UI, fetch your model list directly with the API key
- **Toolchain auto-detection** — scans Keil, J-Link, CMSIS-DAP (ATK-HSWLDBG), OpenOCD, ARM GCC, STM32CubeProg and more across C/D/E drives + registry + PATH; download links for missing ones
- **39 injected skills** — 12 embedded-debugging skills (serial/CAN/Keil/OpenOCD) + 27 engineering-methodology skills (TDD, code review, diagnosis)
- **Local Web UI** — dark theme (customizable colors), streaming chat, workspace file tree (upload/download/delete), collapsible thinking process
- **100% local** — binds 127.0.0.1 only; API keys never leave your machine

## Quick start

```bash
git clone https://github.com/<your-username>/xiaochen-ai.git
cd xiaochen-ai
uv sync
uv run python -m webui --port 8788
# open http://127.0.0.1:8788
```

On Windows, just double-click `启动小臣AI-管理员.cmd` (auto-installs uv + Python + deps).

**First-time setup:** ⚙ gear in the top-right → paste your API key → click "✦ Fetch" to pick a model; then ⚙ → Tool paths → "⚡ Auto-detect all" → Save. Drop your Keil project folder into the left panel and start chatting.

## Typical workflow

```
Upload project → chat to modify code → "build & flash"
→ serial monitor → iterate on real hardware
```

## Architecture

Built with:
- Chinese-first system prompt & intent classification (Greetings answered directly — no more 40-step rampages on "你好")
- `openai_compatible` generic provider (SiliconFlow, vLLM, any OpenAI-style endpoint)
- Windows-native bash tool (echo scrubbing, GBK aware) & editor (GBK fallback)
- Starlette Web UI with SSE streaming + thread-based agent execution

See [README.md](README.md) (Chinese) for full details.

## License

MIT License (c) xiao-chen-ai.

**Credits**: inspired by [trae-agent](https://github.com/bytedance/trae-agent), [embeddedskills](https://github.com/zhinkgit/embeddedskills), [mattpocock/skills](https://github.com/mattpocock/skills) (all MIT, see LICENSE).
