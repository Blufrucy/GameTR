/**
 * 翻译操作弹窗（M4 重构）：点「翻译」→ 弹窗显示将翻译的内容 → 确认开始。
 * 开始后关弹窗，进度在底部状态栏（非模态，长任务不锁界面）。
 * 顺带提供「重翻行数不匹配条目」入口（保留原修复功能，不因去掉独立视图而丢失）。
 */

import { useMemo, useState } from "react";
import { useApp } from "../store/app";
import { Button, Modal } from "./ui";

export function TranslateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { entries, translateTask, startTranslate, retranslateMismatched, projectPath } = useApp();
  const [starting, setStarting] = useState(false);

  // 待译 = 尚无译文；行数不匹配 = 机翻把行合并了，回写会跳过原文，需要重翻
  const pending = useMemo(() => entries.filter((e) => e.status === 1).length, [entries]);
  const mismatched = useMemo(
    () => entries.filter((e) => e.translation && e.source.split("\n").length !== e.translation!.split("\n").length).length,
    [entries],
  );
  // 任务真正运行中才拦截；完成后 translateTask 残留 done 状态，应允许再次翻译
  const running = translateTask?.status === "running";

  async function handleStart() {
    setStarting(true);
    await startTranslate();
    setStarting(false);
    onClose(); // 进度转底部状态栏
  }

  async function handleRetranslateMismatched() {
    setStarting(true);
    await retranslateMismatched();
    setStarting(false);
    onClose();
  }

  return (
    <Modal open={open} onClose={onClose} title="翻译">
      <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 420 }}>
        {!projectPath ? (
          <p style={{ margin: 0, fontSize: 13, color: "#8a8a92" }}>请先导入游戏。</p>
        ) : running ? (
          <p style={{ margin: 0, fontSize: 13, color: "#8a8a92" }}>
            已有翻译任务进行中（{translateTask?.done}/{translateTask?.total}），进度见底部状态栏。
          </p>
        ) : (
          <>
            <div style={{ fontSize: 13, lineHeight: 1.7 }}>
              <p style={{ margin: 0 }}>
                将翻译 <strong>{pending}</strong> 条待译文本。
              </p>
              <p style={{ margin: "4px 0 0", fontSize: 12, color: "#8a8a92" }}>
                用「模型 API」里选择的 Provider 批量翻译。开始后可在底部状态栏查看进度。
              </p>
              {mismatched > 0 && (
                <button
                  onClick={handleRetranslateMismatched}
                  disabled={starting}
                  title="机翻偶尔会合并多行原文，导致回写跳过。点此重翻以保持与原文行数一致。"
                  style={{
                    background: "none", border: "none", color: "#ff9f0a",
                    fontSize: 12, cursor: starting ? "default" : "pointer",
                    marginTop: 8, padding: 0, textDecoration: "underline",
                  }}
                >
                  有 {mismatched} 条译文行数与原文不一致，重翻保持行数
                </button>
              )}
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <Button onClick={onClose}>取消</Button>
              <Button variant="primary" onClick={handleStart} disabled={starting || pending === 0}>
                {starting ? "启动中…" : pending > 0 ? `开始翻译 ${pending} 条` : "没有待翻译内容"}
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
