/**
 * 底部状态栏：左=翻译进度（非模态长任务）+ 状态消息；右=翻译引擎选择器。
 *
 * 引擎选择器（M4，CC-switch 式「启用」）：点 pill → 上方 drop-up 面板列出各已配置服务
 * 下的模型，点某个模型即启用为翻译引擎（store.enableEngine 置 selectedProvider+model，
 * translate.start 真用它）。面板 position:fixed 锚定 viewport —— 避开弹窗 overflow 容器
 * 对 absolute 弹层的裁剪/抢滚轮（WebView2 实测坑），外层 mousedown/Escape 关闭。
 */

import { useEffect, useRef, useState } from "react";
import { useApp, type ProviderInfo } from "../store/app";

export function StatusBar({ onOpenApi }: { onOpenApi?: () => void }) {
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
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>
        {statusMessage ?? "就绪"}
      </span>
      <span style={{ flexShrink: 0 }}>
        <EnginePicker onOpenApi={onOpenApi} />
      </span>
    </div>
  );
}

const GREEN = "#34c759";
const MUTED = "#8a8a92";

/** 翻译引擎启用按钮 + drop-up 面板（CC-switch 式：点模型即切换当前翻译引擎）。 */
function EnginePicker({ onOpenApi }: { onOpenApi?: () => void }) {
  const { providers, selectedProvider, selectedModel, enableEngine } = useApp();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ right: number; bottom: number } | null>(null);
  const pillRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  const provider = providers.find((p) => p.provider_id === selectedProvider) ?? null;
  const enabled = provider !== null;
  const label = provider
    ? (selectedModel ? `${provider.display_name} · ${selectedModel}` : provider.display_name)
    : "翻译引擎：未配置";

  // 开面板期间：点外部 / Escape 关闭
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (pillRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function toggle() {
    if (open) { setOpen(false); return; }
    const r = pillRef.current!.getBoundingClientRect();
    // 锚定 viewport：面板右上角对齐 pill，drop-up（bottom 是到底部的距离，向上弹）
    setPos({ right: window.innerWidth - r.right, bottom: window.innerHeight - r.top + 6 });
    setOpen(true);
  }

  function choose(p: ProviderInfo, model: string) {
    enableEngine(p.provider_id, model); // 启用即生效（翻译真用它）
    setOpen(false);
  }

  return (
    <>
      <button
        ref={pillRef}
        onClick={toggle}
        title={enabled ? "切换翻译引擎（点开选服务·模型）" : "还没有可用的翻译服务，点开去配置"}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          background: "#222226", color: "#c9c9ce", border: "1px solid #333338",
          borderRadius: 999, padding: "2px 10px", fontSize: 12, cursor: "pointer",
          maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
      >
        <span style={{
          width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
          background: enabled ? GREEN : "#4a4a50",
        }} />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        <span style={{ color: MUTED, flexShrink: 0 }}>▾</span>
      </button>

      {open && pos && (
        <div
          ref={panelRef}
          style={{
            position: "fixed", right: pos.right, bottom: pos.bottom, zIndex: 500,
            width: "min(320px, calc(100vw - 16px))", maxHeight: "60vh", overflowY: "auto",
            background: "#161619", border: "1px solid #333338", borderRadius: 8,
            boxShadow: "0 8px 24px rgba(0,0,0,0.45)", padding: "6px",
            boxSizing: "border-box",
          }}
        >
          <div style={{ padding: "4px 8px", fontSize: 11, color: MUTED }}>翻译引擎（点模型启用）</div>
          {providers.length === 0 ? (
            <p style={{ margin: "4px 8px 8px", fontSize: 12, color: MUTED }}>
              还没有可用的翻译服务——先添加一个（选预设/填 key → 测试 → 保存）。
            </p>
          ) : (
            providers.map((p) => {
              const activeP = p.provider_id === selectedProvider;
              return (
                <div key={p.provider_id} style={{ marginTop: 2 }}>
                  <div
                    title={`${p.display_name}\n${p.base_url ?? ""}`}
                    style={{
                      display: "flex", alignItems: "center", gap: 6,
                      padding: "4px 8px", fontSize: 11, color: activeP ? "#4da3ff" : MUTED,
                    }}
                  >
                    <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {p.display_name}
                    </span>
                    {activeP && <span style={{ fontSize: 10, color: GREEN }}>启用中</span>}
                  </div>
                  {p.models.map((m) => {
                    const on = activeP && m === selectedModel;
                    return (
                      <button
                        key={m}
                        onClick={() => choose(p, m)}
                        title={m}
                        style={{
                          display: "flex", alignItems: "center", gap: 8, width: "100%",
                          textAlign: "left", background: on ? "#1a2536" : "transparent",
                          color: on ? "#4da3ff" : "#d0d0d4", border: "none", borderRadius: 5,
                          padding: "5px 8px 5px 16px", fontSize: 12, cursor: "pointer",
                        }}
                      >
                        <span style={{ color: on ? GREEN : "#4a4a50", flexShrink: 0 }}>{on ? "●" : "○"}</span>
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m}</span>
                      </button>
                    );
                  })}
                </div>
              );
            })
          )}
          <div style={{ borderTop: "1px solid #26262a", marginTop: 4, padding: "6px 2px 0" }}>
            <button
              onClick={() => { setOpen(false); onOpenApi?.(); }}
              style={{
                background: "none", border: "none", color: "#9cc9ff", cursor: "pointer",
                fontSize: 12, textDecoration: "underline", padding: "2px 6px",
              }}
            >
              {providers.length === 0 ? "去添加翻译服务…" : "管理模型 API…"}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
