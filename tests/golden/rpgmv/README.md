# 黄金样本（M2 发布闸门，路线图 2.3）

RPGMV 插件的端到端往返测试套件，**红了禁止发版**。

## 结构

- `make_sample.py` — `generate(root)` 程序化生成最小样本工程（版权干净、可重建、
  **真实格式**：JS 紧凑 + null 数组展开，用 `serializer.serialize_rpgm`）
- `serializer.py` — 重序列化器（ADR-0004 降级产物）：生成样本 + 校验"空翻译 diff=0"
- `expected.json` — extract 结果快照（提交入库，与生成样本逐字段比对）
- `test_golden.py` — 三类测试：
  1. **快照**：extract(样本) == expected.json
  2. **空翻译往返**：translation=source 原样回写 → data 文件二进制 diff=0
  3. **带翻译往返**：改译文 → 回写 → 重新 extract → 译文一致
- `_gen_expected.py` — 重新生成 expected.json 的开发工具（extract 逻辑变更后跑一次）
- `sample/` — 生成的样本工程（gitignore，不入库）

## 用法

```bash
# 跑三测试（随全量 pytest）
uv run pytest tests/golden/rpgmv/ -v

# extract 逻辑变更后重生成快照
uv run python tests/golden/rpgmv/_gen_expected.py
```

## 依赖

插件本体在 `plugins/rpgmv/`（`tests/golden/rpgmv/` 不复制插件，测试用
`PluginManager` 从仓库根 `plugins/` 加载，验证的是真实加载路径）。
