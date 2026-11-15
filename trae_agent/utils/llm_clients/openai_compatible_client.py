# Copyright (c) 2026 xiao-chen-ai (embedded-ai project)
# Portions Copyright (c) 2025 ByteDance Ltd. and/or its affiliates (trae-agent, MIT)
# SPDX-License-Identifier: MIT

"""通用 OpenAI 兼容端点客户端：硅基流动 / DeepSeek / vLLM / LM Studio / llama.cpp 等。

任何 chat/completions 协议的服务都可用 provider=openai_compatible + base_url 接入，
复用 openai_compatible_base 的消息解析、工具调用与重试逻辑。
"""

import openai

from trae_agent.utils.config import ModelConfig
from trae_agent.utils.llm_clients.openai_compatible_base import (
    OpenAICompatibleClient,
    ProviderConfig,
)


class GenericOpenAICompatibleProvider(ProviderConfig):
    """通用 OpenAI 兼容端点的 ProviderConfig。"""

    def __init__(self, display_name: str = "OpenAI Compatible"):
        self._display_name = display_name

    def create_client(
        self, api_key: str, base_url: str | None, api_version: str | None
    ) -> openai.OpenAI:
        """创建指向自定义 base_url 的 OpenAI 客户端。"""
        return openai.OpenAI(api_key=api_key, base_url=base_url)

    def get_service_name(self) -> str:
        """重试日志里的服务名。"""
        return self._display_name

    def get_provider_name(self) -> str:
        """轨迹记录里的 provider 名。"""
        return "openai_compatible"

    def get_extra_headers(self) -> dict[str, str]:
        """通用端点无需额外头。"""
        return {}

    def supports_tool_calling(self, model_name: str) -> bool:
        """通用兼容端点默认支持工具调用（不支持时由上层配置 supports_tool_calling=false 关闭）。"""
        return True


class GenericOpenAICompatibleClient(OpenAICompatibleClient):
    """通用 OpenAI 兼容客户端（与 OpenRouter/Doubao 同基类，仅换 ProviderConfig）。"""

    def __init__(self, model_config: ModelConfig):
        if not model_config.model_provider.base_url:
            raise ValueError(
                "openai_compatible provider 需要在配置中提供 base_url"
                "（例如 SiliconFlow: https://api.siliconflow.cn/v1）"
            )
        display = f"OpenAI-Compatible({model_config.model_provider.base_url.split('//')[-1]})"
        super().__init__(model_config, GenericOpenAICompatibleProvider(display))
