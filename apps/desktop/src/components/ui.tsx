/**
 * 自研轻量 UI 组件（M4 桌面应用，不引入 UI 全家桶）。
 * Button / Select / Modal——够用且可定制，bundle 小。
 */

import type { CSSProperties, ReactNode } from "react";

type Variant = "primary" | "default" | "ghost" | "danger";

const VARIANTS: Record<Variant, CSSProperties> = {
  primary: { background: "#2d6cdf", borderColor: "#2d6cdf", color: "#fff" },
  default: { background: "#26262a", borderColor: "#3a3a40", color: "#e6e6e8" },
  ghost: { background: "transparent", borderColor: "transparent", color: "#8a8a92" },
  danger: { background: "#3a1515", borderColor: "#5b2323", color: "#ff9b9b" },
};

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: Variant;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      title={title}
      disabled={disabled}
      onClick={onClick}
      style={{
        ...VARIANTS[variant],
        padding: "5px 12px",
        borderRadius: 6,
        fontSize: 13,
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.5 : 1,
        border: "1px solid",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </button>
  );
}

export function Select({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  label?: string;
}) {
  return (
    <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13 }}>
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          background: "#1c1c1f",
          color: "#e6e6e8",
          border: "1px solid #3a3a40",
          borderRadius: 6,
          padding: "4px 8px",
          fontSize: 13,
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "#1c1c1f",
          border: "1px solid #3a3a40",
          borderRadius: 10,
          padding: "16px 20px",
          minWidth: 460,
          maxWidth: 640,
          maxHeight: "80vh",
          overflow: "auto",
          boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
          }}
        >
          <h2 style={{ margin: 0, fontSize: 15 }}>{title}</h2>
          <button
            onClick={onClose}
            style={{ background: "none", border: "none", color: "#8a8a92", cursor: "pointer", fontSize: 18 }}
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
