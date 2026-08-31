"""项目与条目状态机（路线图 1.4）。

守卫式设计：非法迁移直接抛异常（`InvalidStateTransition`），防止插件/前端
乱改状态。状态值来自 protocol（ProjectState / EntryStatus），这里是迁移规则。

项目：created → detecting → extracted → translating ⇄ reviewing → writing_back → done
条目：PENDING → MACHINE → EDITED → CONFIRMED（重翻不覆盖 CONFIRMED，见 repo.batch_update_status）
"""

from __future__ import annotations

from enum import Enum

from gt_core.rpc.models import EntryStatus, ProjectState

_T = Enum  # type alias 占位，保持阅读一致（实际用具体 Enum）


class InvalidStateTransition(ValueError):
    """状态迁移不合法（守卫：谁乱改状态都不行）。"""

    def __init__(self, from_state: Enum, to_state: Enum) -> None:
        super().__init__(f"非法状态迁移: {from_state.value} -> {to_state.value}")
        self.from_state = from_state
        self.to_state = to_state


# 项目状态机：允许的 (from, to) 对（translating ⇄ reviewing 双向）
_PROJECT_TRANSITIONS: frozenset[tuple[ProjectState, ProjectState]] = frozenset(
    {
        (ProjectState.created, ProjectState.detecting),
        (ProjectState.detecting, ProjectState.extracted),
        (ProjectState.extracted, ProjectState.translating),
        (ProjectState.translating, ProjectState.reviewing),
        (ProjectState.reviewing, ProjectState.translating),
        (ProjectState.reviewing, ProjectState.writing_back),
        (ProjectState.translating, ProjectState.writing_back),
        # M2：人工在校对器里翻完（不经 AI 流水线）可直接回写——合法路径
        (ProjectState.extracted, ProjectState.writing_back),
        (ProjectState.writing_back, ProjectState.done),
        # M4 重导重置：extract 是幂等操作（默认项目路径固定，重导同一游戏会 open 旧项目），
        # 已提取/已翻译/已回写的项目可重新提取重置回 extracted
        (ProjectState.done, ProjectState.extracted),
        (ProjectState.translating, ProjectState.extracted),
        (ProjectState.reviewing, ProjectState.extracted),
        (ProjectState.writing_back, ProjectState.extracted),
    }
)

# 条目状态机：逐级前进，不可回退（重翻走批量接口，跳过 CONFIRMED）
_ENTRY_TRANSITIONS: frozenset[tuple[EntryStatus, EntryStatus]] = frozenset(
    {
        (EntryStatus.PENDING, EntryStatus.MACHINE),
        (EntryStatus.MACHINE, EntryStatus.EDITED),
        (EntryStatus.EDITED, EntryStatus.CONFIRMED),
    }
)


def transition_project(state: ProjectState, target: ProjectState) -> ProjectState:
    """校验并返回迁移后的项目状态；非法迁移抛 InvalidStateTransition。"""
    if state is target:
        return state  # 幂等：原地保持不算非法
    if (state, target) not in _PROJECT_TRANSITIONS:
        raise InvalidStateTransition(state, target)
    return target


def transition_entry(state: EntryStatus, target: EntryStatus) -> EntryStatus:
    """校验并返回迁移后的条目状态；非法迁移抛 InvalidStateTransition。"""
    if state is target:
        return state
    if (state, target) not in _ENTRY_TRANSITIONS:
        raise InvalidStateTransition(state, target)
    return target


def can_batch_set_status(state: EntryStatus, target: EntryStatus) -> bool:
    """批量状态更新是否允许（重翻场景：CONFIRMED 条目不可被批量改动）。

    批量接口语义与单条不同：允许把状态"往回打"（如重翻 MACHINE→PENDING），
    但 CONFIRMED 是人工确认过的，必须保留（路线图：一键重翻不得覆盖 CONFIRMED）。
    返回 False 的条目会被批量操作跳过。
    """
    if state is EntryStatus.CONFIRMED:
        return False
    return state is not target
