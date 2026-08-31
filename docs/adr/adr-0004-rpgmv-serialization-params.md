# ADR-0004：RPG Maker 数据文件 write_back 算法

- 状态：已接受（**2026-08-28 真实 MZ + MV 双工程验证**，推翻合成样本假设；同日后确立字节替换为正解）
- 日期：2026-08-27（初稿，合成样本）、2026-08-28（MZ 定稿）、2026-08-28（MV 扩展 + 字节替换正解）
- 来源：M0 Spike 2（tests/spikes/spike2_rpgmv/）
- 相关：ADR-0003（opaque locators / 字节区间）、M2 的 write_back

## 背景（Context）

RPG Maker 游戏数据文件（MV `www/data/*.json`、MZ 部署版 `data/*.json`）由官方编辑器/引擎/第三方插件生成。回写译文时若序列化格式与原文件不一致，会产生字节级 diff（缩进/转义/key 顺序/换行装饰不同），破坏"空翻译往返 diff=0"这一黄金样本硬闸门。

## 决策（Decision）

**write_back 的正解 = 字节区间替换**（tests/spikes/spike2_rpgmv/roundtrip.py 的 `locate_strings` / `apply_text_swap`）：

1. **extract**：用带字节区间的解析器 `locate_strings(原文字节)` 遍历文件，记录每个字符串字面量的
   字节区间 `[start, end)`（字符偏移、含引号），随条目存 `context_json.byte_range`（见 ADR-0003）。
2. **write_back**：`apply_text_swap` 只替换被翻译字符串的字面量区间
   （`原文[:start] + json.dumps(译文, ensure_ascii=False) + 原文[end:]`），**其余字节原样保留**。
3. **格式免疫**：不重序列化，就不需要复刻引擎 serializer —— CRLF / 空数组展开 / 缩进 / 插件怪癖
   天然保留，翻译器对任何引擎、任何格式变体免疫。

### 为什么不是重序列化（serialize_rpgm）

重序列化要求 100% 复刻每个引擎每个版本的 serializer。真实 MV 工程就逼出三种怪癖
（空 events 数组展开、Map001 是 CRLF、插件 Doodads.json 是 indent=2），不可穷尽。
故 `serialize_rpgm`（`detect_style` + 重序列化）**降级为黄金样本校验器**：
验证「不改任何文本时重写 == 原文」，用作测试参照，不作为 write_back 实现。

## 实测结论（真实工程，2026-08-28）

| 验证项 | MZ oriontest（rmmz 1.9.0） | MV False Awakening（NW.js） |
| :--- | :--- | :--- |
| 文件数 | 17 | 83 |
| `serialize_rpgm` 全字节复刻（校验器） | **17/17** | **83/83** |
| 字节区间替换：定位精确 + 替换无损 | **17580 字符串全对**（两工程合计） | 同左 |
| 修改写回 → 还原 → 字节 diff | **0 差异** | **98 文件 0 差异**（2 个纯 null 值文件跳过） |
| 带 BOM 吗 | 否 | 否 |

关键事实（字节区间替换为何是正解的证据）：
- **空 events 数组**（无事件地图）在 MV 里展开为 `[\n]`，同文件 `encounterList:[]` 却内联——空数组是否
  展开依赖具体数组。重序列化要探测 `expand_paths` 才能复刻；字节替换根本不关心。
- **换行符不统一**：Map001 是 CRLF、其余 LF。重序列化要探测 newline；字节替换不碰。
- **插件文件**：Doodads.json 整文件 `indent=2`。重序列化要降级；字节替换不碰。
- **17580 个字符串**逐一验证：字节区间定位精确、替换后 JSON 可解析且只有目标值变、其余字节逐字不变。

## 与合成样本假设的差异（重要，勿回归旧假设）

- ❌ 旧假设 `indent=2`（`JSON.stringify(obj, null, 2)`）**对真实 MV/MZ 编辑器文件是错的**。真实格式是
  **紧凑 + null 数组展开换行**。（`indent=2` 只出现在第三方插件文件，如 Doodads.json。）
- ❌ 旧假设"固定 `\n`"**对 CRLF 文件是错的**。
- ❌ 旧方向"write_back 用重序列化"**已推翻**——改用字节区间替换（格式免疫）。
- ✅ 仍正确：`ensure_ascii=False`（中文/日文原样 UTF-8）、`sort_keys=False`（插入序）、无尾换行、无 BOM。
- 合成样本（make_sample.py）用 `serialize_rpgm` 生成，作为黄金样本校验器的输入（非 write_back 目标）。

## 验证方法与待办

```bash
python tests/spikes/spike2_rpgmv/make_sample.py --out sample_game   # 重建样本（真实格式）
python tests/spikes/spike2_rpgmv/roundtrip.py --dir "<真实游戏>/data"  # 空往返校验（重序列化 diff=0）
python tests/spikes/spike2_rpgmv/roundtrip.py --file <file> --edit '$.a.b' '新文本' --diff  # 字节替换
```

- **加密游戏（.rpgmvp/.rpgmvo）**：先解密 → 字节替换 → 再加密，仍适用；解密后格式待实测。

## 后果（Consequences）

- M2 write_back 用 `locate_strings` + `apply_text_swap`（字节区间替换）；`serialize_rpgm` 仅作黄金样本校验器。
- **extract 必须保留原文**：字节区间依赖原文字节，extract 时算好并写入 `context_json.byte_range`。
- 占位符文本（`\N[1]` 等）经 `ensure_ascii=False` 原样 UTF-8，字节替换不引入转义差异。
- 写回器永不写原目录（输出到用户指定目录 + 拷贝整个游戏）。
