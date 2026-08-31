/**
 * 底部状态栏：状态消息 + 忙碌指示（进度概念的雏形，M4 接 progress 通知）。
 */

import { useApp } from "../store/app";

export function StatusBar() {
  const { statusMessage, busy } = useApp();

  return (
    <div
      className="statusbar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "6px 14px",
        borderTop: "1px solid #2a2a2e",
        background: "#18181b",
        fontSize: 12,
        color: "#8a8a92",
      }}
    >
      {busy && (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#4da3ff" }}>
          <span className="spinner" /> 处理中…
        </span>
      )}
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {statusMessage ?? "就绪"}
      </span>
      <span style={{ marginLeft: "auto" }}>侧边栏 · 桌面端</span>
    </div>
  );
}
