# Spike 2：RPG Maker MV 数据文件往返保真（路线图 1.4）

**目标**：验证读 → 改 → 写回的字节级保真，找出能 100% 还原原格式的 `json.dumps` 参数组合。
结论（含"为什么是这个格式"的分析）已写入 **ADR-0004**。

## 已在本仓库验证的结论（合成样本）

对程序化生成的最小 MV 样本（Map001/Actors/Items/System），**全部文件**找到同一组零差异参数：

```
indent=2  ensure_ascii=False  sort_keys=False  separators=默认(', ', ': ')  trailing_newline=False
```

即等价于 JS `JSON.stringify(obj, null, 2)`：
- 2 空格缩进
- 非 ASCII 字符**不转义**（直接 UTF-8 输出，中文/日文原样）
- key 保持插入顺序（不排序）
- 无尾随换行

带占位符文本（`\N[1]` 等）往返同样 0 差异。**这对 M2 写回器意味着**：写回时直接 `json.dumps(data, indent=2, ensure_ascii=False)`，无需额外的转义处理。

## 待验证（真实 MV 工程）

合成样本的格式是我们假定的 MV 编辑器格式。真实 RPG Maker MV 编辑器产出的文件需用同一脚本验证：

```bash
python roundtrip.py --dir "<真实游戏>/www/data"
```

若输出"未找到零差异组合"，把该文件的参数搜索结果发到 ADR-0004 更新结论
（真实 MV 可能还有未知细节：前导空格、文件头 BOM、特殊转义等）。

## 文件

- `make_sample.py` — 程序化生成最小样本工程（版权干净、可重建，输出 `sample_game/` 已 gitignore）
- `roundtrip.py` — 参数空间搜索 + 修改写回 + 字节 diff 验证

## 用法

```bash
python make_sample.py --out sample_game                    # 生成样本
python roundtrip.py --dir sample_game/www/data             # 空往返：找零差异参数
python roundtrip.py --file .../Map001.json \
    --edit '$.events[0].pages[0].list[1].parameters[0]' '新文本' --diff   # 改一条并验证
```

## 与 M2 的关系

- locator 用 JSON Pointer 风格（`$.events[0].pages[0].list[1].parameters[0]`，见 roundtrip.py 解析器）
- M2 黄金样本三类测试中的"空翻译往返 diff=0"就是本 spike 的自动版
