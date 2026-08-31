"""生成黄金样本 → extract → 写 expected.json（快照）+ 打印摘要。"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_sample import generate  # noqa: E402

from gt_core.plugin import PluginManager  # noqa: E402

ROOT = Path(__file__).parent
GAME = ROOT / "sample"  # 生成到样本目录（gitignore）
EXPECTED = ROOT / "expected.json"


def main() -> None:
    generate(GAME)

    mgr = PluginManager(["plugins"])
    adapter = mgr.get_entry("rpgmv")
    entries = adapter.extract(str(GAME))

    by_file = Counter(json.loads(e["context_json"])["file_path"].split("/")[-1] for e in entries)
    print(f"total: {len(entries)}")
    for f, n in sorted(by_file.items()):
        print(f"  {f}: {n}")

    # 关键类型抽查
    for e in entries:
        src = e["source"]
        if src.startswith("こんにちは"):
            ctx = json.loads(e["context_json"])
            print(f"  MESSAGE-join: {src!r}")
            print(f"    ranges={ctx['char_ranges']} segments={ctx['segments']!r}")
        if src == "はい":
            print(f"  CHOICE: {src!r} @ {e['locator']}")
        if "スクロール" in src:
            print(f"  SCROLL-405: {src!r} @ {e['locator']}")
        if e["locator"].endswith("Actors.json::$[0].nickname"):
            print(f"  ACTOR-nickname: {src!r}")
        if "battleStart" in e["locator"]:
            print(f"  SYSTEM-message: {src!r}")
    comments = [e for e in entries if "注释" in e["source"] or "续行" in e["source"]]
    print(f"  注释（应 0）: {len(comments)}")

    EXPECTED.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(f"expected.json 已写: {EXPECTED} ({len(entries)} 条)")


if __name__ == "__main__":
    main()
