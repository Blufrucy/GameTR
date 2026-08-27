# GameTR 常用任务入口（pnpm scripts 是主入口，Makefile 提供类 Unix 别名）
# 本地 CI gate 对齐 .github/workflows/ci.yml 的 core/frontend/rust 三 job，
# GitHub Actions 计费锁定时用 `make ci` 在本地跑全套质量门。
.PHONY: protocol core-test sidecar lint ci protocol-check \
	lint-core lint-frontend core-gate frontend-gate rust-gate

protocol:
	pnpm protocol

core-test:
	uv run pytest

sidecar:
	uv run python core/scripts/build_sidecar.py

# ---------- 本地 CI gate（对齐 ci.yml 三 job） ----------

lint-core:
	uv run ruff check core/gt_core core/tests
	uv run mypy

lint-frontend:
	pnpm --filter desktop lint

# 协议生成物必须与提交一致（生成后 git diff 判空）
protocol-check:
	pnpm protocol
	git diff --exit-code -- core/gt_core/rpc/models.py core/gt_core/plugin_manifest.py \
		apps/desktop/src/rpc/models.ts apps/desktop/src/rpc/plugin-manifest.ts

core-gate: lint-core core-test protocol-check

frontend-gate: lint-frontend
	pnpm --filter desktop typecheck
	pnpm --filter desktop test
	pnpm --filter desktop build

rust-gate:
	cargo clippy --manifest-path apps/desktop/src-tauri/Cargo.toml -- -D warnings
	cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
	cargo build --manifest-path apps/desktop/src-tauri/Cargo.toml

ci: core-gate frontend-gate rust-gate

lint: lint-core lint-frontend
