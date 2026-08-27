# 生成产物：禁止手改

按 `protocol/README.md` 中的生成命令，产物实际落盘位置：

- Python → `core/gt_core/rpc/models.py`、`core/gt_core/plugin_manifest.py`
- TS → `apps/desktop/src/rpc/models.ts`、`apps/desktop/src/rpc/plugin-manifest.ts`

CI 会校验生成物与提交物一致。
