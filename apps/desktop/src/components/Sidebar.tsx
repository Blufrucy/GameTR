/**
 * 侧边栏：状态筛选（钉顶固定）+ 文件导航（独立滚动 + 即时过滤 + 勾选 + 工作队列角标）。
 *
 * - 状态行固定在最上（横切过滤器永远可见，不随文件滚动）；
 * - 文件列独立滚动，顶部即时过滤框（子串匹配文件名）——检索代替滚动；
 * - 每文件前有三态勾选（父子联动）：文件内**所有片段**都勾选 → 文件显示全选(✓)，
 *   部分勾选 → 半选(–)，未勾 → 空。勾文件 = 把它所有片段纳入翻译范围（与状态筛选无关，
 *   不受当前只看到部分片段影响）。
 * - 文件角标随当前状态变工作队列：无状态筛选时=该文件条数；选中某状态（如「待译」）后
 *   角标=该文件内命中条数，有活置琥珀色突出、0 条置灰。
 *
 * 状态语义（M4）：待译=status1，机翻=status2，已修改=edited（人工标记），已确认=status4。
 */

import { useMemo, useState } from "react";
import { matchStatus, STATUS_LABEL, useApp, type StatusKey } from "../store/app";
import { TriCheck } from "./Checkbox";

const STATUS_KEYS: StatusKey[] = ["pending", "machine", "edited", "confirmed"];

const DIM = "#5a5a60";
const MUTED = "#6a6a70";
const NAV = "#d0d0d4";
const NAV_ACTIVE = "#4da3ff";

