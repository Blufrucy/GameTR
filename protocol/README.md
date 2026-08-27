# protocol/ — 共享协议层

单一事实源，跨语言契约。**只加不改不删**；破坏性变更必须升 `api_version` 并加迁移层。

## 目录

- `schema/common.json` — IR 与 RPC 公共类型（EntryStatus、PingResult、Entry、DetectResult…）
- `schema/rpc-methods.json` — 全部 RPC 方法目录 + 错误码表（人机可读，不参与代码生成）
- `schema/plugin-manifest.json` — 插件 manifest（M2）
- `scripts/generate.mjs` — 生成器编排（`pnpm protocol`）
- `generated/` — 生成产物占位目录；按路线图生成命令，产物实际落盘在消费方：
  - Python → `core/gt_core/rpc/models.py`、`core/gt_core/plugin_manifest.py`
  - TS → `apps/desktop/src/rpc/models.ts`、`apps/desktop/src/rpc/plugin-manifest.ts`

## 重新生成

```shell
pnpm protocol
# 或
make protocol
```

前提：根 `uv sync` 已装 datamodel-code-generator；`apps/desktop` 已 `pnpm install` 装 json-schema-to-typescript。

CI 校验：跑生成后 `git diff --exit-code`，产物与提交物不一致则 CI 失败（防止手改/漏改）。
