from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.plugins.sts_card_guess.core import CardRecord, CardRepository, create_game_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地测试杀戮尖塔猜卡逻辑，不依赖 NapCatQQ。")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="卡牌 JSON 根目录，默认使用插件中的默认目录。",
    )
    parser.add_argument(
        "--card",
        type=str,
        default=None,
        help="指定卡牌中文名或 card id 进入测试，例如 暴走 或 rampage。",
    )
    return parser.parse_args()


def select_card(repository: CardRepository, keyword: str | None) -> CardRecord:
    if not keyword:
        return repository.pick_random_card()
    card = repository.find_card(keyword)
    if card is None:
        raise SystemExit(f"没有找到卡牌：{keyword}")
    return card


def main() -> None:
    args = parse_args()
    repository = CardRepository(args.data_dir)
    card = select_card(repository, args.card)
    state = create_game_state(card)

    print("本地猜卡测试已开始。")
    print(state.build_start_message())
    print("可用命令：/提示  /答案  /退出")

    while True:
        user_input = input("请输入猜测或命令：").strip()
        if not user_input:
            continue

        if user_input == "/提示":
            result = state.advance()
            print(result.message)
            if result.finished:
                return
            continue

        if user_input == "/答案":
            print(f"当前卡牌答案：{state.card.name} ({state.card.card_id})")
            print("本地测试已按失败处理并结束。")
            return

        if user_input == "/退出":
            print("已退出本地猜卡测试。")
            return

        if state.check_guess(user_input):
            print(f"猜对了，答案就是 `{state.card.name}`。")
            return

        print("猜错了，游戏继续。")


if __name__ == "__main__":
    main()
