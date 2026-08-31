/**
 * 侧边栏：文件树（按 file_path 分组，点击过滤编辑器）+ 状态筛选计数。
 */

import { useMemo } from "react";
import { useApp } from "../store/app";

const STATUS_FILTERS = [
  { key: 1, label: "待译" },
  { key: 2, label: "机翻" },
  { key: 3, label: "已改" },
  { key: 4, label: "已确认" },
];

export function Sidebar() {
  const { entries, fileFilter, setFileFilter, statusFilter, setStatusFilter } = useApp();

  // 文件树：file_path -> 条目数（按出现顺序分组）
  const files = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of entries) {
      m.set(e.file_path, (m.get(e.file_path) ?? 0) + 1);
    }
    return [...m.entries()];
  }, [entries]);

  // 状态计数
  const statusCounts = useMemo(() => {
    const c: Record<number, number> = {};
    for (const e of entries) c[e.status] = (c[e.status] ?? 0) + 1;
    return c;
  }, [entries]);

  const total = entries.length;

  /** 点状态：若当前文件筛选下该状态为空 → 自动清除文件筛选（显示全局该状态，无需手动清）。 */
  function handleStatusClick(key: number) {
    if (fileFilter && !entries.some((e) => e.file_path === fileFilter && e.status === key)) {
      setFileFilter(null);
    }
    setStatusFilter(key);
  }

  /** 点文件：若该文件下当前状态为空 → 自动清除状态筛选。 */
  function handleFileClick(file: string) {
    if (statusFilter !== null && !entries.some((e) => e.file_path === file && e.status === statusFilter)) {
      setStatusFilter(null);
    }
    setFileFilter(file);
  }

  return (
    <aside
      className="sidebar"
      style={{
        width: 200,
        borderRight: "1px solid #2a2a2e",
        background: "#161619",
        padding: "12px 10px",
        display: "flex",
        flexDirection: "column",
        gap: 12,
        overflow: "auto",
      }}
    >
      <section>
        <h3 style={{ margin: "0 0 8px", fontSize: 12, color: "#8a8a92" }}>
          文件（{files.length}）
        </h3>
        {files.length === 0 ? (
          <p style={{ margin: 0, fontSize: 12, color: "#5a5a60" }}>导入游戏后显示</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <button
              onClick={() => setFileFilter(null)}
              style={fileBtn(fileFilter === null)}
            >
              全部（{total}）
            </button>
            {files.map(([file, count]) => (
              <button
                key={file}
                title={file}
                onClick={() => handleFileClick(file)}
                style={fileBtn(fileFilter === file)}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {file.split("/").pop()}
                </span>
                <span style={{ marginLeft: "auto", color: "#6a6a70", fontSize: 11 }}>{count}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section>
        <h3 style={{ margin: "0 0 8px", fontSize: 12, color: "#8a8a92" }}>状态</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <button onClick={() => setStatusFilter(null)} style={fileBtn(statusFilter === null)}>
            <span>全部</span>
            <span style={{ marginLeft: "auto", color: "#6a6a70", fontSize: 11 }}>{total}</span>
          </button>
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => handleStatusClick(f.key)}
              style={fileBtn(statusFilter === f.key)}
            >
              <span>{f.label}</span>
              <span style={{ marginLeft: "auto", color: "#6a6a70", fontSize: 11 }}>
                {statusCounts[f.key] ?? 0}
              </span>
            </button>
          ))}
        </div>
      </section>
    </aside>
  );
}

function fileBtn(active: boolean): React.CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 6,
    textAlign: "left",
    padding: "4px 8px",
    borderRadius: 5,
    border: "none",
    fontSize: 12,
    cursor: "pointer",
    background: active ? "#1a2536" : "transparent",
    color: active ? "#4da3ff" : "#d0d0d4",
  };
}
