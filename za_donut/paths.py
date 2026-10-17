"""設定ファイルと記録ファイルの置き場所。

config/  人が書く設定（recipe.json・target.json・discord.json）。discord.json だけ git に入れない
state/   プログラムが書く記録（きのみ一覧の並び、前回の残り個数）。消しても作り直される。git に入れない
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "state"

RECIPE = CONFIG_DIR / "recipe.json"        # 使うきのみ
TARGET = CONFIG_DIR / "target.json"        # 狙うパワー
DISCORD = CONFIG_DIR / "discord.json"      # Discord Bot のトークンなど（git に入れない）

BERRY_LIST = STATE_DIR / "berry_list.json"      # きのみ選択画面の一覧の並び
BERRY_COUNTS = STATE_DIR / "berry_counts.json"  # 直前の当たりのときのきのみの残り個数


def write_state(path: Path, text: str) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
