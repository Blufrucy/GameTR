/**
 * GameTR 桌面应用主界面（M4）。
 *
 * 布局：顶部工具栏（导入/翻译/回写/模型API）→ 侧边栏 + 主体（编辑器/翻译）→ 状态栏。
 * 导入 → extract → 编辑器显示真实条目；翻译 → translate.start → progress 通知 → 刷新。
 */

import { useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { ApiKeyModal } from "./components/ApiKeyModal";
import { ActivityBar } from "./components/ActivityBar";
import { EditEntryModal } from "./components/EditEntryModal";
import { MenuBar } from "./components/MenuBar";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { VirtualTable } from "./components/VirtualTable";
import { ensureSubscribed, onNotification, type RpcNotification } from "./rpc/client";
import { useApp, type EntryRow } from "./store/app";
import { Button } from "./components/ui";
import "./App.css";

const STATUS_LABEL: Record<number, string> = { 1: "待译", 2: "机翻", 3: "已改", 4: "已确认" };
const STATUS_CLASS: Record<number, string> = {
  1: "status-pending", 2: "status-machine", 3: "status-edited", 4: "status-confirmed",
};

function Editor() {
  const { entries, entriesLoading, fileFilter, statusFilter, setFileFilter, setStatusFilter } = useApp();
  const [editing, setEditing] = useState<EntryRow | null>(null);
  const visible = entries.filter((e) => {
    if (fileFilter && e.file_path !== fileFilter) return false;
    if (statusFilter !== null && e.status !== statusFilter) return false;
    return true;
  });

  const columns = useMemo<ColumnDef<EntryRow, unknown>[]>(
    () => [
      {
        id: "status",
        header: "状态",
        size: 56,
        cell: (info) => (
          <span className={`status ${STATUS_CLASS[info.row.original.status] ?? "status-pending"}`}>
            {STATUS_LABEL[info.row.original.status] ?? "?"}
          </span>
        ),
      },
      {
        id: "source",
        header: "原文",
        size: 380,
        cell: (info) => <span className="cell-source">{info.row.original.source}</span>,
      },
      {
        id: "translation",
        header: "译文",
        cell: (info) => (
          <span className={info.row.original.translation ? "" : "cell-empty"}>
            {info.row.original.translation || "（未翻译）"}
          </span>
        ),
      },
      {
        id: "location",
        header: "位置",
        size: 220,
        cell: (info) => <span className="cell-loc">{info.row.original.file_path}</span>,
      },
    ],
    [],
  );

  if (entriesLoading && entries.length === 0) {
    return <div className="view-placeholder"><p>加载条目…</p></div>;
  }
  if (entries.length === 0) {
    return (
      <div className="view-placeholder">
        <h2>编辑器</h2>
        <p>点击「导入游戏」选择游戏文件夹开始。</p>
      </div>
    );
  }
  if (visible.length === 0) {
    return (
      <div className="view-placeholder">
        <h2>无匹配条目</h2>
        <p>
          当前筛选下没有条目
          {fileFilter ? `（文件：${fileFilter.split("/").pop()}）` : ""}
          {statusFilter !== null ? "（状态筛选）" : ""}。
        </p>
        {(fileFilter || statusFilter !== null) && (
          <div style={{ marginTop: 12 }}>
            <Button onClick={() => { setFileFilter(null); setStatusFilter(null); }}>
              清除筛选
            </Button>
          </div>
        )}
      </div>
    );
  }
  return (
    <>
      <VirtualTable
        columns={columns}
        data={visible}
        rowHeight={36}
        getRowId={(r) => r.id}
        onRowClick={(r) => setEditing(r)}
        height="100%"
      />
      {editing && <EditEntryModal entry={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function WritebackView() {
  const { sourcePath, writeBack, writeBackResult, setView, entries, retranslateMismatched } = useApp();
  const [outDir, setOutDir] = useState(sourcePath ? `${sourcePath}_zh` : "");
  const [showDetail, setShowDetail] = useState(false);
  // 行数不匹配：译文换行数与原文不一致（回写保留原文）
  const mismatchedCount = useMemo(
    () => entries.filter(
      (e) => e.translation && e.source.split("\n").length !== e.translation.split("\n").length
    ).length,
    [entries],
  );
  const warningList = writeBackResult?.message ? writeBackResult.message.split("; ").filter(Boolean) : [];

  return (
    <div className="view-placeholder">
      <h2>回写</h2>
      <p>把译文写入游戏副本（原游戏不受影响）。输出目录已自动建议为源游戏旁的新文件夹，可修改。</p>
      <div style={{ display: "flex", gap: 8, marginTop: 14, width: 480 }}>
        <input
          value={outDir}
          onChange={(e) => setOutDir(e.target.value)}
          style={{
            flex: 1, background: "#1c1c1f", color: "#e6e6e8", border: "1px solid #3a3a40",
            borderRadius: 6, padding: "6px 10px", fontSize: 13,
          }}
        />
        <Button variant="primary" onClick={() => writeBack(outDir)}>
          开始回写
        </Button>
      </div>

      {writeBackResult && (
        <div style={{ marginTop: 16, textAlign: "center" }}>
          <p style={{ color: "#34c759" }}>{writeBackResult.written_count} 条译文已写入</p>
          {writeBackResult.warning_count > 0 && (
            <>
              <p style={{ color: "#ff9f0a" }}>
                {writeBackResult.warning_count} 条警告（已保留原文）
              </p>
              {warningList.length > 0 && (
                <>
                  <p style={{ fontSize: 12, color: "#8a8a92", maxWidth: 520 }}>
                    {warningList.slice(0, 3).join("；")}
                  </p>
                  {warningList.length > 3 && (
                    <button
                      onClick={() => setShowDetail(!showDetail)}
                      style={{
                        background: "none", border: "none", color: "#4da3ff",
                        fontSize: 12, cursor: "pointer", marginTop: 4,
                      }}
                    >
                      {showDetail ? "收起" : `展开全部 ${warningList.length} 条`}
                    </button>
                  )}
                  {showDetail && (
                    <p style={{ fontSize: 11, color: "#6a6a70", maxWidth: 600, wordBreak: "break-all", whiteSpace: "pre-wrap" }}>
                      {warningList.join("；")}
                    </p>
                  )}
                </>
              )}
            </>
          )}
          <p style={{ fontSize: 12, color: "#8a8a92" }}>输出目录：{writeBackResult.output_dir}</p>
        </div>
      )}

      {mismatchedCount > 0 && (
        <div style={{ marginTop: 14, textAlign: "center" }}>
          <p style={{ color: "#ff9f0a", fontSize: 13 }}>
            {mismatchedCount} 条行数不匹配（原文/译文换行数不一致）
          </p>
          <Button onClick={() => retranslateMismatched()}>
            重新翻译这 {mismatchedCount} 条
          </Button>
        </div>
      )}

      <p style={{ marginTop: 8, fontSize: 12, color: "#5a5a60" }}>
        建议：回写后去输出目录启动游戏，验证译文生效。
      </p>
      <div style={{ marginTop: 12 }}>
        <Button variant="ghost" onClick={() => setView("editor")}>
          返回编辑器
        </Button>
      </div>
    </div>
  );
}
function TranslateView() {
  const { translateTask, statusMessage, setView, entries, retranslateMismatched, startTranslate } = useApp();
  // 行数不匹配：译文换行数与原文不一致（AI 合并行，回写会跳过保留原文）
  const mismatchedCount = useMemo(
    () => entries.filter(
      (e) => e.translation && e.source.split("\n").length !== e.translation.split("\n").length
    ).length,
    [entries],
  );
  // 待译：漏译/失败跳过未落库的条目（续翻会补）
  const pendingCount = useMemo(() => entries.filter((e) => e.status === 1).length, [entries]);
  if (!translateTask) {
    return (
      <div className="view-placeholder">
        <h2>翻译</h2>
        <p>启动 AI 翻译当前项目的文本。</p>
        <div style={{ marginTop: 12 }}>
          <Button variant="primary" onClick={() => startTranslate()}>
            开始翻译
          </Button>
        </div>
      </div>
    );
  }
  const { done, total, status } = translateTask;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const label = status === "done" ? "翻译完成" : status === "error" ? "翻译出错" : "翻译中";

  if (status === "error") {
    return (
      <div className="view-placeholder">
        <h2>{label}</h2>
        <p style={{ color: "#ff5b5b", maxWidth: 520, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {statusMessage ?? "未知错误"}
        </p>
        <p style={{ color: "#8a8a92", fontSize: 12 }}>
          {done} / {total} 条已完成（已完成的不受影响，重新翻译会跳过）
        </p>
      </div>
    );
  }

  return (
    <div className="view-placeholder">
      <h2>{label}</h2>
      <div className="progress-track">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <p>
        {done} / {total} · {pct}%
      </p>
      {status === "done" ? (
        <>
          <p style={{ color: "#8a8a92", fontSize: 12 }}>译文已保存，可以回写到游戏。</p>
          {mismatchedCount > 0 && (
            <p style={{ color: "#ff9f0a", fontSize: 13 }}>
              {mismatchedCount} 条行数不匹配（原文/译文换行数不一致，回写会保留原文）
            </p>
          )}
          {pendingCount > 0 && (
            <p style={{ color: "#ff9f0a", fontSize: 13 }}>
              {pendingCount} 条仍待译（漏译/失败跳过），点「翻译」可补翻
            </p>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            {mismatchedCount > 0 && (
              <Button onClick={() => retranslateMismatched()}>
                重新翻译这 {mismatchedCount} 条
              </Button>
            )}
            <Button variant="primary" onClick={() => setView("writeback")}>
              回写译文到游戏
            </Button>
          </div>
        </>
      ) : (
        <p style={{ color: "#8a8a92", fontSize: 12 }}>
          正在请求翻译服务…（批量较大时单批约 10-60 秒；如遇限流会自动重试）
        </p>
      )}
    </div>
  );
}

export default function App() {
  const { view, loadProviders, setTranslateProgress, importProgress } = useApp();
  const [apiOpen, setApiOpen] = useState(false);

  // 启动即订阅通知（防丢）+ 加载 Provider
  useEffect(() => {
    ensureSubscribed();
    loadProviders().catch(() => {});
    // progress 通知 → 翻译进度 + 完成后刷新条目
    return onNotification((n: RpcNotification) => {
      if (n.method !== "progress") return;
      const p = n.params as Record<string, unknown>;
      if (p.phase !== "translate") return;
      setTranslateProgress(
        String(p.task_id), String(p.status), Number(p.done), Number(p.total),
        p.message as string | null,
      );
    });
  }, [loadProviders, setTranslateProgress]);

  return (
    <div className="app">
      <MenuBar />
      <div className="app-body" style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <ActivityBar onOpenApi={() => setApiOpen(true)} />
        <Sidebar />
        <main className="app-content" style={{ flex: 1, minWidth: 0, display: "flex" }}>
          {view === "editor" && <Editor />}
          {view === "translate" && <TranslateView />}
          {view === "writeback" && <WritebackView />}
          {view === "home" && (
            <div className="view-placeholder">
              <h2>欢迎</h2>
              <p>点击「导入游戏」选择游戏文件夹开始。</p>
            </div>
          )}
        </main>
      </div>
      <StatusBar />
      <ApiKeyModal open={apiOpen} onClose={() => setApiOpen(false)} />

      {/* 导入进度覆盖层 */}
      {importProgress && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 200,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <div style={{
            background: "#1c1c1f", border: "1px solid #3a3a40", borderRadius: 10,
            padding: "24px 32px", width: 340, textAlign: "center",
          }}>
            <h3 style={{ margin: "0 0 16px", fontSize: 14 }}>导入游戏</h3>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${importProgress.pct}%` }} />
            </div>
            <p style={{ margin: "10px 0 0", fontSize: 12, color: "#8a8a92" }}>
              {importProgress.phase}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
