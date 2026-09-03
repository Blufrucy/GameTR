/**
 * 编辑译文弹窗（M4）：点条目打开，原文全文显示（解决长文本截断）+ 译文编辑 + 保存。
 *
 * 语义（translation 与 machine_text 分工）：
 * - translation = 当前生效译文（机翻或人工改后）
 * - machine_text  = 机翻基线（AI 原文）：人工编辑只改 translation，基线保留——
 *   机翻·已改条目可查看机翻内容、一键「恢复机翻」（edited 归 0）；
 * - 纯人工从待译直填的条目没有基线，可「清空译文」回待译（状态 MACHINE→PENDING）。
 * 键盘：Ctrl+Enter 保存，Esc 取消。
 */

import { useEffect, useState } from "react";
import { rpc } from "../rpc/client";
import { useApp, type EntryRow } from "../store/app";
import { Button, Modal } from "./ui";

export function EditEntryModal({
  entry,
  onClose,
}: {
  entry: EntryRow;
  onClose: () => void;
}) {
  const { loadEntries } = useApp();
  const [text, setText] = useState(entry.translation ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showMachine, setShowMachine] = useState(true);
  const [armClear, setArmClear] = useState(false);

  // 机翻·已改：有机翻基线可看/可恢复（纯机翻未改 / 纯人工直填没有此块）
  const canRevertMachine = entry.status === 2 && entry.edited === 1
    && entry.machine_text != null && entry.machine_text.length > 0;
  // 有当前译文且非已确认 → 可整条清空回待译
  const canClear = entry.translation != null && entry.translation.length > 0
    && entry.status !== 4;

  useEffect(() => {
    setText(entry.translation ?? "");
    setErr(null);
    setShowMachine(true);
    setArmClear(false);
  }, [entry]);

  async function handleSave() {
    const origTranslation = entry.translation ?? "";
    const hasText = text.trim().length > 0;
    // 空/未改 → no-op（保持原状态；想回到机翻用「恢复机翻」，想回到待译用「清空译文」）
    if (!hasText || text === origTranslation) {
      onClose();
      return;
    }
    setSaving(true);
    setErr(null);
    try {
      // 待译填内容 → status 1→2（有译文了）；机翻/已改保持 status（来源不变）
      if (entry.status === 1) {
        await rpc("entries.update", { id: entry.id, status: 2 });
      }
      // 写译文 + 人工修改标记 edited=1（与 status 正交——机翻条目改后仍命中机翻/已修改）
      await rpc("entries.update", { id: entry.id, translation: text, edited: 1 });
      await loadEntries(); // 刷新编辑器
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  /** 恢复机翻：把译文还原为 AI 基线，并清掉「已修改」标记。 */
  async function handleRevertMachine() {
    if (!canRevertMachine || entry.machine_text == null) return;
    setSaving(true);
    setErr(null);
    try {
      await rpc("entries.update", { id: entry.id, translation: entry.machine_text, edited: 0 });
      await loadEntries();
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  /** 清空译文：整条回到待译（MACHINE→PENDING，状态机已放行；已确认不可清）。 */
  async function handleClear() {
    if (!canClear) return;
    setSaving(true);
    setErr(null);
    try {
      await rpc("entries.update", { id: entry.id, translation: null, status: 1, edited: 0 });
      await loadEntries();
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      void handleSave();
    }
    if (e.key === "Escape") onClose();
  }

  return (
    <Modal open onClose={onClose} title={`编辑译文 · ${entry.file_path.split("/").pop() || entry.locator}`}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div>
          <div style={{ fontSize: 12, color: "#8a8a92", marginBottom: 4 }}>原文</div>
          <pre
            style={{
              margin: 0, padding: 10, background: "#161619", border: "1px solid #2a2a2e",
              borderRadius: 6, color: "#e6e6e8", fontSize: 13, whiteSpace: "pre-wrap",
              wordBreak: "break-word", maxHeight: 220, overflow: "auto",
            }}
          >
            {entry.source}
          </pre>
        </div>

        {/* 机翻·已改：显示 AI 基线，可对比/恢复 */}
        {canRevertMachine && (
          <div style={{ border: "1px solid #1f3a55", borderRadius: 6, background: "#101a26", overflow: "hidden" }}>
            <button
              onClick={() => setShowMachine(!showMachine)}
              style={{
                width: "100%", textAlign: "left", background: "none", border: "none",
                color: "#7fb7f0", fontSize: 12, padding: "6px 10px", cursor: "pointer",
                display: "flex", justifyContent: "space-between", alignItems: "center",
              }}
            >
              <span>机翻译文（AI 原文）</span>
              <span>{showMachine ? "收起 ▲" : "展开 ▼"}</span>
            </button>
            {showMachine && (
              <pre
                style={{
                  margin: 0, padding: "0 10px 8px", fontSize: 12, color: "#b8d4f0",
                  whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 140, overflow: "auto",
                }}
              >
                {entry.machine_text}
              </pre>
            )}
            <div style={{ padding: "0 10px 8px" }}>
              <button
                onClick={handleRevertMachine}
                disabled={saving}
                style={{
                  background: "#0f2f4f", border: "1px solid #2d6cdf", color: "#9cc9ff",
                  borderRadius: 6, padding: "4px 12px", fontSize: 12, cursor: "pointer",
                  opacity: saving ? 0.5 : 1,
                }}
              >
                恢复为机翻（撤销我的修改）
              </button>
            </div>
          </div>
        )}

        <div>
          <div style={{ fontSize: 12, color: "#8a8a92", marginBottom: 4 }}>
            译文（Ctrl+Enter 保存）
          </div>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={Math.min(8, Math.max(3, entry.source.split("\n").length + 1))}
            style={{
              width: "100%", background: "#1c1c1f", color: "#e6e6e8", border: "1px solid #3a3a40",
              borderRadius: 6, padding: "8px 10px", fontSize: 13, resize: "vertical",
              whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}
          />
        </div>

        {err && (
          <p style={{ margin: 0, fontSize: 12, color: "#ff5b5b", wordBreak: "break-all" }}>{err}</p>
        )}

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <div>
            {canClear && (
              <button
                onClick={() => {
                  if (!armClear) { setArmClear(true); return; }
                  setArmClear(false);
                  void handleClear();
                }}
                onBlur={() => setArmClear(false)}
                disabled={saving}
                style={{
                  background: "none", border: "none", color: armClear ? "#ff5b5b" : "#8a8a92",
                  fontSize: 12, cursor: "pointer", textDecoration: "underline", padding: "4px 0",
                }}
              >
                {armClear ? "确认清空译文（回到待译）" : "清空译文"}
              </button>
            )}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <Button onClick={onClose}>取消</Button>
            <Button variant="primary" onClick={handleSave} disabled={saving}>
              {saving ? "保存中…" : "保存"}
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
