/**
 * 模型 API 配置面板（M4）：纯用户配置，无内置默认。
 *
 * 流程（用户指定）：添加 API（名称/地址/key）→ 测试连通性 → 获取模型（自动填充下拉）
 * → 选择模型 → 保存。保存走 providers.configure（核心持久化 ~/.gametr/providers.json）。
 */

import { useState } from "react";
import { rpc } from "../rpc/client";
import { useApp, type ProviderInfo } from "../store/app";
import { Button, Modal, Select } from "./ui";

export function ApiKeyModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { providers, loadProviders } = useApp();

  const [adding, setAdding] = useState(false);
  const [providerId, setProviderId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");

  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [fetchingModels, setFetchingModels] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const [testResults, setTestResults] = useState<Record<string, string>>({});
  const [testingId, setTestingId] = useState<string | null>(null);

  function resetForm() {
    setProviderId(""); setDisplayName(""); setBaseUrl(""); setApiKey("");
    setTestResult(null); setModels([]); setSelectedModel(""); setMsg(null);
  }

  /** 测试连通性：验证 base_url + key 有效（未保存的临时配置，传 base_url）。 */
  async function handleTest() {
    const pid = providerId.trim();
    if (!pid || !baseUrl.trim() || !apiKey.trim()) {
      setTestResult({ ok: false, msg: "请先填写 Provider 名称、API 地址、API key" });
      return;
    }
    setTesting(true); setTestResult(null);
    try {
      const res = await rpc<{ ok: boolean; latency_ms: number; message: string | null }>(
        "providers.test", { provider_id: pid, api_key: apiKey.trim(), base_url: baseUrl.trim() }
      );
      setTestResult(
        res.ok
          ? { ok: true, msg: `连通 ✓ ${Math.round(res.latency_ms)}ms` }
          : { ok: false, msg: res.message ?? "连接失败" }
      );
    } catch (err) {
      setTestResult({ ok: false, msg: `失败: ${err}` });
    } finally {
      setTesting(false);
    }
  }

  /** 获取模型：调 /models 端点，自动填充下拉（未保存临时配置，传 base_url）。 */
  async function handleFetchModels() {
    const pid = providerId.trim();
    if (!pid || !baseUrl.trim() || !apiKey.trim()) {
      setMsg("请先填写 Provider 名称、API 地址、API key 再获取模型");
      return;
    }
    setFetchingModels(true); setMsg(null);
    try {
      const res = await rpc<{ models: string[] }>("providers.models", {
        provider_id: pid, api_key: apiKey.trim(), base_url: baseUrl.trim(),
      });
      if (!res.models.length) {
        setMsg("端点返回空模型列表，请检查 API 地址是否正确（需 OpenAI 兼容 /models）");
      }
      setModels(res.models);
      setSelectedModel(res.models[0] ?? "");
    } catch (err) {
      setMsg(`获取模型失败: ${err}`);
    } finally {
      setFetchingModels(false);
    }
  }

  /** 保存：configure 持久化（含模型列表 + key）。 */
  async function handleSave() {
    const pid = providerId.trim();
    if (!pid || !baseUrl.trim()) { setMsg("请填写 Provider 名称与 API 地址"); return; }
    if (!selectedModel) { setMsg("请先②获取模型并③选择一个模型（保存需要有效模型名）"); return; }
    setSaving(true); setMsg(null);
    try {
      await rpc<ProviderInfo>("providers.configure", {
        provider_id: pid,
        base_url: baseUrl.trim(),
        display_name: displayName.trim() || pid,
        models: [selectedModel],
        api_key: apiKey.trim() || undefined,
      });
      await loadProviders();
      setAdding(false); resetForm();
    } catch (err) {
      setMsg(`保存失败: ${err}`);
    } finally {
      setSaving(false);
    }
  }

  /** 已配置 Provider 测试连接。 */
  async function handleTestProvider(p: ProviderInfo) {
    setTestingId(p.provider_id);
    try {
      const res = await rpc<{ ok: boolean; latency_ms: number; message: string | null }>(
        "providers.test", { provider_id: p.provider_id }
      );
      setTestResults((s) => ({
        ...s,
        [p.provider_id]: res.ok ? `连通 ✓ ${Math.round(res.latency_ms)}ms` : (res.message ?? "失败"),
      }));
    } catch (err) {
      setTestResults((s) => ({ ...s, [p.provider_id]: `失败: ${err}` }));
    } finally {
      setTestingId(null);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="模型 API 设置">
      <p style={{ margin: "0 0 12px", fontSize: 12, color: "#8a8a92" }}>
        添加翻译服务（如 DeepSeek）：填 API 地址和 key → 测试连通 → 获取模型 → 选择 → 保存。
        key 仅保存在本机（正式版用系统钥匙串）。
      </p>

      {/* 已配置 Provider */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {providers.map((p) => (
          <div key={p.provider_id} style={{ border: "1px solid #2a2a2e", borderRadius: 8, padding: "10px 12px", background: "#161619" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <strong style={{ fontSize: 13 }}>{p.display_name}</strong>
              <span style={{ fontSize: 11, color: "#8a8a92" }}>{p.base_url}</span>
            </div>
            <div style={{ marginTop: 6, fontSize: 12, color: "#8a8a92" }}>
              模型：{p.models.join("、") || "（未选）"}
            </div>
            <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 10 }}>
              <Button variant="ghost" onClick={() => handleTestProvider(p)} disabled={testingId === p.provider_id}>
                {testingId === p.provider_id ? "测试中…" : "测试连接"}
              </Button>
              {testResults[p.provider_id] && (
                <span style={{ fontSize: 12, color: testResults[p.provider_id].startsWith("连通") ? "#34c759" : "#ff5b5b" }}>
                  {testResults[p.provider_id]}
                </span>
              )}
            </div>
          </div>
        ))}
        {providers.length === 0 && (
          <p style={{ fontSize: 13, color: "#8a8a92" }}>尚未添加翻译服务，点击下方「+ 添加 API」。</p>
        )}
      </div>

      {/* 添加 API */}
      <div style={{ marginTop: 14 }}>
        <Button onClick={() => { setAdding(!adding); if (!adding) resetForm(); }}>
          {adding ? "收起" : "+ 添加 API"}
        </Button>

        {adding && (
          <div style={{ marginTop: 10, border: "1px solid #2a2a2e", borderRadius: 8, padding: "12px", display: "flex", flexDirection: "column", gap: 8, background: "#161619" }}>
            <Field label="名称" value={displayName} onChange={setDisplayName} placeholder="如 DeepSeek" />
            <Field label="Provider ID" value={providerId} onChange={setProviderId} placeholder="如 deepseek（唯一标识）" />
            <Field label="API 地址" value={baseUrl} onChange={setBaseUrl} placeholder="https://api.deepseek.com" />
            <Field label="API Key" value={apiKey} onChange={setApiKey} placeholder="sk-..." password />

            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <Button variant="primary" onClick={handleTest} disabled={testing}>
                {testing ? "测试中…" : "① 测试连通性"}
              </Button>
              {testResult && (
                <span style={{ fontSize: 12, color: testResult.ok ? "#34c759" : "#ff5b5b" }}>
                  {testResult.msg}
                </span>
              )}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <Button onClick={handleFetchModels} disabled={fetchingModels || !testResult?.ok}>
                {fetchingModels ? "获取中…" : "② 获取模型"}
              </Button>
              {models.length > 0 && (
                <Select
                  label="③ 选择模型"
                  value={selectedModel}
                  onChange={setSelectedModel}
                  options={models.map((m) => ({ value: m, label: m }))}
                />
              )}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Button variant="primary" onClick={handleSave} disabled={saving}>
                {saving ? "保存中…" : "④ 保存"}
              </Button>
              {msg && <span style={{ fontSize: 12, color: "#ff9f0a" }}>{msg}</span>}
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
  label, value, onChange, placeholder, password,
}: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; password?: boolean;
}) {
  return (
    <label style={{ fontSize: 13, display: "flex", gap: 6, alignItems: "center" }}>
      {label}
      <input
        type={password ? "password" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          flex: 1, background: "#1c1c1f", color: "#e6e6e8", border: "1px solid #3a3a40",
          borderRadius: 6, padding: "6px 10px", fontSize: 13, fontFamily: password ? "monospace" : undefined,
        }}
      />
    </label>
  );
}
