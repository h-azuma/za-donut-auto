"""Discord の設定を確かめる。テスト用のボタン付きメッセージを送り、押されたボタンを表示する。

使い方:
    python tools/discord_test.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from za_donut.notify import CONFIG_PATH, Discord  # noqa: E402


def main() -> None:
    discord = Discord.from_config()
    if discord is None:
        sys.exit(f"{CONFIG_PATH} がありません。docs/discord-setup.md を見て作成してください。")
    with discord:
        print("Discord に接続しました。テストメッセージのボタンを押してください。")
        choice = discord.ask("接続テストです。どちらかのボタンを押してください。",
                             [("ok", "OK", "primary"), ("ng", "NG", "secondary")])
        print(f"押されたボタン: {choice}")
        discord.send(f"テスト完了（{choice}）。")


if __name__ == "__main__":
    main()
