"""ContextBatcher（路线图 3.1）：同文件条目分组 + 上下文。

分组语义引擎无关（机制在核心，语义在插件侧 context_json）：
- 按 file_path 分桶（RPGMV = 同 Map/CommonEvent，M6 Ren'Py = 同场景/对话）
- 桶内按 context_json.order 排序（"前一条已确认译文 few-shot"的顺序来源）
- **条目数 + 总字符双上限**（RPGMV 文本短按条数，Ren'Py 段落长按字符防爆 context）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from gt_core.project.repo import Repo
from gt_core.rpc.models import Entry, EntryStatus

# 批上限：40 条 / 2000 字符（路线图 3.1：同 Map/CommonEvent 条目同批，上限 40 条/批）
_DEFAULT_MAX_ITEMS = 40
_DEFAULT_MAX_CHARS = 2000


@dataclass
class Batch:
    """一批待翻译条目 + 上下文。items 已按 order 排序。"""

    file_path: str
    entries: list[Entry] = field(default_factory=list)
    speaker: str | None = None  # 批说话人（多说话人时取第一个；RPGMV 101 头像名）
    few_shot: list[tuple[str, str]] = field(default_factory=list)  # [(source, translation)]

    @property
    def ids(self) -> list[str]:
        return [e.id for e in self.entries]


def _ctx(e: Entry) -> dict[str, Any]:
    try:
        parsed = json.loads(e.context_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _order(e: Entry) -> int:
    o = _ctx(e).get("order")
    return o if isinstance(o, int) else 0


def make_batches(entries: list[Entry], *, max_items: int = _DEFAULT_MAX_ITEMS,
                 max_chars: int = _DEFAULT_MAX_CHARS) -> list[Batch]:
    """分组：**全局贪心合并**——按 (file_path, order) 排序后累积切块，条目数+总字符双上限。

    为何不按文件严格分桶（原实现）：真实游戏大量小文件（Map 只有 2-10 条），
    每文件一批 → 批次爆炸（2061 条/60 文件 → 60+ 批）+ 每批一次 API 调用，
    又慢又易触发限流。贪心合并让小文件拼成大批（≤40 条/2000 字符），
    同文件条目仍相邻（排序保证，few-shot 上下文稳定）。
    """
    ordered = sorted(entries, key=lambda e: (_ctx(e).get("file_path", ""), _order(e)))
    batches: list[Batch] = []
    cur = Batch(file_path=_ctx(ordered[0]).get("file_path", "") if ordered else "")
    cur_chars = 0
    for e in ordered:
        need = len(e.source)
        if cur.entries and (len(cur.entries) >= max_items or cur_chars + need > max_chars):
            batches.append(cur)
            cur = Batch(file_path=_ctx(e).get("file_path", ""))
            cur_chars = 0
        cur.entries.append(e)
        cur_chars += need
    if cur.entries:
        batches.append(cur)
    return batches


def fill_speaker_and_few_shot(batches: list[Batch], repo: Repo | None) -> None:
    """批量填充说话人（context_json.speaker）+ few-shot（同文件已确认译文）。

    单独一步：查询依赖 repo（Few-shot 取该文件第一条 CONFIRMED 译文做示例），
    便于与纯分组逻辑解耦测试。
    """
    for b in batches:
        speakers = [s for e in b.entries if isinstance((s := _ctx(e).get("speaker")), str)]
        b.speaker = speakers[0] if speakers else None
        if repo is not None:
            b.few_shot = _confirmed_examples(repo, b.file_path)


def _confirmed_examples(repo: Repo, file_path: str, limit: int = 1) -> list[tuple[str, str]]:
    """取该文件已确认（CONFIRMED）条目的 (source, translation) 作 few-shot 示例。"""
    page = repo.list_entries(file_path=file_path, page=1, page_size=limit)
    out: list[tuple[str, str]] = []
    for e in page.items:
        if e.translation and e.status == EntryStatus.CONFIRMED:
            out.append((e.source, e.translation))
    return out
