/**
 * 模型 API 配置面板（M4）：常见服务预设 + 分步「测试连接 → 拉模型」+ 删除服务。
 *
 * 连接逻辑（配合后端 TCP/TLS ping 语义）：
 * - ① 测试连接 = providers.test：只做握手 ping（毫秒级，CC switch 式即时反馈）——
 *   不等慢服务端 HTTP 响应（旧实现 GET /models 会把服务端响应慢误报成连接慢 ~9s）
 * - ② 拉取模型列表（ping 通过后自动触发，独立 spinner）：慢属服务端 /models，不混入连通
 *   结果显示；端点无 /models 或失败时可手输模型名保存
 * - 模型下拉是可滚动自定义列表（WebView2 的 datalist 无法滚动，换自绘 combobox）
 * key 仅存本机 ~/.gametr/providers.json（MVP，正式版系统钥匙串）。
 */

import { useState, type ReactNode } from "react";
import { rpc } from "../rpc/client";
import { useApp, type ProviderInfo } from "../store/app";
import { Button, Modal } from "./ui";

/** 常见服务预设：点了自动填名称/标识/API 地址；local=true 的服务 key 可留空。 */
const PRESETS = [
  { name: "DeepSeek", base: "https://api.deepseek.com" },
  { name: "OpenAI", base: "https://api.openai.com/v1" },
  { name: "Kimi（月之暗面）", base: "https://api.moonshot.cn/v1" },
  { name: "智谱 GLM", base: "https://open.bigmodel.cn/api/paas/v4" },
  { name: "通义千问", base: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { name: "豆包（火山方舟）", base: "https://ark.cn-beijing.volces.com/api/v3" },
  { name: "Ollama（本地）", base: "http://127.0.0.1:11434/v1", local: true },
  { name: "LM Studio（本地）", base: "http://127.0.0.1:1234/v1", local: true },
] as const;

const GREEN = "#34c759";
const RED = "#ff5b5b";
const AMBER = "#ff9f0a";
const MUTED = "#8a8a92";

function slug(s: string): string {
  const out = s.toLowerCase().trim().replace(/[^a-z0-9一-鿿]+/g, "-").replace(/^-+|-+$/g, "");
  return out || "custom";
}

function pickDefaultModel(models: string[]): string {
  // 优先挑聊天/生成模型（跳过 embed/reasoner 等非翻译用途的），没有就取第一个
  const chat = models.find((m) =>
    /chat|v4|turbo|pro|lite|plus|glm|qwen|kimi|instruct|max|1\.5|2\.0|3\.0/i.test(m)
    && !/reasoner|embed|rerank|whisper|tts|audio|moderation/i.test(m));
  return chat ?? models[0] ?? "";
}

interface TestResult { ok: boolean; latencyMs: number | null; msg: string }

export function ApiKeyModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { providers, loadProviders, setStatus } = useApp();

  const [adding, setAdding] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);

  const [pinging, setPinging] = useState(false);
  const [ping, setPing] = useState<TestResult | null>(null); // ① 即时连通结果
  const [modelLoading, setModelLoading] = useState(false);   // ② 模型列表拉取（独立于 ping）
  const [modelError, setModelError] = useState<string | null>(null);
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);

  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, string>>({});
  const [confirmDel, setConfirmDel] = useState<string | null>(null);

  function resetAdd() {
    setDisplayName(""); setBaseUrl(""); setApiKey(""); setShowKey(false);
    setPing(null); setModelError(null); setModelLoading(false);
    setModel(""); setModels([]); setErr(null);
  }

  function applyPreset(p: (typeof PRESETS)[number]) {
    setAdding(true);
    setDisplayName(p.name); setBaseUrl(p.base); setApiKey("");
    setPing(null); setModelError(null); setModelLoading(false);
    setModel(""); setModels([]); setErr(null);
  }

  /** ② 拉取模型列表：慢属服务端 /models 响应，独立 spinner，不与「已连接」混为一谈。 */
  async function fetchModels(key: string | undefined) {
    setModelLoading(true); setModelError(null);
    try {
      const ml = await rpc<{ models: string[] }>("providers.models", {
        provider_id: slug(displayName || "custom"), base_url: baseUrl.trim(), api_key: key,
      });
      setModels(ml.models);
      setPing((p) => (p?.ok ? { ok: true, latencyMs: p.latencyMs, msg: `${p.msg} · ${ml.models.length} 个模型` } : p));
      setModel((prev) => (prev && ml.models.includes(prev) ? prev : pickDefaultModel(ml.models)));
    } catch (e) {
      // 401/402/403（key 无效/余额）在拉列表这一步才暴露：ping 不验 key
      setModelError(`模型列表拉取失败：${e}（可手输模型名保存；常见原因：key 无效或余额不足）`);
    } finally {
      setModelLoading(false);
    }
  }

  /** ① 测试连接 = 即时 ping（TCP/TLS 握手，毫秒级），不等服务端响应 → CC switch 式即时反馈。 */
  async function handleConnect() {
    const url = baseUrl.trim();
    if (!url || !/^https?:\/\//i.test(url)) {
      setPing({ ok: false, latencyMs: null, msg: "API 地址需以 http(s):// 开头" });
      return;
    }
    setPinging(true); setPing(null); setModelError(null); setErr(null);
    const key = apiKey.trim() || undefined;
    try {
      const res = await rpc<{ ok: boolean; latency_ms: number; message: string | null }>(
        "providers.test", { provider_id: slug(displayName || "custom"), base_url: url, api_key: key },
      );
      if (!res.ok) {
        setPing({ ok: false, latencyMs: null, msg: res.message ?? "连接失败" });
        return;
      }
      const ms = Math.round(res.latency_ms);
      setPing({ ok: true, latencyMs: ms, msg: `已连接 · ${ms}ms` });
      void fetchModels(key); // ping 秒回即显示「已连接」，模型列表随后异步填充
    } catch (e) {
      setPing({ ok: false, latencyMs: null, msg: String(e) });
    } finally {
      setPinging(false);
    }
  }

  /** 保存：base_url + 模型名必填，key 可留空（本地服务）。标识由名称推导，同名同地址=更新。 */
  async function handleSave() {
    const name = displayName.trim();
    const url = baseUrl.trim();
    const mdl = model.trim();
    if (!name || !url) { setErr("请填写服务名称与 API 地址（可从上方预设选）"); return; }
    if (!mdl) { setErr("请选择或输入模型名（测试通过后会自动填充，也可手输）"); return; }
    let pid = slug(name);
    // 派生 id 撞上「不同地址」的既有服务 → 加序号并存，避免悄悄覆盖
    let existing = providers.find((p) => p.provider_id === pid);
    let n = 2;
    while (existing && existing.base_url !== url) {
      pid = `${slug(name)}-${n++}`;
      existing = providers.find((p) => p.provider_id === pid);
    }
    setSaving(true); setErr(null);
    try {
      await rpc<ProviderInfo>("providers.configure", {
        provider_id: pid, base_url: url,
        display_name: name, models: [mdl],
        api_key: apiKey.trim() || undefined,
      });
      await loadProviders();
      setStatus(`已保存服务：${name}`);
      setAdding(false); resetAdd();
    } catch (e) {
      setErr(`保存失败: ${e}`);
    } finally {
      setSaving(false);
    }
  }

  /** 已配置服务：测试连接（用已存 key，后端 TCP/TLS ping，秒级即时）。 */
  async function handleTestProvider(p: ProviderInfo) {
    setTestingId(p.provider_id);
    try {
      const res = await rpc<{ ok: boolean; latency_ms: number; message: string | null }>(
        "providers.test", { provider_id: p.provider_id },
      );
      setTestResults((s) => ({
        ...s,
        [p.provider_id]: res.ok
          ? `已连接 · ${Math.round(res.latency_ms)}ms`
          : (res.message ?? "连接失败"),
      }));
    } catch (e) {
      setTestResults((s) => ({ ...s, [p.provider_id]: `失败: ${e}` }));
    } finally {
      setTestingId(null);
    }
  }

  async function handleRemove(p: ProviderInfo) {
    setConfirmDel(null);
    try {
      await rpc<{ removed: boolean }>("providers.remove", { provider_id: p.provider_id });
      await loadProviders();
      setStatus(`已删除服务：${p.display_name}`);
    } catch (e) {
      setStatus(`删除失败: ${e}`);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="模型 API 设置">
      <p style={{ margin: "0 0 12px", fontSize: 12, color: MUTED, lineHeight: 1.6 }}>
        接入翻译服务（DeepSeek/OpenAI/本地 Ollama 等，OpenAI 兼容）。选预设或自定义 → 填 key →
        点「① 测试连接」（毫秒级即时反馈）→ 通过后自动拉取可用模型，再「③ 保存」即可翻译。
        key 仅存本机（正式版用系统钥匙串）。
      </p>

      {/* 已配置服务 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {providers.map((p) => (
          <div key={p.provider_id} style={{ border: "1px solid #2a2a2e", borderRadius: 8, padding: "10px 12px", background: "#161619" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
              <div style={{ minWidth: 0 }}>
                <strong style={{ fontSize: 13 }}>{p.display_name}</strong>
                <div style={{ fontSize: 11, color: MUTED, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {p.base_url}
                </div>
                <div style={{ marginTop: 4, fontSize: 12, color: MUTED }}>
                  模型：{p.models.join("、") || "（未选）"}
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
                <div style={{ display: "flex", gap: 6 }}>
                  <Button variant="ghost" onClick={() => handleTestProvider(p)} disabled={testingId === p.provider_id}>
                    {testingId === p.provider_id ? "测试中…" : "测试连接"}
                  </Button>
                  <button
                    onClick={() => {
                      if (confirmDel !== p.provider_id) { setConfirmDel(p.provider_id); return; }
                      void handleRemove(p);
                    }}
                    onBlur={() => setConfirmDel((c) => (c === p.provider_id ? null : c))}
                    disabled={testingId === p.provider_id}
                    style={{
                      background: "transparent",
                      border: confirmDel === p.provider_id ? "1px solid #5b2323" : "1px solid transparent",
                      color: confirmDel === p.provider_id ? "#ff9b9b" : MUTED,
                      borderRadius: 6, padding: "5px 10px", fontSize: 13,
                      cursor: "pointer", whiteSpace: "nowrap",
                    }}
                  >
                    {confirmDel === p.provider_id ? "确认删除？" : "删除"}
                  </button>
                </div>
                {testResults[p.provider_id] && (
                  <span style={{ fontSize: 12, color: testResults[p.provider_id].startsWith("已连接") ? GREEN : RED }}>
                    {testResults[p.provider_id]}
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
        {providers.length === 0 && (
          <p style={{ fontSize: 13, color: MUTED, margin: 0 }}>尚未添加服务，选个预设或点下方「+ 添加服务」。</p>
        )}
      </div>

      {/* 添加 / 更新服务 */}
      <div style={{ marginTop: 14 }}>
        <Button onClick={() => { setAdding(!adding); if (adding) resetAdd(); }}>
          {adding ? "收起" : "+ 添加服务"}
        </Button>

        {adding && (
          <div style={{ marginTop: 10, border: "1px solid #2a2a2e", borderRadius: 8, padding: "12px", background: "#161619" }}>
            <div style={{ marginBottom: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
              {PRESETS.map((p) => (
                <button
                  key={p.name}
                  onClick={() => applyPreset(p)}
                  style={{
                    background: displayName === p.name ? "#1f3a55" : "#26262a",
                    border: displayName === p.name ? "1px solid #2d6cdf" : "1px solid #3a3a40",
                    color: displayName === p.name ? "#9cc9ff" : "#e6e6e8",
                    borderRadius: 999, padding: "3px 12px", fontSize: 12, cursor: "pointer",
                  }}
                >
                  {p.name}
                </button>
              ))}
            </div>

            <Field label="名称" value={displayName} onChange={setDisplayName} placeholder="如 DeepSeek" />
            <Field label="API 地址" value={baseUrl} onChange={setBaseUrl} placeholder="https://api.deepseek.com" mono />
            <Field
              label="API Key"
              value={apiKey}
              onChange={setApiKey}
              placeholder="sk-…（云端服务必填；本地 Ollama/LM Studio 可留空）"
              password={!showKey}
              trailing={
                <button
                  onClick={() => setShowKey(!showKey)}
                  title={showKey ? "隐藏 key" : "显示 key"}
                  style={{ background: "none", border: "none", color: MUTED, cursor: "pointer", fontSize: 12 }}
                >
                  {showKey ? "隐藏" : "显示"}
                </button>
              }
            />

            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
              <Button variant="primary" onClick={handleConnect} disabled={pinging}>
                {pinging ? "测试中…" : "① 测试连接"}
              </Button>
              {ping && (
                <span style={{ fontSize: 12, color: ping.ok ? GREEN : RED }}>{ping.msg}</span>
              )}
              {modelLoading && (
                <span style={{ fontSize: 12, color: MUTED }}>拉取模型列表中…</span>
              )}
            </div>

            <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap", alignItems: "flex-start" }}>
              <ModelPicker
                value={model}
                models={models}
                onChange={setModel}
                placeholder="② 模型（点下方 chip 选择，也可手输）"
              />
              {!modelLoading && !modelError && models.length > 1 && (
                <span style={{ marginTop: 9, fontSize: 11, color: MUTED }}>{models.length} 个可选</span>
              )}
              {modelError && (
                <span style={{ fontSize: 11, color: AMBER, flexBasis: "100%", wordBreak: "break-all" }}>
                  {modelError}
                  {ping?.ok && (
                    <button
                      onClick={() => void fetchModels(apiKey.trim() || undefined)}
                      style={{
                        marginLeft: 8, background: "none", border: "none", color: "#9cc9ff",
                        cursor: "pointer", fontSize: 11, textDecoration: "underline", padding: 0,
                      }}
                    >
                      重试拉取
                    </button>
                  )}
                </span>
              )}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
              <Button variant="primary" onClick={handleSave} disabled={saving}>
                {saving ? "保存中…" : "③ 保存"}
              </Button>
              {err && <span style={{ fontSize: 12, color: AMBER, wordBreak: "break-all" }}>{err}</span>}
            </div>
          </div>
        )}
      </div>

      <div style={{ marginTop: 16, display: "flex", justifyContent: "flex-end" }}>
        <Button onClick={onClose} variant="primary">完成</Button>
      </div>
    </Modal>
  );
}

function Field({
  label, value, onChange, placeholder, password, mono, trailing,
}: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; password?: boolean; mono?: boolean; trailing?: ReactNode;
}) {
  return (
    <label style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center", marginTop: 6 }}>
      <span style={{ width: 64, flexShrink: 0 }}>{label}</span>
      <input
        type={password ? "password" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          flex: 1, background: "#1c1c1f", color: "#e6e6e8", border: "1px solid #3a3a40",
          borderRadius: 6, padding: "6px 10px", fontSize: 13,
          fontFamily: password || mono ? "ui-monospace, monospace" : undefined,
        }}
      />
      {trailing}
    </label>
  );
}

