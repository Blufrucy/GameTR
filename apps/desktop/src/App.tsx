import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import "./App.css";

interface PingInfo {
  pong: boolean;
  version: string;
  pid: number;
  ts: number;
  roundtrip_ms: number;
}

function App() {
  const [ping, setPing] = useState<PingInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const doPing = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await invoke<PingInfo>("core_ping");
      setPing(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    doPing();
  }, [doPing]);

  return (
    <main className="container">
      <h1>GameTR</h1>
      <p>Python 核心 sidecar 健康检查（M0 Spike 1）</p>
      <button onClick={doPing} disabled={loading}>
        {loading ? "Ping…" : "重新 Ping"}
      </button>

      {ping && (
        <div className="ping-info">
          <p>
            ✅ {ping.pong ? "pong" : "?"} · 核心版本 {ping.version} · pid {ping.pid}
          </p>
          <p>
            往返耗时：<strong>{ping.roundtrip_ms} ms</strong>
          </p>
        </div>
      )}
      {error && <p className="ping-error">⚠️ {error}</p>}

      <p className="hint">
        杀掉 gt-core 进程 → GUI 自动重启并恢复（M0 验收③）。在任务管理器结束
        pid {ping?.pid ?? "—"} 后再点“重新 Ping”。
      </p>
    </main>
  );
}

export default App;
