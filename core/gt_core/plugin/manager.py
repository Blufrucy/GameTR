"""插件加载器：目录扫描 + manifest 校验 + API 版本契约 + 加载即测试（路线图 2.1）。

设计要点：
- 插件目录 = `{cwd}/plugins/<engine>/*` + 环境变量 `GAMETR_PLUGIN_DIR`（os.pathsep 分隔追加）
- 每个插件一个入口模块（manifest.entry，如 adapter.py），核心动态加载成"伪包"，
  入口可以 import 自己的兄弟模块（如 `from . import ranges`）而不污染 sys.path
- **加载即契约测试**：入口必须暴露可调用的 detect/extract/write_back，且 detect
  对空临时目录不抛、返回合法 DetectResult——不合格标 disabled，不阻断服务
- api_version 必须等于 PLUGIN_API_VERSION（协议纪律：破坏性变更升 api_version）
- 失败原因随 PluginInfo.error 暴露，plugins.list 可见；调用时报 ENGINE_NOT_SUPPORTED

插件适配器契约（本模块文档化，RPGMV 实现见 plugins/rpgmv/adapter.py）：
- `detect(dir) -> dict`：返回 DetectResult 形状（engine_id/display_name/confidence/version/details）
- `extract(source_path) -> list[dict]`：每条含 locator/source/context_json/warnings_json
  （核心负责算稳定 ID 与落库，插件不依赖核心模型）
- `write_back(source_path, output_dir, entries) -> dict`：entries 为完整 Entry dict 列表
  （含 context_json.char_ranges），返回 WriteBackResult 形状
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import tempfile
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gt_core.plugin_manifest import Manifest
from gt_core.rpc.errors import RpcError, RpcErrorCode
from gt_core.rpc.models import DetectResult, PluginInfo

PLUGIN_API_VERSION = "1.0"

# 插件适配器必须暴露的可调用（契约测试逐项检查）
_REQUIRED_CALLABLES = ("detect", "extract", "write_back")

# 可选能力（feature-detect，缺省降级）——M3 加性演进示范：
# protect/restore/has_protected = 占位符保护器（语法是引擎域，核心只按契约调用）
_OPTIONAL_CALLABLES = ("protect", "restore", "has_protected")

# 环境变量追加扫描目录（用户/CI 注入自定义插件仓用）
_ENV_PLUGIN_DIR = "GAMETR_PLUGIN_DIR"

_API_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


def _api_version_compatible(api_version: str) -> bool:
    """版本区间兼容：major 必须匹配，minor >= 核心支持的最低值。

    PLUGIN_API_VERSION="1.0" 表示核心最低支持 1.0；1.1/1.2 加性演进版本也加载
    （feature-detect 缺省降级）。插件商店方向（1.0 后）需 major 匹配 + minor 兼容，
    这里即为该语义的实现。
    """
    m = _API_VERSION_RE.match(api_version.strip())
    if not m:
        return False
    major, minor = map(int, m.groups())
    base_major, base_minor = map(int, PLUGIN_API_VERSION.split("."))
    return major == base_major and minor >= base_minor


def _plugin_features(entry: types.ModuleType | None) -> list[str]:
    """插件可选能力列表（feature-detect）：当前仅占位符保护器。"""
    if entry is None:
        return []
    feats = []
    if callable(getattr(entry, "protect", None)) and callable(getattr(entry, "restore", None)):
        feats.append("protect")
    return feats


_PKG_COUNTER = 0  # 伪包名去重（同名插件目录在不同搜索路径时避免模块名冲突）


@dataclass
class LoadedPlugin:
    """一个已扫描到的插件（加载成功或禁用都占位，便于列表可见）。"""

    manifest: Manifest | None
    entry: types.ModuleType | None
    error: str | None

    @property
    def loaded(self) -> bool:
        return self.error is None and self.entry is not None

    @property
    def engine_id(self) -> str:
        return self.manifest.engine_id if self.manifest else ""

    @property
    def display_name(self) -> str:
        return self.manifest.display_name if self.manifest else ""


class PluginManager:
    """扫描并持有全部插件；get(engine_id) 取可用插件，缺失/禁用报 ENGINE_NOT_SUPPORTED。"""

    def __init__(self, plugin_dirs: list[str] | None = None) -> None:
        dirs = list(plugin_dirs) if plugin_dirs is not None else _default_dirs()
        self._plugins: dict[str, LoadedPlugin] = {}
        for base in dirs:
            self._scan_dir(Path(base))

    # ---------- 查询 ----------

    def infos(self) -> list[PluginInfo]:
        """已加载插件信息（含 disabled），engine_id 排序，稳定输出。"""
        out = [_to_info(p) for p in self._plugins.values()]
        out.sort(key=lambda i: i.engine_id)
        return out

    def loaded_plugins(self) -> list[LoadedPlugin]:
        """可用插件（契约测试通过）的迭代对象，供 detect/extract 按序尝试。"""
        return [p for p in self._plugins.values() if p.loaded]

    def get(self, engine_id: str) -> LoadedPlugin:
        """取可用插件；不存在或禁用报 ENGINE_NOT_SUPPORTED（错误码契约）。"""
        p = self._plugins.get(engine_id)
        if p is None or not p.loaded:
            reason = f"无 {engine_id} 插件" if p is None else f"{engine_id} 插件已禁用: {p.error}"
            raise RpcError(RpcErrorCode.ENGINE_NOT_SUPPORTED, reason)
        return p

    def get_entry(self, engine_id: str) -> types.ModuleType:
        """便捷：取插件入口模块（get 的 loaded 保证非 None）。"""
        entry = self.get(engine_id).entry
        assert entry is not None  # loaded 契约保证
        return entry

    def get_protector(self, engine_id: str) -> tuple[Callable[..., Any], Callable[..., Any], Callable[..., Any] | None] | None:
        """取插件占位符保护器（M3 流水线 Protector 阶段委托，feature-detect）。

        返回 (protect, restore, has_protected?) 或 None（插件未提供，缺省身份保护器）。
        占位符语法是引擎域（RPGMV \\N[1] vs Ren'Py 不同），核心只按契约调用不解析。
        """
        entry = self.get(engine_id).entry
        assert entry is not None
        p = getattr(entry, "protect", None)
        r = getattr(entry, "restore", None)
        if not callable(p) or not callable(r):
            return None
        h = getattr(entry, "has_protected", None)
        return (p, r, h if callable(h) else None)

    # ---------- 加载 ----------

    def _scan_dir(self, base: Path) -> None:
        if not base.is_dir():
            return
        for mf in sorted(base.glob("*/plugin.json")):
            self._load_one(mf.parent)

    def _load_one(self, pdir: Path) -> None:
        """加载一个插件目录：manifest 解析失败也记录（engine_id 用目录名兜底）。"""
        try:
            manifest = Manifest.model_validate_json(
                (pdir / "plugin.json").read_text(encoding="utf-8")
            )
        except Exception as exc:  # noqa: BLE001 — 单个坏插件不阻断其他
            self._plugins[pdir.name] = LoadedPlugin(None, None, f"manifest 无效: {exc}")
            return
        error = self._check_contract(manifest, pdir)
        entry = None if error else _import_entry(pdir, manifest.entry)
        if entry is None and error is None:
            error = f"入口加载失败: {manifest.entry}"
        self._plugins[manifest.engine_id] = LoadedPlugin(manifest, entry, error)

    def _check_contract(self, m: Manifest, pdir: Path) -> str | None:
        """加载即契约测试：api_version + 可调用性 + detect 空目录不炸。失败返回原因串。"""
        if not _api_version_compatible(m.api_version):
            return f"api_version 不兼容: 需要 >= {PLUGIN_API_VERSION} 同 major，得到 {m.api_version}"
        try:
            entry = _import_entry(pdir, m.entry)
        except Exception as exc:  # noqa: BLE001
            return f"入口模块加载失败: {exc}"
        missing = [n for n in _REQUIRED_CALLABLES if not callable(getattr(entry, n, None))]
        if missing:
            return f"缺少可调用函数: {', '.join(missing)}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                raw = entry.detect(tmp)
                DetectResult.model_validate(raw)  # 空目录也必须返回合法 DetectResult
        except Exception as exc:  # noqa: BLE001
            return f"detect 契约测试失败（空目录应返回合法 DetectResult）: {exc}"
        return None


def _default_dirs() -> list[str]:
    """默认扫描路径：{cwd}/plugins + PyInstaller 打包目录 + exe 同目录 + 环境变量追加。

    PyInstaller onefile 把插件解压到 sys._MEIPASS/plugins（spec datas 打进包）；
    tauri dev 的 sidecar cwd 不是 repo 根，不能只靠 {cwd}/plugins（实测坑，
    见 AGENTS.md 环境注意）。多候选目录去重扫描，任何命中即加载。
    """
    dirs = [str(Path.cwd() / "plugins")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(str(Path(meipass) / "plugins"))
    dirs.append(str(Path(sys.executable).resolve().parent / "plugins"))
    env = os.environ.get(_ENV_PLUGIN_DIR, "")
    dirs.extend(d for d in env.split(os.pathsep) if d)
    return dirs


def _import_entry(pdir: Path, entry: str) -> types.ModuleType:
    """把插件目录加载成可导入伪包，再加载入口模块。

    伪包 __path__ = [插件目录]，入口里 `from . import ranges` 就能拿到兄弟模块，
    且不污染 sys.path（只往 sys.modules 加一个唯一包名）。
    """
    global _PKG_COUNTER
    _PKG_COUNTER += 1
    pkg_name = f"_gt_plugin_{pdir.name}_{_PKG_COUNTER}"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(pdir)]  # 包搜索路径指向插件目录
    sys.modules[pkg_name] = pkg
    return importlib.import_module(f"{pkg_name}.{Path(entry).stem}")


def _to_info(p: LoadedPlugin) -> PluginInfo:
    """LoadedPlugin -> PluginInfo（manifest 解析失败的兜底字段）。"""
    if p.manifest is not None:
        return PluginInfo(
            engine_id=p.manifest.engine_id,
            display_name=p.manifest.display_name,
            version=p.manifest.version,
            api_version=p.manifest.api_version,
            entry=p.manifest.entry,
            loaded=p.loaded,
            error=p.error,
            features=_plugin_features(p.entry),
        )
    return PluginInfo(
        engine_id=p.engine_id or "unknown",
        display_name="(manifest 损坏)",
        version="",
        api_version="",
        entry="",
        loaded=False,
        error=p.error,
    )
