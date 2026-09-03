/**
 * 全局应用状态（zustand，M4 桌面应用）。
 *
 * 管理：Provider/模型/API key、当前项目（导入/extract/条目）、翻译任务进度、UI 视图。
 * API key 本地持久化（localStorage，MVP）；正式方案 OS keyring。
 */

import { create } from "zustand";
import { open, save } from "@tauri-apps/plugin-dialog";
import { rpc } from "../rpc/client";

export interface ProviderInfo {
  provider_id: string;
  display_name: string;
  models: string[];
  needs_api_key: boolean;
  supports_structured: boolean;
  base_url: string | null;
}

/** 编辑器条目（映射核心 Entry，context_json 拆出 file_path）。 */
export interface EntryRow {
  id: string;
  source: string;
  translation: string | null;
  status: number;
  edited: number; // 人工修改标记（M4：1=被人工编辑过，与 status 正交）
  machine_text: string | null; // 最近一次机翻输出基线（机翻·已改可查看/恢复；纯机翻未改时=translation）
  locator: string;
  file_path: string;
}

/** 状态筛选 key（M4：已修改=人工标记 edited，独立于机翻 status——机翻条目改后两筛都命中） */
export type StatusKey = "pending" | "machine" | "edited" | "confirmed";

export const STATUS_LABEL: Record<StatusKey, string> = {
  pending: "待译", machine: "机翻", edited: "已修改", confirmed: "已确认",
};

export function matchStatus(e: EntryRow, key: StatusKey): boolean {
  switch (key) {
    case "pending": return e.status === 1;
    case "machine": return e.status === 2;
    case "edited": return e.edited === 1; // 人工动过（机翻改 / 待译填 / 确认过的都算）
    case "confirmed": return e.status === 4;
  }
}

export interface TranslateTaskInfo {
  task_id: string;
  status: string;
  done: number;
  total: number;
}

export interface WriteBackResult {
  output_dir: string;
  written_count: number;
  warning_count: number;
  message: string | null;
}

interface AppState {
  // Provider / 模型
  providers: ProviderInfo[];
  providerLoading: boolean;
  selectedProvider: string | null;
  selectedModel: string | null;
  apiKeys: Record<string, string>; // localStorage 持久化

  // 项目
  projectPath: string | null;
  sourcePath: string | null; // 源游戏目录（回写建议输出目录用）
  engineId: string | null;
  entries: EntryRow[];
  entriesLoading: boolean;
  fileFilter: string | null;
  statusFilter: StatusKey | null;

  // 翻译
  translateTask: TranslateTaskInfo | null;

  // 勾选（翻译范围）：片段级 Set；文件全选 = 该文件所有片段都勾选（三态在 UI 派生）
  selectedIds: ReadonlySet<string>;

  // UI
  statusMessage: string | null;
  busy: boolean;
  importProgress: { phase: string; pct: number } | null; // 导入阶段进度（覆盖层显示）

  // actions
  loadProviders: () => Promise<void>;
  setProvider: (id: string) => void;
  setModel: (model: string) => void;
  enableEngine: (providerId: string, model: string) => void; // CC-switch 式「启用某服务某模型」
  setApiKey: (providerId: string, key: string) => void;
  setStatus: (msg: string | null) => void;
  setBusy: (busy: boolean) => void;
  toggleEntry: (id: string) => void; // 勾/取消勾一个片段
  setFileSelection: (file: string, selected: boolean) => void; // 勾/取消勾整个文件
  setVisibleSelection: (ids: readonly string[], selected: boolean) => void; // 表头勾/取消勾当前筛选
  clearSelection: () => void;

  importGame: (dir: string, engineId: string) => Promise<void>;
  loadEntries: () => Promise<void>;
  startTranslate: () => Promise<void>;
  writeBack: (outputDir: string) => Promise<WriteBackResult>;
  setImportProgress: (p: { phase: string; pct: number } | null) => void;
  retranslateMismatched: () => Promise<void>;
  exportTranslations: () => Promise<void>;
  importTranslations: () => Promise<void>;
  setTranslateProgress: (taskId: string, status: string, done: number, total: number, message?: string | null) => void;
  setFileFilter: (file: string | null) => void;
  setStatusFilter: (status: StatusKey | null) => void;
}

