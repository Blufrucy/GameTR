/**
 * 回写操作弹窗（M4 重构）：点「回写」→ 弹窗填输出目录 → 开始回写 → 结果显示在弹窗内。
 * 结果本地持有，每次打开重置（不留上次旧结果）。失败信息也在弹窗内给出。
 */

import { useEffect, useState } from "react";
import { useApp, type WriteBackResult } from "../store/app";
import { Button, Modal } from "./ui";

export function WritebackModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { sourcePath, projectPath, writeBack } = useApp();
  const [outDir, setOutDir] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<WriteBackResult | null>(null);
  const [showDetail, setShowDetail] = useState(false);

  useEffect(() => {
    if (open) {
      setOutDir(sourcePath ? `${sourcePath}_zh` : "");
      setBusy(false);
      setErr(null);
      setResult(null);
      setShowDetail(false);
    }
  }, [open, sourcePath]);

  async function handleWrite() {
    const dir = outDir.trim();
    if (!dir) { setErr("请填写输出目录"); return; }
    setBusy(true);
    setErr(null);
    try {
      const res = await writeBack(dir);
      setResult(res);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const warningList = result?.message ? result.message.split("; ").filter(Boolean) : [];

  return (
    <Modal open={open} onClose={onClose} title="回写译文到游戏副本">
      <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 460 }}>
        {!projectPath ? (
          <p style={{ margin: 0, fontSize: 13, color: "#8a8a92" }}>请先导入游戏。</p>
        ) : (
          <>
            <p style={{ margin: 0, fontSize: 13 }}>
              把已翻译的译文写入游戏副本（原游戏不受影响）。输出目录默认取源游戏旁的「{`${sourcePath?.split(/[\\/]/).pop() ?? ""}_zh`}」，可改。
            </p>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                value={outDir}
                onChange={(e) => setOutDir(e.target.value)}
                disabled={busy}
                style={{
                  flex: 1, background: "#1c1c1f", color: "#e6e6e8", border: "1px solid #3a3a40",
                  borderRadius: 6, padding: "6px 10px", fontSize: 13,
                }}
              />
            </div>

            {err && (
              <p style={{ margin: 0, fontSize: 12, color: "#ff5b5b", wordBreak: "break-all" }}>{err}</p>
            )}

            {result && (
              <div style={{ fontSize: 13 }}>
                <p style={{ margin: 0, color: "#34c759" }}>
                  完成：{result.written_count} 条译文已写入
                </p>
                <p style={{ margin: "2px 0 0", fontSize: 11, color: "#6a6a70", wordBreak: "break-all" }}>
                  {result.output_dir}
                </p>
                {result.warning_count > 0 && (
                  <>
                    <p style={{ margin: "8px 0 0", color: "#ff9f0a" }}>
                      {result.warning_count} 条警告（已保留原文，未写入）
                    </p>
                    {warningList.length > 0 && (
                      <>
                        <p style={{ margin: "4px 0 0", fontSize: 11, color: "#8a8a92" }}>
                          {warningList.slice(0, 2).join("；")}
                        </p>
                        {warningList.length > 2 && (
                          <button
                            onClick={() => setShowDetail(!showDetail)}
                            style={{
                              background: "none", border: "none", color: "#4da3ff",
                              fontSize: 11, cursor: "pointer", marginTop: 2, padding: 0,
                            }}
                          >
                            {showDetail ? "收起" : `展开全部 ${warningList.length} 条`}
                          </button>
                        )}
                        {showDetail && (
                          <p style={{
                            margin: "4px 0 0", fontSize: 10, color: "#6a6a70",
                            maxHeight: 140, overflow: "auto", whiteSpace: "pre-wrap",
                            wordBreak: "break-all",
                          }}>
                            {warningList.join("；")}
                          </p>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 4 }}>
              <Button onClick={onClose}>{result ? "完成" : "取消"}</Button>
              {!result && (
                <Button variant="primary" onClick={handleWrite} disabled={busy}>
                  {busy ? "回写中…" : "开始回写"}
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