/**
 * 模型选择：可用模型以内联 chip **始终平铺**在输入框下方，点哪个选哪个（多模型全可见）。
 *
 * 不依赖弹层/下拉滚动 —— WebView2 里 datalist 弹层滚不动、absolute 下拉会被弹窗滚动容器
 * 抢滚轮/裁剪（用户实测多选时后面的模型看不见），两个坑都避掉：chip 在文档流里，
 * 选第三个就是点第三颗钮，无滚动参与。输入框仍可手输任意模型名（端点无 /models 兜底）。
 */
function ModelPicker({
  value, models, onChange, placeholder,
}: {
  value: string; models: string[]; onChange: (v: string) => void; placeholder: string;
}) {
  const many = models.length > 12; // 模型很多（本地 LM Studio）才限高内滚，通常平铺即可
  return (
    <div style={{ flex: 1, minWidth: 220 }}>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          width: "100%", boxSizing: "border-box", background: "#1c1c1f", color: "#e6e6e8",
          border: "1px solid #3a3a40", borderRadius: 6, padding: "6px 10px", fontSize: 13,
        }}
      />
      {models.length > 0 && (
        <div
          style={{
            display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6,
            maxHeight: many ? 116 : undefined, overflowY: many ? "auto" : undefined,
            overscrollBehavior: "contain",
          }}
        >
          {models.map((m) => {
            const active = m === value;
            return (
              <button
                key={m}
                onClick={() => onChange(m)}
                title={m}
                style={{
                  background: active ? "#1f3a55" : "#26262a",
                  border: active ? "1px solid #2d6cdf" : "1px solid #3a3a40",
                  color: active ? "#9cc9ff" : "#e6e6e8",
                  borderRadius: 999, padding: "3px 10px", fontSize: 12, cursor: "pointer",
                  maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis",
                }}
              >
                {m}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
