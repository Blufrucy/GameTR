"""CLI 入口单测：self-test 全流程可运行（M1 验收的自动化形态）。"""

from gt_core.__main__ import run_self_test


def test_self_test_completes():
    """self-test 跑完整流程不抛异常（成功即通过，失败会抛 AssertionError）。"""
    run_self_test()
