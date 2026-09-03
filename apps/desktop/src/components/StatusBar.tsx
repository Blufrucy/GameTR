/**
 * 底部状态栏：翻译进度（非模态，长任务期间显示）+ 状态消息 + 忙碌。
 */

import { useApp } from "../store/app";

export function StatusBar() {
  const { statusMessage, busy, translateTask } = useApp();

  return (
    <div
      className="statusbar"
      style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "6px 14px", borderTop: "1px solid #2a2a2e",
        background: "#18181b", fontSize: 12, color: "#8a8a92",
      }}
    >
      {translateTask && translateTask.status === "running" ? (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, color: "#4da3ff" }}>
          <span className="spinner" />
          翻译中 {translateTask.done}/{translateTask.total}
          <span style={{ display: "inline-block", width: 140, height: 6, background: "#26262a", borderRadius: 3, overflow: "hidden" }}>
            <span style={{
              display: "block", height: "100%",
              width: `${translateTask.total > 0 ? (translateTask.done / translateTask.total) * 100 : 0}%`,
              background: "#4da3ff", transition: "width 0.3s",
            }} />
          </span>
        </span>
      ) : (
        busy && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#4da3ff" }}>
            <span className="spinner" /> 处理中…
          </span>
        )
      )}
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {statusMessage ?? "就绪"}
      </span>
    </div>
  );
}