export function Sidebar() {
  const {
    entries, fileFilter, setFileFilter, statusFilter, setStatusFilter,
    selectedIds, setFileSelection,
  } = useApp();
  const [q, setQ] = useState("");

  // 文件行：全量条数 + 勾选数（父子联动按全量算，不受状态/可见性筛选影响）
  const fileRows = useMemo(() => {
    const m = new Map<string, { total: number; sel: number }>();
    for (const e of entries) {
      const rec = m.get(e.file_path) ?? { total: 0, sel: 0 };
      rec.total += 1;
      if (selectedIds.has(e.id)) rec.sel += 1;
      m.set(e.file_path, rec);
    }
    return [...m.entries()].map(([file, rec]) => ({ file, ...rec }));
  }, [entries, selectedIds]);

  // 「当前状态」下各文件命中数（无状态筛选=全量）——文件角标的值，随状态切换变工作队列。
  // 顺带一趟算状态计数（顶部行），避免每条状态又 filter 一遍 entries。
  const { scoped, statusCounts } = useMemo(() => {
    const m = new Map<string, number>();
    const sc: Record<StatusKey, number> = { pending: 0, machine: 0, edited: 0, confirmed: 0 };
    let total = 0;
    for (const e of entries) {
      for (const k of STATUS_KEYS) if (matchStatus(e, k)) sc[k] += 1;
      if (statusFilter !== null && !matchStatus(e, statusFilter)) continue;
      m.set(e.file_path, (m.get(e.file_path) ?? 0) + 1);
      total += 1;
    }
    m.set("__total__", total);
    return { scoped: m, statusCounts: sc };
  }, [entries, statusFilter]);
  const scopedTotal = scoped.get("__total__") ?? entries.length;

  // 文件即时过滤（本地查询，不写 fileFilter）：按文件名（去扩展名）子串匹配
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return fileRows;
    return fileRows.filter(({ file }) => {
      const base = file.split("/").pop()?.toLowerCase() ?? "";
      return base.includes(s) || file.toLowerCase().includes(s);
    });
  }, [fileRows, q]);

  const selectedCount = useMemo(() => {
    let n = 0;
    for (const e of entries) if (selectedIds.has(e.id)) n += 1;
    return n;
  }, [entries, selectedIds]);

  /** 点状态：若当前文件筛选下该状态为空 → 自动清除文件筛选（显示全局该状态，无需手动清）。 */
  function handleStatusClick(key: StatusKey) {
    if (fileFilter && !entries.some((e) => e.file_path === fileFilter && matchStatus(e, key))) {
      setFileFilter(null);
    }
    setStatusFilter(key);
  }

  /** 点文件：若该文件下当前状态为空 → 自动清除状态筛选。 */
  function handleFileClick(file: string) {
    if (statusFilter !== null && !entries.some((e) => e.file_path === file && matchStatus(e, statusFilter))) {
      setStatusFilter(null);
    }
    setFileFilter(file);
  }

  return (
    <aside
      className="sidebar"
      style={{
        width: 210,
        borderRight: "1px solid #2a2a2e",
        background: "#161619",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden", // 状态行与文件区各自管滚动，整个栏不滚
      }}
    >
      {/* 状态行：钉在顶部，永不沉底 */}
      <section style={{ padding: "10px 10px 8px", borderBottom: "1px solid #222226", flexShrink: 0 }}>
        <h3 style={{ margin: "0 0 6px", fontSize: 12, color: "#8a8a92" }}>状态</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <button onClick={() => setStatusFilter(null)} style={btn(statusFilter === null)}>
            <span>全部</span>
            <span style={{ marginLeft: "auto", color: MUTED, fontSize: 11 }}>{entries.length}</span>
          </button>
          {STATUS_KEYS.map((k) => (
            <button key={k} onClick={() => handleStatusClick(k)} style={btn(statusFilter === k)}>
              <span>{STATUS_LABEL[k]}</span>
              <span style={{ marginLeft: "auto", color: MUTED, fontSize: 11 }}>{statusCounts[k]}</span>
            </button>
          ))}
        </div>
      </section>

      {/* 文件区：独立滚动 + 顶部过滤框 */}
      <section style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: "10px" }}>
        <h3 style={{ margin: "0 0 6px", fontSize: 12, color: "#8a8a92" }}>
          文件{q.trim() ? `（筛出 ${shown.length}/${fileRows.length}）` : `（${fileRows.length}）`}
        </h3>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="过滤文件名，如 Map0"
          style={{
            flexShrink: 0, background: "#1c1c1f", color: "#e6e6e8",
            border: "1px solid #3a3a40", borderRadius: 5, padding: "4px 8px", fontSize: 12,
            outline: "none",
          }}
        />
        {selectedCount > 0 && (
          <div style={{ marginTop: 6, fontSize: 11, color: "#ffb340" }}>
            已勾选 {selectedCount} 条片段（去「翻译」可只翻所选）
          </div>
        )}
        <div
          style={{
            flex: 1, minHeight: 0, overflowY: "auto", marginTop: 6,
            display: "flex", flexDirection: "column", gap: 2,
          }}
        >
          <button
            onClick={() => setFileFilter(null)}
            style={{ ...btn(fileFilter === null), color: fileFilter === null ? NAV_ACTIVE : NAV }}
          >
            <span>全部</span>
            <span style={{ marginLeft: "auto", color: MUTED, fontSize: 11 }}>{scopedTotal}</span>
          </button>
          {shown.map(({ file, total, sel }) => {
            const cnt = statusFilter === null ? total : (scoped.get(file) ?? 0);
            const hasWork = statusFilter === null || cnt > 0;
            const tri: 0 | 1 | 2 = sel === 0 ? 0 : sel === total ? 2 : 1;
            return (
              <div key={file} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ flexShrink: 0, display: "inline-flex" }}>
                  <TriCheck
                    value={tri}
                    onChange={(next) => setFileSelection(file, next)}
                    title={tri === 2
                      ? "取消勾选整个文件"
                      : `勾选整个文件（${total} 条片段全选；已勾 ${sel}）`}
                  />
                </span>
                <button
                  onClick={() => handleFileClick(file)}
                  title={file}
                  style={{
                    flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 6,
                    textAlign: "left", padding: "4px 6px", borderRadius: 5, border: "none",
                    fontSize: 12, cursor: "pointer",
                    background: fileFilter === file ? "#1a2536" : "transparent",
                    color: fileFilter === file ? NAV_ACTIVE : NAV,
                  }}
                >
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {file.split("/").pop()}
                  </span>
                  {/* 工作队列角标：选中状态时显示该文件待处理数；有活置琥珀色突出 */}
                  <span
                    style={{
                      marginLeft: "auto", flexShrink: 0, fontSize: 11,
                      color: !hasWork ? DIM : statusFilter !== null ? "#ffb340" : MUTED,
                    }}
                  >
                    {cnt}
                  </span>
                </button>
              </div>
            );
          })}
          {shown.length === 0 && (
            <p style={{ margin: "6px 0 0", fontSize: 11, color: DIM }}>无匹配文件（清空过滤框重试）</p>
          )}
        </div>
      </section>
    </aside>
  );
}

function btn(active: boolean, dim = false): React.CSSProperties {
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
    color: dim ? DIM : active ? NAV_ACTIVE : NAV,
  };
}
