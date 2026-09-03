"""ProviderManager：内置注册表 + 用户配置 Provider + providers.list/test/configure。

注册：
- mock：确定性伪翻译（测试/演示/CI，UI 不展示）
- openai-compatible：默认（base_url 可被 OPENAI_BASE_URL 覆盖）
- **用户配置**（providers.configure RPC）：接入真实 API（DeepSeek/OpenAI/自定义），
  持久化到 ~/.gametr/providers.json，同一 OpenAI 兼容代码路径

api_key 解析优先级：显式参数 > 环境变量 > 用户配置（providers.configure 存的 key）。
⚠️ 用户配置的 key 明文落 ~/.gametr/providers.json（MVP；正式版 OS keyring → spawn 注入 env）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from gt_core.providers.base import TranslationProvider
from gt_core.providers.openai_compat import OpenAICompatibleProvider
from gt_core.rpc.models import ProviderInfo, ProviderTestResult

# provider_id -> 环境变量（api_key 兜底读取）
_ENV_KEY_MAP = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mock": "",  # mock 不需要
}

# 用户 Provider 配置（providers.configure 持久化）
_CONFIG_FILE = Path.home() / ".gametr" / "providers.json"


def _read_configs() -> list[dict[str, Any]]:
    try:
        data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        return data.get("providers", []) if isinstance(data, dict) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_configs(configs: list[dict[str, Any]]) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(
        json.dumps({"providers": configs}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class ProviderManager:
    def __init__(self) -> None:
        # 只加载用户配置的 Provider（providers.configure 持久化）——无内置默认，
        # 用户先「添加 API」才能翻译（UI 流程：添加 → 测试 → 获取模型 → 选择）
        self._providers: dict[str, TranslationProvider] = {}
        self._config_keys: dict[str, str] = {}
        for cfg in _read_configs():
            self._apply_config(cfg)

    # ---------- 查询 ----------

    def infos(self) -> list[ProviderInfo]:
        return [
            ProviderInfo(
                provider_id=p.provider_id,
                display_name=p.display_name,
                models=p.models,
                needs_api_key=p.needs_api_key,
                supports_structured=p.supports_structured,
                base_url=p.base_url,
            )
            for p in self._providers.values()
        ]

    def get(self, provider_id: str) -> TranslationProvider:
        p = self._providers.get(provider_id)
        if p is None:
            raise KeyError(f"未知 Provider: {provider_id}（可用: {sorted(self._providers)}）")
        return p

    # ---------- 用户配置（接入真实 API） ----------

    def configure(self, *, provider_id: str, base_url: str,
                  display_name: str | None = None, models: list[str] | None = None,
                  api_key: str | None = None) -> ProviderInfo:
        """注册/更新用户 Provider 并持久化。返回 ProviderInfo。"""
        cfg = {
            "provider_id": provider_id,
            "display_name": display_name or provider_id,
            "base_url": base_url,
            "models": models or [],
        }
        if api_key:
            cfg["api_key"] = api_key
        configs = [c for c in _read_configs() if c.get("provider_id") != provider_id]
        configs.append(cfg)
        _write_configs(configs)
        self._apply_config(cfg)
        return self._to_info(self.get(provider_id))

    def remove(self, provider_id: str) -> bool:
        """删除用户 Provider（内存 + 持久化）。返回是否曾存在（幂等删除）。"""
        existed = provider_id in self._providers
        configs = [c for c in _read_configs() if c.get("provider_id") != provider_id]
        _write_configs(configs)
        self._providers.pop(provider_id, None)
        self._config_keys.pop(provider_id, None)
        return existed

    def _apply_config(self, cfg: dict[str, Any]) -> None:
        """把一条用户配置应用到内存（Provider + key 缓存）。"""
        pid = cfg.get("provider_id")
        if not pid:
            return
        self._providers[pid] = OpenAICompatibleProvider(
            provider_id=pid,
            display_name=cfg.get("display_name") or pid,
            base_url=cfg.get("base_url") or "https://api.openai.com/v1",
            models=cfg.get("models") or None,
        )
        if cfg.get("api_key"):
            self._config_keys[pid] = cfg["api_key"]

    @staticmethod
    def _to_info(p: TranslationProvider) -> ProviderInfo:
        return ProviderInfo(
            provider_id=p.provider_id,
            display_name=p.display_name,
            models=p.models,
            needs_api_key=p.needs_api_key,
            supports_structured=p.supports_structured,
            base_url=p.base_url,
        )

    # ---------- api_key 解析 ----------

    def resolve_api_key(self, provider_id: str, explicit: str | None = None) -> str | None:
        """显式参数 > 环境变量 > 用户配置（providers.configure 存的 key）。"""
        if explicit:
            return explicit
        env_key = _ENV_KEY_MAP.get(provider_id)
        if env_key and os.environ.get(env_key):
            return os.environ[env_key]
        return self._config_keys.get(provider_id)

    # ---------- 连通性自检 / 模型获取 ----------

    async def test(self, provider_id: str, *, model: str | None = None,
                   api_key: str | None = None, base_url: str | None = None) -> ProviderTestResult:
        """连通性自检。base_url 传入 = 测试**未保存**的临时配置（UI 保存前），
        不要求已注册；否则用已注册 Provider（key 走 resolve）。"""
        if base_url:
            temp = OpenAICompatibleProvider(provider_id=provider_id, base_url=base_url)
            ok, latency, msg = await temp.test(model=model, api_key=api_key)
        else:
            registered = self.get(provider_id)
            ok, latency, msg = await registered.test(
                model=model, api_key=self.resolve_api_key(provider_id, api_key)
            )
        return ProviderTestResult(provider_id=provider_id, ok=ok, latency_ms=latency, message=msg)

    async def list_models(self, provider_id: str, *, api_key: str | None = None,
                          base_url: str | None = None) -> list[str]:
        """获取模型列表（GET /models，UI「获取模型」按钮）。base_url 同 test：支持临时配置。"""
        if base_url:
            temp = OpenAICompatibleProvider(provider_id=provider_id, base_url=base_url)
            return await temp.list_models(api_key=api_key)
        registered = self.get(provider_id)
        return await registered.list_models(
            api_key=api_key or self.resolve_api_key(provider_id, api_key)
        )
