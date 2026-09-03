/**
 * 三态复选框：0=未选 / 1=半选 / 2=全选。
 * 用于「文件 vs 片段」父子联动：某文件所有片段都勾选 → 文件才显示为全选（2），
 * 部分勾选 → 半选（1），未勾 → 空（0）。点击 = 切换 未选↔全选。
 */

import type { MouseEvent } from "react";

export type TriValue = 0 | 1 | 2;

const ACCENT = "#2d6cdf";
const EDGE = "#56565e";

export function TriCheck({
  value,
  onChange,
  title,
  disabled,
}: {
  value: TriValue;
  onChange: (next: boolean) => void;
  title?: string;
  disabled?: boolean;
}) {
  const filled = value !== 0;
  return (
    <button
      type="button"
      disabled={disabled}
      title={title}
      onClick={(e: MouseEvent) => {
        e.stopPropagation(); // 表格行点击（打开编辑）不被勾选触发
        onChange(value !== 2);
      }}
      style={{
        width: 16, height: 16, padding: 0, boxSizing: "border-box", flexShrink: 0,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        borderRadius: 4, cursor: disabled ? "default" : "pointer",
        border: filled ? `1px solid ${ACCENT}` : `1px solid ${EDGE}`,
        background: filled ? ACCENT : "transparent",
        color: "#fff", fontSize: 11, lineHeight: 1,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {value === 2 ? "✓" : value === 1 ? "–" : ""}
    </button>
  );
}
