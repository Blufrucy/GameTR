"""程序化生成黄金样本 RPG Maker 工程（版权干净、可重建、真实格式）。

覆盖路线图 M2 2.2 支持的全部提取面：
- 数据库文件：Actors/Items/Weapons/Armors/Enemies/Skills/States/MapInfos（name/description/note 等）
- System：gameTitle + terms（basic/commands/params/messages）
- 事件指令：101+401（连续拼接）、102+402（选项）、405（滚动文字）、108/408（注释）

写盘格式 = serialize_rpgm（真实 MZ+MV 格式：JS 紧凑 + null 数组展开），
用作黄金样本快照测试的稳定输入。用法：python make_sample.py --out <dir>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from serializer import serialize_rpgm


def _write(root: Path, rel: str, data: object) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    # newline='' 防 Windows 把 \n 翻译成 \r\n（字节保真前提）
    p.write_text(serialize_rpgm(data), encoding="utf-8", newline="")


def _cmd(code: int, params: list[object]) -> dict:
    return {"code": code, "indent": 0, "parameters": params}


def _map_json() -> dict:
    """一张地图：对话（连续 401 拼接）、选项（102+402）、滚动文字（405）、注释（108/408）。"""
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
                        "conditions": {"actorId": 1, "actorValid": False, "itemId": 1,
                                       "itemValid": False, "selfSwitchCh": "A",
                                       "selfSwitchValid": False, "switch1Id": 1,
                                       "switch1Valid": False, "switch2Id": 1,
                                       "switch2Valid": False, "variableId": 1,
                                       "variableValid": False, "variableValue": 0},
                        "directionFix": False,
                        "image": {"characterIndex": 0, "characterName": "", "direction": 2,
                                  "pattern": 0, "tileId": 0},
                        "list": [
                            # 108 注释 + 408 续行（默认跳过）
                            _cmd(108, ["本地化提示：这是第一条对话"]),
                            _cmd(408, ["续行注释"]),
                            # 101 显示文字 + 连续 401（拼接为段落）
                            _cmd(101, ["", 0, 0, 2]),
                            _cmd(401, ["こんにちは、\\N[1]勇者！"]),
                            _cmd(401, ["次の行も同じメッセージ。\\C[2]赤色"]),
                            # 102 选项 + 402 分支
                            _cmd(102, [["はい", "いいえ"], 0, 0, 0]),
                            _cmd(402, ["はい", 0]),
                            _cmd(402, ["いいえ", 0]),
                            # 405 滚动文字
                            _cmd(405, ["スクロールテキスト\\G"]),
                            _cmd(0, []),
                        ],
                        "moveFrequency": 3,
                        "moveRoute": {"list": [{"code": 0, "parameters": [0, 0, 0, 0, 0]}],
                                      "repeat": True, "skippable": False, "wait": False},
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


def _actor(i: int, name: str) -> dict:
    return {
        "id": i, "battlerName": "", "characterIndex": 0, "characterName": "",
        "classId": 1, "equips": [0, 0, 0, 0, 0], "faceIndex": 0, "faceName": "",
        "traits": [], "initialLevel": 1, "maxLevel": 99,
        "name": name, "nickname": f"ニック{i}", "note": f"伝説の勇者{i}。",
        "profile": "村で生まれ育った少女。",
    }


def _item(i: int) -> dict:
    return {
        "id": i, "animationId": 0, "consumable": True,
        "damage": {"critical": False, "elementId": 0, "formula": "0", "type": 0, "variance": 20},
        "description": f"HP を {i * 100} 回復する。",
        "effects": [{"code": 11, "dataId": 0, "value1": 300, "value2": 0}],
        "hitType": 0, "iconIndex": 288, "itypeId": 1, "name": f"回復薬{i}",
        "note": "", "occasion": 0, "price": 50, "repeats": 1, "scope": 7,
        "speed": 0, "successRate": 100, "tpGain": 0,
    }


def _weapon(i: int) -> dict:
    return {
        "id": i, "animationId": 0, "atypeId": 1, "description": f"鋭い剣{i}。",
        "effects": [], "etypeId": 1, "hitType": 1, "iconIndex": 200,
        "name": f"剣{i}", "note": "", "occasion": 0, "price": 120, "params": [0, 10, 0, 0, 0, 0, 0, 0],
        "repeats": 1, "scope": 1, "speed": 0, "successRate": 100, "tpGain": 0, "wtypeId": 1,
    }


def _armor(i: int) -> dict:
    return {
        "id": i, "atypeId": 1, "description": f"軽い鎧{i}。", "effects": [],
        "etypeId": 2, "iconIndex": 210, "name": f"鎧{i}", "note": "",
        "occasion": 0, "price": 80, "params": [20, 0, 0, 0, 0, 0, 0, 0],
        "repeats": 1, "scope": 1, "speed": 0, "successRate": 100, "tpGain": 0,
    }


def _enemy(i: int) -> dict:
    return {
        "id": i, "actions": [{"conditionParam1": 0, "conditionParam2": 0, "conditionType": 0,
                              "rating": 5, "skillId": 1}], "battlerName": "", "dropItems": [],
        "exp": 100, "gold": 50, "name": f"スライム{i}", "note": f"弱い敵{i}。",
        "params": [200, 20, 10, 10, 10, 10, 10, 10], "traits": [],
    }


def _skill(i: int) -> dict:
    return {
        "id": i, "animationId": 0, "damage": {"critical": False, "elementId": 0, "formula": "0",
                                              "type": 0, "variance": 20},
        "description": f"火を放つ{i}。", "effects": [], "hitType": 0, "iconIndex": 130,
        "message1": f"{{name}}は炎を放った{i}！", "message2": "",
        "mpCost": 5, "name": f"ファイア{i}", "note": "", "occasion": 0, "repeats": 1,
        "requiredWtypeId1": 0, "requiredWtypeId2": 0, "scope": 1, "speed": 0,
        "stypeId": 1, "successRate": 100, "tpCost": 0, "tpGain": 0,
    }


def _state(i: int) -> dict:
    return {
        "id": i, "autoRemovalTiming": 0, "chanceByDamage": 100, "iconIndex": 1,
        "maxTurns": 0, "message1": f"{{name}}は毒に侵された{i}！", "message2": "",
        "minTurns": 0, "name": f"毒{i}", "note": "", "overlap": True, "priority": 50,
        "removeAtBattleEnd": False, "removeByDamage": False, "removeByRestriction": False,
        "removeByWalking": False, "restriction": 0, "stepsToRemove": 0, "traits": [],
    }


def _system_json() -> dict:
    return {
        "gameTitle": "Golden Sample",
        "currencyUnit": "G",
        "terms": {
            "basic": ["レベル", "HP", "MP", "攻撃力"],
            "commands": ["ニューゲーム", "コンティニュー", "セーブ"],
            "params": ["最大HP", "最大MP", "攻撃力"],
            "messages": {"battleStart": "戦闘開始！", "escapeSuccess": "逃げ切った。"},
        },
        "switches": [{"id": 1, "name": "フラグA"}],
        "variables": [{"id": 1, "name": "変数A"}],
    }


def generate(root: Path) -> Path:
    """生成黄金样本到 root（游戏根），返回 root。供 CLI 与测试共用。"""
    _write(root, "www/data/Map001.json", _map_json())
    _write(root, "www/data/Actors.json", [_actor(1, "勇者アリス"), _actor(2, "魔法使いボブ")])
    _write(root, "www/data/Items.json", [_item(1)])
    _write(root, "www/data/Weapons.json", [_weapon(1)])
    _write(root, "www/data/Armors.json", [_armor(1)])
    _write(root, "www/data/Enemies.json", [_enemy(1)])
    _write(root, "www/data/Skills.json", [_skill(1)])
    _write(root, "www/data/States.json", [_state(1)])
    _write(root, "www/data/MapInfos.json", [
        {"id": 1, "expanded": False, "name": "はじまりの村", "order": 1,
         "parentId": 0, "scrollX": 0, "scrollY": 0},
        {"id": 2, "expanded": False, "name": "ダンジョン", "note": "地下の迷宮。",
         "order": 2, "parentId": 0, "scrollX": 0, "scrollY": 0},
    ])
    _write(root, "www/data/System.json", _system_json())
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="生成黄金样本 RPG Maker 工程")
    parser.add_argument("--out", required=True, help="输出目录（游戏根，如 ./sample）")
    args = parser.parse_args()
    root = generate(Path(args.out))
    print(f"黄金样本已生成: {root}")


if __name__ == "__main__":
    main()
