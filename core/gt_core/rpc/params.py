"""RPC 方法参数 pydantic 模型（协议纪律：与 rpc-methods.json 的 params 一致）。

严格模式（extra=forbid）：未知字段按 INVALID_PARAMS 拒绝，防止前后端契约漂移。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gt_core.rpc.models import EntryStatus

_STRICT = ConfigDict(extra="forbid")


class EmptyParams(BaseModel):
    """无参数方法（params:{} 契约）：任何多余字段都被 extra=forbid 拒绝。"""

    model_config = _STRICT


# ---------- project.* ----------

class ProjectCreateParams(BaseModel):
    model_config = _STRICT
    path: str
    engine_id: str
    source_path: str


class ProjectOpenParams(BaseModel):
    model_config = _STRICT
    path: str


class ProjectDefaultPathParams(BaseModel):
    """project.default_path：按游戏目录算默认项目文件路径（前端导入用）。"""

    model_config = _STRICT
    dir: str


# ---------- entries.* ----------

class EntriesListParams(BaseModel):
    model_config = _STRICT
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=200, ge=1, le=2000)
    status: EntryStatus | None = None
    file_path: str | None = None


class EntriesGetParams(BaseModel):
    model_config = _STRICT
    id: str


class EntriesUpdateParams(BaseModel):
    model_config = _STRICT
    id: str
    translation: str | None = None  # 显式 null = 清空译文（协议 ['string','null']）
    status: EntryStatus | None = None  # 仅作「未传」占位；显式 null 会被 validator 拒绝
    edited: int | None = None  # M4 人工修改标记（1=人工编辑过；与 status 正交）

    @field_validator("status")
    @classmethod
    def _status_not_null(cls, v: EntryStatus | None) -> EntryStatus | None:
        """协议 status 是非空枚举：显式传 null 属契约违规（仅「未传」允许 None）。

        未传字段不走 validator（pydantic 只在显式赋值时调用），故不会误伤默认值。
        """
        if v is None:
            raise ValueError("status 不可为 null（如需清空译文请用 translation=null）")
        return v

    @field_validator("edited")
    @classmethod
    def _edited_01(cls, v: int | None) -> int | None:
        if v not in (None, 0, 1):
            raise ValueError("edited 只能是 0 或 1")
        return v


class EntriesBatchStatusParams(BaseModel):
    model_config = _STRICT
    ids: list[str]
    status: EntryStatus


class EntriesSearchParams(BaseModel):
    model_config = _STRICT
    query: str
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=200, ge=1, le=2000)


# ---------- glossary.* ----------

class GlossaryUpsertParams(BaseModel):
    model_config = _STRICT
    term: str
    translation: str
    match_case: bool = False


# ---------- M2：detect / extract / write_back（插件框架） ----------

class DetectRunParams(BaseModel):
    model_config = _STRICT
    dir: str


class ExtractRunParams(BaseModel):
    """extract.run 无参数：source_path 来自当前项目的 meta（协议 params:{}）。"""

    model_config = _STRICT


class WriteBackRunParams(BaseModel):
    model_config = _STRICT
    output_dir: str


# ---------- M3：providers（Provider 层） ----------

class ProviderTestParams(BaseModel):
    model_config = _STRICT
    provider_id: str
    model: str | None = None
    api_key: str | None = None  # 可选覆盖；规范路径是环境变量注入（RPC 日志已脱敏）
    base_url: str | None = None  # 传入 = 测试未保存的临时配置（UI 保存前）


class ProviderModelsParams(BaseModel):
    """providers.models：获取 Provider 可用模型列表（UI「获取模型」按钮）。"""

    model_config = _STRICT
    provider_id: str
    api_key: str | None = None
    base_url: str | None = None  # 传入 = 用临时配置拉模型（UI 保存前）


class ProviderRemoveParams(BaseModel):
    """providers.remove：删除用户 Provider（配置面板「删除」按钮）。"""

    model_config = _STRICT
    provider_id: str


class ProviderConfigureParams(BaseModel):
    """providers.configure：接入真实 Provider（DeepSeek/OpenAI/自定义）。

    api_key 持久化到 ~/.gametr/providers.json（明文，MVP）；正式版 OS keyring。
    RPC 日志对 api_key 字段脱敏（server.py _RpcLogger）。
    """

    model_config = _STRICT
    provider_id: str
    base_url: str
    display_name: str | None = None
    models: list[str] | None = None
    api_key: str | None = None


# ---------- M3：translate（任务管理） ----------

class TranslateStartParams(BaseModel):
    model_config = _STRICT
    scope: Literal["all", "selection", "file"]
    ids: list[str] | None = None
    file_path: str | None = None
    status_filter: EntryStatus | None = None
    provider_id: str
    model: str | None = None
    style_id: str | None = None
    overwrite_confirmed: bool = False  # 重译已确认条目（默认 false，受守卫）


class TranslateTaskParams(BaseModel):
    """pause/resume/cancel/status 共用：{task_id}。"""

    model_config = _STRICT
    task_id: str


class TranslateStatsParams(BaseModel):
    model_config = _STRICT
    task_id: str | None = None  # 缺省取最近任务


class TranslateStatusParams(BaseModel):
    model_config = _STRICT
    task_id: str | None = None  # 缺省取最近任务（sidecar 重启后前端恢复）


class TranslateRetranslateParams(BaseModel):
    model_config = _STRICT
    ids: list[str]
    provider_id: str | None = None
    model: str | None = None
    style_id: str | None = None


class GlossaryDeleteParams(BaseModel):
    model_config = _STRICT
    term: str


class TranslateExportParams(BaseModel):
    """translate.export：导出翻译文件到指定路径（同款游戏用户导入复用）。"""

    model_config = _STRICT
    path: str


class TranslateImportParams(BaseModel):
    """translate.import：从翻译文件导入译文（按 locator 匹配）。"""

    model_config = _STRICT
    path: str
