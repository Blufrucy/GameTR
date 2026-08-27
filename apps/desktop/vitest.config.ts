// vitest 配置（M0：纯单元测试，node 环境即可）
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    // M4 双栏编辑器之前还没有前端单测，先放行空测试集
    passWithNoTests: true,
  },
});
