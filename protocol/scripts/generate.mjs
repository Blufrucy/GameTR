#!/usr/bin/env node
/**
 * 从 protocol/schema/ 生成消费方模型（单入口：pnpm protocol 或 make protocol）。
 *
 * 生成物：
 *   - Python: core/gt_core/rpc/models.py          （common.json 类型）
 *   - Python: core/gt_core/plugin_manifest.py      （插件 manifest）
 *   - TS:     apps/desktop/src/rpc/models.ts       （common.json 类型）
 *   - TS:     apps/desktop/src/rpc/plugin-manifest.ts
 *
 * 纪律：生成物禁止手改；CI 校验生成物与提交物一致（git diff 判空）。
 * rpc-methods.json 是方法目录（人机可读契约），不参与代码生成。
 */
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const schema = path.join(root, "protocol", "schema");
const common = path.join(schema, "common.json");
const manifest = path.join(schema, "plugin-manifest.json");

const run = (cmd) => {
  console.log(`$ ${cmd}`);
  execSync(cmd, { cwd: root, stdio: "inherit" });
};

// uv 在 PATH 上优先，否则退回 python -m uv（Windows 下 uv 可能未被加入 PATH）
const uvCmd = (() => {
  try {
    execSync("uv --version", { stdio: "ignore" });
    return "uv";
  } catch {
    return "python -m uv";
  }
})();

// Python 生成（含枚举重命名/根模型清理后处理）复用独立脚本，CI 也用它保证一致
const python = () =>
  run(`${uvCmd} run python protocol/scripts/gen_python_models.py`);

const ts = (input, output) =>
  run(
    `pnpm --filter desktop exec json2ts --input "${input}" ` +
      `--output "${path.join(root, output)}" --cwd "${root}" --unreachableDefinitions`
  );

console.log("== 生成 Python 模型 ==");
python();

console.log("== 生成 TS 模型 ==");
ts(common, "apps/desktop/src/rpc/models.ts");
ts(manifest, "apps/desktop/src/rpc/plugin-manifest.ts");

console.log("== 完成：生成物已更新，提交前请 git diff 确认 ==");
