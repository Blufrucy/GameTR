/**
 * 左侧活动栏（VSCode 风格）：图标切换视图 + 底部设置。
 * 编辑器 / 翻译 / 回写 / 模型 API。
 */

import type { CSSProperties } from "react";
import { useApp, type View } from "../store/app";

const ITEMS: { key: View; icon: string; label: string }[] = [
  { key: "editor", icon: "📄", label: "编辑器" },
  { key: "translate", icon: "🌐", label: "翻译" },
  { key: "writeback", icon: "↩", label: "回写" },
];

export function ActivityBar({ onOpenApi }: { onOpenApi: () => void }) {
  const { view, setView } = useApp();

  return (
    <div
      style={{
        width: 48, background: "#252526", borderRight: "1px solid #333",
        display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 4,
      }}
    >
      {ITEMS.map((it) => (
        <button
          key={it.key}
          onClick={() => setView(it.key)}
          title={it.label}
          style={{
            ...iconBtn,
            borderLeft: view === it.key ? "2px solid #4da3ff" : "2px solid transparent",
            color: view === it.key ? "#4da3ff" : "#8a8a92",
          }}
        >
          <span style={{ fontSize: 18 }}>{it.icon}</span>
        </button>
      ))}
      <div style={{ flex: 1 }} />
      <button onClick={onOpenApi} title="模型 API 设置" style={iconBtn}>
        <span style={{ fontSize: 18 }}>⚙</span>
      </button>
    </div>
  );
}

const iconBtn: CSSProperties = {
  width: 46, height: 42, background: "none", border: "none",
  display: "flex", alignItems: "center", justifyContent: "center",
  cursor: "pointer", borderRadius: 0,
};
