/**
 * GameTR 主界面（M4 重构）：主界面 = 编辑器（校对文本的地方）。
 * 翻译 / 回写 / 模型 API 都是「按钮 → 弹窗 → 确认」操作，不占独立视图。
 */

import { useEffect, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { ApiKeyModal } from "./components/ApiKeyModal";
import { EditEntryModal } from "./components/EditEntryModal";
import { MenuBar } from "./components/MenuBar";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { TranslateModal } from "./components/TranslateModal";
import { VirtualTable } from "./components/VirtualTable";
import { WritebackModal } from "./components/WritebackModal";
import { Button } from "./components/ui";
import { ensureSubscribed, onNotification, type RpcNotification } from "./rpc/client";
import { matchStatus, useApp, type EntryRow } from "./store/app";
import "./App.css";

/** 状态单元格显示（M4：edited 与 status 正交——机翻条目改后显示"机翻·已改"两状态）。 */
function statusInfo(e: EntryRow): { label: string; cls: string } {
  if (e.status === 1) return { label: "待译", cls: "status-pending" };
  if (e.status === 4) return { label: "已确认", cls: "status-confirmed" };
  if (e.edited === 1) return { label: e.status === 2 ? "机翻·已改" : "已修改", cls: "status-edited" };
  if (e.status === 2) return { label: "机翻", cls: "status-machine" };
  return { label: "已修改", cls: "status-edited" }; // 历史 status3 等
}

function Editor() {
  const { entries, entriesLoading, fileFilter, statusFilter, setFileFilter, setStatusFilter } = useApp();
  const [editing, setEditing] = useState<EntryRow | null>(null);
  const visible = entries.filter((e) => {
    if (fileFilter && e.file_path !== fileFilter) return false;
    if (statusFilter !== null && !matchStatus(e, statusFilter)) return false;
    return true;
  });

  const columns = useMemo<ColumnDef<EntryRow, unknown>[]>(
    () => [
      {
        id: "status",
        header: "状态",
        size: 70,
        cell: (info) => {
          const s = statusInfo(info.row.original);
          return <span className={`status ${s.cls}`}>{s.label}</span>;
        },
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
        <h2>翻译编辑器</h2>
        <p>点菜单栏「导入游戏」开始。</p>
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

export default function App() {
  const { loadProviders, setTranslateProgress, importProgress } = useApp();
  const [apiOpen, setApiOpen] = useState(false);
  const [translateOpen, setTranslateOpen] = useState(false);
  const [writebackOpen, setWritebackOpen] = useState(false);

  // 启动即订阅通知（防丢）+ 加载 Provider
  useEffect(() => {
    ensureSubscribed();
    loadProviders().catch(() => {});
    // progress 通知 → 翻译进度（状态栏）+ 完成后刷新
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
      <MenuBar
        onTranslate={() => setTranslateOpen(true)}
        onWriteback={() => setWritebackOpen(true)}
        onOpenApi={() => setApiOpen(true)}
      />
      <div className="app-body" style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <Sidebar />
        <main className="app-content" style={{ flex: 1, minWidth: 0, display: "flex" }}>
          <Editor />
        </main>
      </div>
      <StatusBar />

      <TranslateModal open={translateOpen} onClose={() => setTranslateOpen(false)} />
      <WritebackModal open={writebackOpen} onClose={() => setWritebackOpen(false)} />
      <ApiKeyModal open={apiOpen} onClose={() => setApiOpen(false)} />

      {/* 导入进度覆盖层 */}
      {importProgress && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 200,
          display: "flex", alignItems: "center", justifyContent: "center",
          padding: 16, boxSizing: "border-box",
        }}>
          <div style={{
            background: "#1c1c1f", border: "1px solid #3a3a40", borderRadius: 10,
            padding: "24px 28px", width: "min(90vw, 360px)", textAlign: "center",
            boxSizing: "border-box",
          }}>
            <h3 style={{ margin: "0 0 16px", fontSize: 14 }}>导入游戏</h3>
            <div style={{
              width: "100%", height: 8, background: "#26262a", borderRadius: 4,
              overflow: "hidden",
            }}>
              <div style={{
                height: "100%", width: `${importProgress.pct}%`,
                background: "linear-gradient(90deg,#2d6cdf,#4da3ff)",
                transition: "width 0.3s ease",
              }} />
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
