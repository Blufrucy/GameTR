/**
 * VirtualTable：基于 @tanstack/react-table (v8) + @tanstack/react-virtual 的虚拟滚动表格。
 *
 * 性能关键（M4 双栏编辑器 5 万行目标）：
 * - **只渲染可视区行**（virtualizer），DOM 行数 = 视口高度/行高 + overscan，与数据量无关
 * - **virtualizer 放最低组件（TableBody）**：滚动/数据变化只重渲染可视行，不重渲染整表
 * - 固定行高（estimateSize 常量）：免动态测量，滚动最流畅
 *
 * 用 CSS grid 替代 table 布局（官方虚拟化推荐）：thead sticky + tbody 绝对定位。
 */

import { useRef } from "react";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type Row,
  type Table,
} from "@tanstack/react-table";
import { useVirtualizer, type VirtualItem, type Virtualizer } from "@tanstack/react-virtual";

interface VirtualTableProps<T> {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  rowHeight?: number; // 固定行高（px）
  overscan?: number; // 视口外预渲染行数
  getRowId?: (row: T) => string;
  onRowClick?: (row: T) => void;
  height?: number | string; // 滚动容器高度
}

export function VirtualTable<T>({
  columns,
  data,
  rowHeight = 40,
  overscan = 8,
  getRowId,
  onRowClick,
  height = "100%",
}: VirtualTableProps<T>) {
  const table = useReactTable<T>({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });
  const containerRef = useRef<HTMLDivElement>(null);

  return (
    <div className="vt-scroll" ref={containerRef} style={{ height, overflow: "auto" }}>
      <table className="vt-table" style={{ display: "grid" }}>
        <thead className="vt-head" style={{ display: "grid", position: "sticky", top: 0, zIndex: 1 }}>
          {table.getHeaderGroups().map((headerGroup) => (
            <tr key={headerGroup.id} style={{ display: "flex", width: "100%" }}>
              {headerGroup.headers.map((header) => (
                <th
                  key={header.id}
                  style={{
                    flex: header.column.getSize() ? undefined : 1,
                    width: header.column.getSize() || undefined,
                    padding: "4px 8px",
                    textAlign: "left",
                    fontSize: 12,
                    color: "#889",
                    borderBottom: "1px solid #333",
                    background: "#1c1c1f",
                    userSelect: "none",
                  }}
                >
                  {header.isPlaceholder
                    ? null
                    : flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <VirtualTableBody
          table={table}
          containerRef={containerRef}
          rowHeight={rowHeight}
          overscan={overscan}
          getRowId={getRowId}
          onRowClick={onRowClick}
        />
      </table>
    </div>
  );
}

interface TableBodyProps<T> {
  table: Table<T>;
  containerRef: React.RefObject<HTMLDivElement | null>;
  rowHeight: number;
  overscan: number;
  getRowId?: (row: T) => string;
  onRowClick?: (row: T) => void;
}

/** 行虚拟化放最低组件：滚动/数据变化只重渲染可视行（性能关键）。 */
function VirtualTableBody<T>({
  table,
  containerRef,
  rowHeight,
  overscan,
  getRowId,
  onRowClick,
}: TableBodyProps<T>) {
  const { rows } = table.getRowModel();
  const rowVirtualizer = useVirtualizer<HTMLDivElement, HTMLTableRowElement>({
    count: rows.length,
    estimateSize: () => rowHeight,
    getScrollElement: () => containerRef.current,
    overscan,
  });

  return (
    <tbody
      className="vt-body"
      style={{
        display: "grid",
        height: `${rowVirtualizer.getTotalSize()}px`, // 滚动条总高度 = 数据量
        position: "relative",
      }}
    >
      {rowVirtualizer.getVirtualItems().map((virtualRow) => {
        const row = rows[virtualRow.index] as Row<T>;
        return (
          <VirtualTableRow
            key={getRowId ? getRowId(row.original) : row.id}
            row={row}
            virtualRow={virtualRow}
            rowVirtualizer={rowVirtualizer}
            rowHeight={rowHeight}
            onClick={onRowClick ? () => onRowClick(row.original) : undefined}
          />
        );
      })}
    </tbody>
  );
}

interface RowProps<T> {
  row: Row<T>;
  virtualRow: VirtualItem;
  rowVirtualizer: Virtualizer<HTMLDivElement, HTMLTableRowElement>;
  rowHeight: number;
  onClick?: () => void;
}

function VirtualTableRow<T>({ row, virtualRow, rowVirtualizer, rowHeight, onClick }: RowProps<T>) {
  return (
    <tr
      data-index={virtualRow.index}
      ref={(node) => rowVirtualizer.measureElement(node)} // 动态测量（固定行高时开销极小，保持正确性）
      onClick={onClick}
      style={{
        display: "flex",
        position: "absolute",
        transform: `translateY(${virtualRow.start}px)`, // 用 style 而非 CSS：滚动时即时更新
        width: "100%",
        height: rowHeight,
        alignItems: "center",
        borderBottom: "1px solid #2a2a2e",
        cursor: onClick ? "pointer" : "default",
      }}
    >
      {row.getVisibleCells().map((cell) => (
        <td
          key={cell.id}
          style={{
            flex: cell.column.getSize() ? undefined : 1,
            width: cell.column.getSize() || undefined,
            padding: "0 8px",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontSize: 13,
          }}
        >
          {flexRender(cell.column.columnDef.cell, cell.getContext())}
        </td>
      ))}
    </tr>
  );
}
