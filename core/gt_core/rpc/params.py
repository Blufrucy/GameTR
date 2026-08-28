"""RPC 方法参数 pydantic 模型（协议纪律：与 rpc-methods.json 的 params 一致）。

严格模式（extra=forbid）：未知字段按 INVALID_PARAMS 拒绝，防止前后端契约漂移。
"""

from __future__ import annotations

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

    @field_validator("status")
    @classmethod
    def _status_not_null(cls, v: EntryStatus | None) -> EntryStatus | None:
        """协议 status 是非空枚举：显式传 null 属契约违规（仅「未传」允许 None）。

        未传字段不走 validator（pydantic 只在显式赋值时调用），故不会误伤默认值。
        """
        if v is None:
            raise ValueError("status 不可为 null（如需清空译文请用 translation=null）")
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
