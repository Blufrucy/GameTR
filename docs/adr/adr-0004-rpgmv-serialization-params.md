# ADR-0004：RPG Maker MV 数据文件序列化参数

- 状态：已接受（基于合成样本；真实 MV 工程待补验）
- 日期：2026-08-27
- 来源：M0 Spike 2（tests/spikes/spike2_rpgmv/）
- 相关：ADR-0003（opaque locators）、M2 的 write_back

## 背景（Context）

RPG Maker MV 游戏数据文件（`www/data/*.json`）由官方编辑器生成。回写译文时若序列化参数与原格式不一致，会产生字节级 diff（缩进/转义/key 顺序/中文转义不同），破坏"空翻译往返 diff=0"这一黄金样本硬闸门。

## 决策（Decision）

**回写参数 = `json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False, 默认分隔符, 无尾换行)`**，等价于 JS `JSON.stringify(obj, null, 2)`。

## 实测结论（Spike 2，合成样本）

对程序化生成的 4 个数据文件（Map001/Actors/Items/System），穷举参数空间（indent∈{1,2,3,4} × ensure_ascii × sort_keys × 分隔符 × 尾换行 = 64 组合），全部文件零差异命中同一组参数：

| 参数 | 值 |
| :--- | :--- |
| indent | 2（2 空格缩进） |
| ensure_ascii | **False**（中文/日文原样 UTF-8，不转 `\uXXXX`） |
| sort_keys | False（保持插入顺序） |
| separators | 默认 `(', ', ': ')` |
| trailing_newline | False |

带占位符文本（`\N[1]` 等）修改 → 写回 → 还原后字节 diff **0 差异**（已验证两次）。

> 注：System.json 搜到 2 个命中（另一组合为 ASCII-only 内容下 ensure_ascii 不产生差异的特例），推荐组合统一取上述参数。

## 验证方法与待办

- 真实 MV 编辑器产出的文件可能含未知细节（BOM、前导空格、特殊转义），**须用同一脚本验证**：
  ```bash
  python tests/spikes/spike2_rpgmv/roundtrip.py --dir "<真实游戏>/www/data"
  ```
- 若输出"未找到零差异组合"，回填更新本 ADR 并调整 M2 写回器

## 后果（Consequences）

- M2 write_back 用上述参数组合，黄金样本"空翻译往返 diff=0"作为硬性发布闸门（路线图工程纪律）
- 写回器永不写原目录（输出到用户指定目录 + 拷贝整个游戏）
