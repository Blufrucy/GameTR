/**
 * 翻译操作弹窗（M4 重构）：点「翻译」→ 弹窗显示将翻译的内容 → 确认开始。
 * 开始后关弹窗，进度在底部状态栏（非模态，长任务不锁界面）。
 * 顺带提供「重翻行数不匹配条目」入口（保留原修复功能，不因去掉独立视图而丢失）。
 */

import { useMemo, useState } from "react";
import { useApp } from "../store/app";
import { Button, Modal } from "./ui";

export function TranslateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const {
    entries, translateTask, startTranslate, retranslateMismatched, projectPath,
    selectedIds, clearSelection,
  } = useApp();
  const [starting, setStarting] = useState(false);

  // 待译 = 尚无译文；行数不匹配 = 机翻把行合并了，回写会跳过原文，需要重翻
  const pending = useMemo(() => entries.filter((e) => e.status === 1).length, [entries]);
  const mismatched = useMemo(
    () => entries.filter((e) => e.translation && e.source.split("\n").length !== e.translation!.split("\n").length).length,
    [entries],
  );
  // 勾选范围：只翻所选（后端对勾选里「已有译文/已确认」的条目会跳过 → 待译数才是实际量）
  const hasSelection = selectedIds.size > 0;
  const selectedPending = useMemo(
    () => entries.filter((e) => e.status === 1 && selectedIds.has(e.id)).length,
    [entries, selectedIds],
  );
  const scopePending = hasSelection ? selectedPending : pending;
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
              {hasSelection ? (
                <>
                  <p style={{ margin: 0 }}>
                    将翻译你勾选的 <strong>{selectedPending}</strong> 条待译文本。
                  </p>
                  {selectedPending < selectedIds.size && (
                    <p style={{ margin: "4px 0 0", fontSize: 12, color: "#8a8a92" }}>
                      勾选中其余 {selectedIds.size - selectedPending} 条已有译文/已确认，会自动跳过。
                    </p>
                  )}
                  <p style={{ margin: "4px 0 0", fontSize: 12, color: "#8a8a92" }}>
                    点行首方块勾片段、左侧勾整个文件；点「清除勾选」可改回翻全部待译。
                  </p>
                </>
              ) : (
                <>
                  <p style={{ margin: 0 }}>
                    将翻译全部 <strong>{pending}</strong> 条待译文本。
                  </p>
                  <p style={{ margin: "4px 0 0", fontSize: 12, color: "#8a8a92" }}>
                    只想翻部分文件/片段？先点左侧文件前的方块或表格行首方块勾选，再打开本弹窗。
                  </p>
                </>
              )}
              <p style={{ margin: "4px 0 0", fontSize: 12, color: "#8a8a92" }}>
                用「模型 API」里启用的 Provider 批量翻译。开始后可在底部状态栏查看进度。
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
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <div>
                {hasSelection && (
                  <button
                    onClick={clearSelection}
                    disabled={starting}
                    style={{
                      background: "none", border: "none", color: "#9cc9ff", fontSize: 12,
                      cursor: starting ? "default" : "pointer", padding: 0, textDecoration: "underline",
                    }}
                  >
                    清除勾选（改翻全部）
                  </button>
                )}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <Button onClick={onClose}>取消</Button>
                <Button variant="primary" onClick={handleStart} disabled={starting || scopePending === 0}>
                  {starting ? "启动中…"
                    : scopePending > 0
                      ? (hasSelection ? `翻译所选 ${scopePending} 条` : `开始翻译 ${scopePending} 条`)
                      : (hasSelection ? "勾选中没有待译内容" : "没有待翻译内容")}
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
