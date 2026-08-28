"""状态机单测（路线图 1.4）：合法路径可用、非法迁移抛异常。"""

import pytest

from gt_core.pipeline import (
    InvalidStateTransition,
    can_batch_set_status,
    transition_entry,
    transition_project,
)
from gt_core.rpc.models import EntryStatus, ProjectState


class TestProjectStateMachine:
    def test_happy_path(self):
        s = ProjectState.created
        for target in (
            ProjectState.detecting,
            ProjectState.extracted,
            ProjectState.translating,
            ProjectState.reviewing,
            ProjectState.writing_back,
            ProjectState.done,
        ):
            s = transition_project(s, target)
        assert s is ProjectState.done

    def test_review_back_to_translating(self):
        assert transition_project(ProjectState.reviewing, ProjectState.translating) is (
            ProjectState.translating
        )

    def test_illegal_skip(self):
        # 不能从 created 直接到 extracted（跳过 detecting）
        with pytest.raises(InvalidStateTransition):
            transition_project(ProjectState.created, ProjectState.extracted)

    def test_illegal_backwards(self):
        with pytest.raises(InvalidStateTransition):
            transition_project(ProjectState.done, ProjectState.writing_back)

    def test_unknown_pair(self):
        with pytest.raises(InvalidStateTransition):
            transition_project(ProjectState.detecting, ProjectState.done)

    def test_idempotent_same_state(self):
        assert transition_project(ProjectState.extracted, ProjectState.extracted) is (
            ProjectState.extracted
        )


class TestEntryStateMachine:
    def test_happy_path(self):
        s = EntryStatus.PENDING
        for target in (EntryStatus.MACHINE, EntryStatus.EDITED, EntryStatus.CONFIRMED):
            s = transition_entry(s, target)
        assert s is EntryStatus.CONFIRMED

    def test_illegal_backwards(self):
        with pytest.raises(InvalidStateTransition):
            transition_entry(EntryStatus.CONFIRMED, EntryStatus.EDITED)

    def test_illegal_skip(self):
        with pytest.raises(InvalidStateTransition):
            transition_entry(EntryStatus.PENDING, EntryStatus.CONFIRMED)

    def test_idempotent_same_state(self):
        assert transition_entry(EntryStatus.MACHINE, EntryStatus.MACHINE) is (
            EntryStatus.MACHINE
        )


class TestBatchGuard:
    """一键重翻不得覆盖 CONFIRMED（路线图 1.4 的单元测试）。"""

    def test_confirmed_never_batchable(self):
        assert can_batch_set_status(EntryStatus.CONFIRMED, EntryStatus.PENDING) is False
        assert can_batch_set_status(EntryStatus.CONFIRMED, EntryStatus.MACHINE) is False

    def test_non_confirmed_batchable(self):
        assert can_batch_set_status(EntryStatus.MACHINE, EntryStatus.PENDING) is True
        assert can_batch_set_status(EntryStatus.EDITED, EntryStatus.MACHINE) is True

    def test_same_status_not_batchable(self):
        assert can_batch_set_status(EntryStatus.PENDING, EntryStatus.PENDING) is False
