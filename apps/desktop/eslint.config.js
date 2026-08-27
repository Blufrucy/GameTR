// ESLint 9 flat config（TypeScript + React）
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    // 构建产物、Rust 壳、协议生成物都不参与 lint
    ignores: ["dist/", "src-tauri/", "src/rpc/*.ts"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      globals: globals.browser,
    },
  }
);
