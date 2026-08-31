/**
 * 编辑译文弹窗（M4）：点条目打开，原文全文显示（解决长文本截断）+ 译文编辑 + 保存。
 * 保存走 entries.update，状态推进 PENDING→MACHINE→EDITED（状态机逐级）。
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

  useEffect(() => {
    setText(entry.translation ?? "");
    setErr(null);
  }, [entry]);

  async function handleSave() {
    setSaving(true);
    setErr(null);
    try {
      // 状态机逐级推进：PENDING→MACHINE→EDITED（编辑译文 = 人工 = EDITED）
      if (entry.status === 1) {
        await rpc("entries.update", { id: entry.id, status: 2 });
      }
      await rpc("entries.update", { id: entry.id, translation: text, status: 3 });
      await loadEntries(); // 刷新编辑器
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
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" onClick={handleSave} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
