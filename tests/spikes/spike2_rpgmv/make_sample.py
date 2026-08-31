#!/usr/bin/env python
"""程序化生成最小 RPG Maker 样本工程（版权干净、可重建）。

生成结构（对齐真实 MZ 工程 oriontest / rmmz 1.9.0）：
  www/data/Map001.json   一张地图 + 对话事件（101/401）、选项（102/402）、滚动文字（405）
  www/data/Actors.json    角色（name/note）
  www/data/Items.json     物品（name/description/note）
  www/data/System.json    部分字段（terms 含 commandNames 等）

写盘格式 = serialize_rpgm（roundtrip.py，2026-08-28 真实 MZ 工程验证的唯一正确实现）：
JS 紧凑序列化 + "数组含 null 元素则逐元素展开"换行装饰。见 ADR-0004。

用法：python make_sample.py --out <输出目录>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from roundtrip import serialize_rpgm


def _write(root: Path, rel: str, data: object) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    # newline='' 防 Windows 把 \n 翻译成 \r\n（字节保真前提）
    p.write_text(serialize_rpgm(data), encoding="utf-8", newline="")


def _map_json() -> dict:
    """最小地图：1 张，含一条对话事件（含连续401拼接、选项、滚动文字）。

    结构对齐真实 MZ：events 数组索引 0 为 null（事件从 1 起），data 一维数组（width*height）。
    """
    return {
        "autoplayBgm": False,
        "autoplayBgs": False,
        "battleback1Name": "",
        "battleback2Name": "",
        "bgm": {"name": "", "pan": 100, "pitch": 100, "volume": 90},
        "bgs": {"name": "", "pan": 100, "pitch": 100, "volume": 90},
        "disableDashing": False,
        "displayName": "",
        "encounterList": [],
        "encounterStep": 2,
        "events": [
            None,  # 索引 0 占位（真实 MZ 约定）
            {
                "id": 1,
                "name": "Event1",
                "note": "",
                "pages": [
                    {
                        "conditions": {
                            "actorId": 1,
                            "actorValid": False,
                            "itemId": 1,
                            "itemValid": False,
                            "selfSwitchCh": "A",
                            "selfSwitchValid": False,
                            "switch1Id": 1,
                            "switch1Valid": False,
                            "switch2Id": 1,
                            "switch2Valid": False,
                            "variableId": 1,
                            "variableValid": False,
                            "variableValue": 0,
                        },
                        "directionFix": False,
                        "image": {"characterIndex": 0, "characterName": "", "direction": 2, "pattern": 0, "tileId": 0},
                        "list": [
                            {"code": 101, "indent": 0, "parameters": ["", 0, 0, 0]},
                            {"code": 401, "indent": 0, "parameters": ["こんにちは、\\N[1]勇者！"]},
                            # 连续 401 需拼接为段落（路线图 M2 2.2）
                            {"code": 401, "indent": 0, "parameters": ["次の行も同じメッセージ。\\C[2]赤色"]},
                            {"code": 102, "indent": 0, "parameters": [["はい", "いいえ"], 0, 0, 0]},
                            {"code": 402, "indent": 0, "parameters": ["はい", 0]},
                            {"code": 405, "indent": 0, "parameters": ["スクロールテキスト\\G"]},
                            {"code": 0, "indent": 0, "parameters": []},
                        ],
                        "moveFrequency": 3,
                        "moveRoute": {
                            "list": [{"code": 0, "parameters": [0, 0, 0, 0, 0]}],
                            "repeat": True,
                            "skippable": False,
                            "wait": False,
                        },
                        "moveSpeed": 3,
                        "moveType": 0,
                        "priorityType": 0,
                        "stepAnime": False,
                        "through": False,
                        "trigger": 0,
                        "walkAnime": True,
                    }
                ],
                "x": 5,
                "y": 5,
            }
        ],
        "height": 15,
        "meta": {},
        "note": "",
        "parallaxLoopX": False,
        "parallaxLoopY": False,
        "parallaxName": "",
        "parallaxShow": False,
        "parallaxSx": 0,
        "parallaxSy": 0,
        "scrollType": 0,
        "specifiedBattleback": False,
        "tilesetId": 1,
        "width": 20,
        "data": [0] * (20 * 15),  # 一维 tile 数组（真实 MZ 是 width*height 一维）
    }


def _actors_json() -> list[dict]:
    return [
        {
            "id": 1,
            "battlerName": "",
            "characterIndex": 0,
            "characterName": "",
            "classId": 1,
            "equips": [0, 0, 0, 0, 0],
            "faceIndex": 0,
            "faceName": "",
            "traits": [],
            "initialLevel": 1,
            "maxLevel": 99,
            "name": "勇者アリス",
            "nickname": "アリス",
            "note": "伝説の勇者。",
            "profile": "村で生まれ育った少女。",
        },
        {
            "id": 2,
            "battlerName": "",
            "characterIndex": 0,
            "characterName": "",
            "classId": 2,
            "equips": [0, 0, 0, 0, 0],
            "faceIndex": 0,
            "faceName": "",
            "traits": [],
            "initialLevel": 1,
            "maxLevel": 99,
            "name": "魔法使いボブ",
            "nickname": "ボブ",
            "note": "",
            "profile": "賢者に弟子入りした少年。",
        },
    ]


def _items_json() -> list[dict]:
    return [
        {
            "id": 1,
            "animationId": 0,
            "consumable": True,
            "damage": {"critical": False, "elementId": 0, "formula": "0", "type": 0, "variance": 20},
            "description": "HP を 300 回復する。",
            "effects": [{"code": 11, "dataId": 0, "value1": 300, "value2": 0}],
            "hitType": 0,
            "iconIndex": 288,
            "itypeId": 1,
            "name": "回復薬",
            "note": "",
            "occasion": 0,
            "price": 50,
            "repeats": 1,
            "scope": 7,
            "speed": 0,
            "successRate": 100,
            "tpGain": 0,
        }
    ]


def _system_json() -> dict:
    return {
        "gameTitle": "Spike 2 Sample",
        "terms": {
            "basic": ["レベル", "HP", "MP", "攻撃力"],
            "command": ["ニューゲーム", "コンティニュー"],
            "messages": {
                "battleStart": "戦闘開始！",
                "escapeSuccess": "逃げ切った。",
            },
        },
        "variables": [{"id": 1, "name": "フラグA"}],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成最小 RPG Maker MV 样本工程")
    parser.add_argument("--out", required=True, help="输出目录（游戏根目录，如 ./sample_game）")
    args = parser.parse_args()

    root = Path(args.out)
    _write(root, "www/data/Map001.json", _map_json())
    _write(root, "www/data/Actors.json", _actors_json())
    _write(root, "www/data/Items.json", _items_json())
    _write(root, "www/data/System.json", _system_json())
    print(f"样本工程已生成: {root}")


if __name__ == "__main__":
    main()
