/**
 * 顶部菜单栏（VSCode 风格）：文件（导入/导出/导入翻译）· 翻译 · 回写 · 模型 API。
 * 紧凑横排，替代原工具栏。
 */

import { useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { rpc } from "../rpc/client";
import { useApp } from "../store/app";
import { Button, Modal } from "./ui";

interface DetectResult { engine_id: string; display_name: string; }
interface PluginInfo { engine_id: string; display_name: string; loaded: boolean; }

export function MenuBar() {
  const {
    setStatus, setBusy, projectPath, importGame,
    exportTranslations, importTranslations, setImportProgress,
  } = useApp();
  const [importing, setImporting] = useState(false);
  const [dir, setDir] = useState<string | null>(null);
  const [engines, setEngines] = useState<PluginInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);

  async function handleImport() {
    setImporting(true);
    setStatus(null);
    try {
      const picked = await open({ directory: true, title: "选择游戏文件夹" });
      if (typeof picked !== "string") return;
      setDir(picked);
      setBusy(true);
      setImportProgress({ phase: "检测游戏引擎…", pct: 5 });
      let detected: string | null = null;
      try {
        const d = await rpc<DetectResult>("detect.run", { dir: picked });
        detected = d.engine_id;
      } catch { /* 识别失败 → 手动选 */ }
      const plugins = await rpc<PluginInfo[]>("plugins.list");
      setEngines(plugins.filter((p) => p.loaded));
      setSelected(detected ?? plugins[0]?.engine_id ?? null);
      setBusy(false);
      setImportProgress(null); // 弹窗由用户确认，确认后 importGame 接管进度
      setWizardOpen(true);
    } catch (err) {
      setBusy(false);
      setStatus(`导入失败: ${err}`);
    } finally {
      setImporting(false);
    }
  }

  async function handleConfirm() {
    if (!dir || !selected) return;
    setWizardOpen(false);
    await importGame(dir, selected);
  }

  return (
    <div
      className="menubar"
      style={{
        display: "flex", alignItems: "center", gap: 2, padding: "4px 8px",
        background: "#1e1e1e", borderBottom: "1px solid #333", fontSize: 13, flexWrap: "wrap",
      }}
    >
      <span style={{ marginRight: 8, fontWeight: 600, color: "#4da3ff" }}>GameTR</span>
      <MenuBtn onClick={handleImport} disabled={importing}>{importing ? "检测中…" : "导入游戏"}</MenuBtn>
      <Sep />
      <MenuBtn onClick={exportTranslations} disabled={!projectPath} title="导出译文，分享给同款游戏用户">导出翻译</MenuBtn>
      <MenuBtn onClick={importTranslations} disabled={!projectPath} title="导入同款游戏的译文，免重复翻译">导入翻译</MenuBtn>

      {/* 游戏类型选择（导入后） */}
      <Modal open={wizardOpen} onClose={() => setWizardOpen(false)} title="选择游戏类型">
        <p style={{ margin: "0 0 12px", fontSize: 12, color: "#8a8a92" }}>
          已自动识别到游戏类型（可改选），将用对应引擎插件提取文本。
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {engines.map((e) => (
            <button
              key={e.engine_id}
              onClick={() => setSelected(e.engine_id)}
              style={{
                textAlign: "left", padding: "10px 12px", borderRadius: 8,
                border: `1px solid ${selected === e.engine_id ? "#2d6cdf" : "#2a2a2e"}`,
                background: selected === e.engine_id ? "#101828" : "#161619",
                color: "#e6e6e8", fontSize: 14, cursor: "pointer",
              }}
            >
              {e.display_name}
              {selected === e.engine_id && <span style={{ float: "right", color: "#4da3ff" }}>✓</span>}
            </button>
          ))}
          {engines.length === 0 && <p style={{ fontSize: 13, color: "#8a8a92" }}>没有可用的引擎插件。</p>}
        </div>
        <div style={{ marginTop: 16, display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <Button onClick={() => setWizardOpen(false)}>取消</Button>
          <Button variant="primary" onClick={handleConfirm} disabled={!selected}>确认</Button>
        </div>
      </Modal>
    </div>
  );
}

function MenuBtn({ children, onClick, disabled, title }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean; title?: string;
}) {
  return (
    <button
      onClick={onClick} disabled={disabled} title={title}
      style={{
        background: "none", border: "none", color: disabled ? "#5a5a60" : "#e6e6e8",
        padding: "3px 8px", borderRadius: 4, fontSize: 13, cursor: disabled ? "default" : "pointer",
        whiteSpace: "nowrap",
      }}
      onMouseEnter={(e) => { if (!disabled) e.currentTarget.style.background = "#2a2a2e"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "none"; }}
    >
      {children}
    </button>
  );
}

function Sep() {
  return <span style={{ width: 1, height: 16, background: "#333", margin: "0 4px" }} />;
}
