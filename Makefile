# GameTR 常用任务入口（pnpm scripts 是主入口，Makefile 提供类 Unix 别名）
.PHONY: protocol core-test sidecar lint ci

protocol:
	pnpm protocol

core-test:
	uv run pytest

sidecar:
	uv run python core/scripts/build_sidecar.py

lint:
	uv run ruff check core/gt_core core/tests
	pnpm --filter desktop lint

ci: lint core-test