const API_KEYS_STORAGE = "gametr.api_keys";

function loadKeys(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(API_KEYS_STORAGE) ?? "{}");
  } catch {
    return {};
  }
}

/** 项目文件路径：由核心计算（~/.gametr/projects/<游戏目录slug>.sqlite3）。
 * 前端不依赖 Tauri path API（homeDir/join 权限细节易出问题），路径统一核心算。 */
async function projectPathFor(dir: string): Promise<string> {
  const { path } = await rpc<{ path: string }>("project.default_path", { dir });
  return path;
}

export const useApp = create<AppState>((set, get) => ({
  providers: [],
  providerLoading: false,
  selectedProvider: null,
  selectedModel: null,
  apiKeys: loadKeys(),

  projectPath: null,
  sourcePath: null,
  engineId: null,
  entries: [],
  entriesLoading: false,
  fileFilter: null,
  statusFilter: null,

  translateTask: null,
  selectedIds: new Set<string>(),

  statusMessage: null,
  busy: false,
  importProgress: null,

  loadProviders: async () => {
    set({ providerLoading: true });
    try {
      const providers = await rpc<ProviderInfo[]>("providers.list");
      set((s) => {
        // 被删的 Provider 仍在选中（translate.start 会取不到）→ 回退到第一个可用
        const keepId = s.selectedProvider && providers.some((p) => p.provider_id === s.selectedProvider)
          ? s.selectedProvider : null;
        const pid = keepId ?? providers[0]?.provider_id ?? null;
        const p = providers.find((x) => x.provider_id === pid);
        const keepModel = keepId === s.selectedProvider
          && s.selectedModel && p?.models.includes(s.selectedModel)
          ? s.selectedModel : null;
        return {
          providers,
          selectedProvider: pid,
          selectedModel: keepModel ?? p?.models[0] ?? null,
        };
      });
    } catch (err) {
      set({ statusMessage: `加载 Provider 失败: ${err}` });
    } finally {
      set({ providerLoading: false });
    }
  },

  setProvider: (id) => {
    const p = get().providers.find((x) => x.provider_id === id);
    set({ selectedProvider: id, selectedModel: p?.models[0] ?? null });
  },
  setModel: (model) => set({ selectedModel: model }),
  // CC-switch 式启用：一次置 provider+model（翻译/重译真正用它），并回显
  enableEngine: (providerId, model) => {
    const p = get().providers.find((x) => x.provider_id === providerId);
    set({ selectedProvider: providerId, selectedModel: model });
    if (p) set({ statusMessage: `翻译引擎：${p.display_name} · ${model}` });
  },
  setApiKey: (providerId, key) => {
    const next = { ...get().apiKeys, [providerId]: key };
    localStorage.setItem(API_KEYS_STORAGE, JSON.stringify(next));
    set({ apiKeys: next });
  },
  setStatus: (statusMessage) => set({ statusMessage }),
  setBusy: (busy) => set({ busy }),

  // ---- 勾选（翻译范围，Set 每次整换触发响应） ----
  clearSelection: () => set({ selectedIds: new Set<string>() }),
  toggleEntry: (id) => {
    const next = new Set(get().selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    set({ selectedIds: next });
  },
  setFileSelection: (file, selected) => {
    const next = new Set(get().selectedIds);
    for (const e of get().entries) {
      if (e.file_path !== file) continue;
      if (selected) next.add(e.id); else next.delete(e.id);
    }
    set({ selectedIds: next });
  },
  setVisibleSelection: (ids, selected) => {
    const next = new Set(get().selectedIds);
    if (selected) { for (const id of ids) next.add(id); }
    else { for (const id of ids) next.delete(id); }
    set({ selectedIds: next });
  },

  importGame: async (dir, engineId) => {
    set({ busy: true, importProgress: { phase: "创建项目", pct: 20 }, selectedIds: new Set<string>() });
    try {
      const projPath = await projectPathFor(dir);
      // 重导复用已有项目（open），否则 create
      try {
        await rpc("project.open", { path: projPath });
      } catch {
        await rpc("project.create", { path: projPath, engine_id: engineId, source_path: dir });
      }
      set({ importProgress: { phase: "提取游戏文本", pct: 50 } });
      const extract = await rpc<{ extracted_count: number }>("extract.run");
      set({
        projectPath: projPath, sourcePath: dir, engineId,
        importProgress: { phase: `加载 ${extract.extracted_count} 条文本`, pct: 80 },
      });
      await get().loadEntries();
      set({ statusMessage: `提取到 ${extract.extracted_count} 条文本`, importProgress: null });
    } catch (err) {
      set({ statusMessage: `导入失败: ${err}`, importProgress: null });
    } finally {
      set({ busy: false });
    }
  },

  setImportProgress: (importProgress) => set({ importProgress }),

  loadEntries: async () => {
    if (!get().projectPath) return;
    set({ entriesLoading: true });
    try {
      const all: EntryRow[] = [];
      let page = 1;
      for (;;) {
        const pg = await rpc<{ items: Array<Record<string, unknown>>; total: number; page_size: number }>(
          "entries.list", { page, page_size: 2000 }
        );
        all.push(
          ...pg.items.map((e) => {
            const ctx = (() => {
              try { return JSON.parse(String(e.context_json ?? "{}")); } catch { return {}; }
            })() as { file_path?: string };
            return {
              id: String(e.id), source: String(e.source),
              translation: e.translation as string | null,
              status: Number(e.status), edited: Number((e as { edited?: unknown }).edited ?? 0),
              machine_text: (e.machine_text as string | null | undefined) ?? null,
              locator: String(e.locator),
              file_path: ctx.file_path ?? "",
            };
          })
        );
        if (page * pg.page_size >= pg.total) break;
        page += 1;
      }
      set({ entries: all, statusMessage: `共 ${all.length} 条文本` });
    } catch (err) {
      set({ statusMessage: `加载条目失败: ${err}` });
    } finally {
      set({ entriesLoading: false });
    }
  },

  startTranslate: async () => {
    const { selectedProvider, projectPath } = get();
    if (!projectPath) { set({ statusMessage: "请先导入游戏" }); return; }
    if (!selectedProvider) { set({ statusMessage: "请先在「模型 API」添加并选择 Provider" }); return; }
    set({ statusMessage: "启动翻译…" });
    try {
      // 勾选了片段 → 只翻所选（后端 scope=selection 仅取其中「未翻译+非已确认」条目）；
      // 没勾选 → 保持默认全量（断点续翻：只翻还没译的）
      const selected = get().selectedIds;
      const task = await rpc<{ task_id: string; total: number }>("translate.start", selected.size > 0
        ? { scope: "selection", ids: [...selected], provider_id: selectedProvider, model: get().selectedModel ?? undefined }
        : { scope: "all", provider_id: selectedProvider, model: get().selectedModel ?? undefined },
      );
      set({ translateTask: { task_id: task.task_id, status: "running", done: 0, total: task.total },
            statusMessage: `翻译任务已启动（共 ${task.total} 条）` });
    } catch (err) {
      set({ statusMessage: `翻译启动失败: ${err}` });
    }
  },

  setTranslateProgress: (taskId, status, done, total, message) => {
    set({
      translateTask: { task_id: taskId, status, done, total },
      statusMessage: message ?? (status === "done" ? `翻译完成（${done}/${total}）` : null),
    });
    // 完成/失败后刷新条目（译文已落库）
    if (status === "done" || status === "error") {
      void get().loadEntries();
    }
  },

  writeBack: async (outputDir): Promise<WriteBackResult> => {
    const { projectPath } = get();
    if (!projectPath) throw new Error("请先导入游戏");
    const dir = outputDir.trim();
    if (!dir) throw new Error("请填写输出目录");
    set({ busy: true, statusMessage: "回写中…（拷贝游戏 + 写入译文）" });
    try {
      const res = await rpc<WriteBackResult>("write_back.run", { output_dir: dir });
      set({ statusMessage: `回写完成：${res.written_count} 条译文，${res.warning_count} 条警告` });
      return res;
    } catch (err) {
      const msg = String(err);
      // 校验失败：给出人话提示（输出目录不能是源游戏目录）
      const friendly = msg.includes("源目录内")
        ? "输出目录不能是源游戏目录或其子目录（会破坏原游戏）。请改为独立的输出目录（如「源目录_zh」）。"
        : `回写失败: ${msg}`;
      set({ statusMessage: friendly });
      throw new Error(friendly);
    } finally {
      set({ busy: false });
    }
  },

  retranslateMismatched: async () => {
    const { entries, projectPath } = get();
    if (!projectPath) { set({ statusMessage: "请先导入游戏" }); return; }
    // 行数不匹配：译文换行数与原文不一致（AI 合并行导致，回写会跳过）
    const mismatched = entries.filter((e) => {
      if (!e.translation) return false;
      return e.source.split("\n").length !== e.translation.split("\n").length;
    });
    if (mismatched.length === 0) {
      set({ statusMessage: "没有行数不匹配的条目" });
      return;
    }
    set({ busy: true, statusMessage: `重新翻译 ${mismatched.length} 条行数不匹配的文本…` });
    try {
      const task = await rpc<{ task_id: string }>("translate.retranslate_entries", {
        ids: mismatched.map((e) => e.id),
      });
      // 后台任务：立即返回 task_id，progress 通知驱动进度与完成刷新（App 订阅）
      set({
        translateTask: { task_id: task.task_id, status: "running", done: 0, total: mismatched.length },
        statusMessage: `重新翻译 ${mismatched.length} 条（保持行数）…`,
      });
    } catch (err) {
      set({ statusMessage: `重译失败: ${err}` });
    } finally {
      set({ busy: false });
    }
  },

  exportTranslations: async () => {
    const { projectPath } = get();
    if (!projectPath) { set({ statusMessage: "请先导入游戏" }); return; }
    set({ busy: true, statusMessage: "导出翻译…" });
    try {
      const file = await save({
        defaultPath: "gametr-translation.json",
        filters: [{ name: "GameTR 翻译文件", extensions: ["json"] }],
      });
      if (typeof file !== "string") { set({ busy: false }); return; }
      const res = await rpc<{ path: string; count: number }>("translate.export", { path: file });
      set({ statusMessage: `已导出 ${res.count} 条译文（${res.path}）` });
    } catch (err) {
      set({ statusMessage: `导出失败: ${err}` });
    } finally {
      set({ busy: false });
    }
  },

  importTranslations: async () => {
    const { projectPath } = get();
    if (!projectPath) { set({ statusMessage: "请先导入游戏" }); return; }
    set({ busy: true, statusMessage: "导入翻译…" });
    try {
      const file = await open({
        filters: [{ name: "GameTR 翻译文件", extensions: ["json"] }],
      });
      if (typeof file !== "string") { set({ busy: false }); return; }
      const res = await rpc<{ imported: number; skipped: number; warnings: string[] }>(
        "translate.import", { path: file }
      );
      set({
        statusMessage: `已导入 ${res.imported} 条译文${res.skipped ? `，${res.skipped} 条未匹配` : ""}`,
      });
      await get().loadEntries();
    } catch (err) {
      set({ statusMessage: `导入失败: ${err}` });
    } finally {
      set({ busy: false });
    }
  },

  setFileFilter: (file) => set({ fileFilter: file }),
  setStatusFilter: (status) => set({ statusFilter: status }),
}));
