"""きのみの一覧と、レシピ（どのきのみを何個使うか）の設定、きのみ選択画面での読み取り。

きのみ選択画面の一覧は、持っているきのみだけが並ぶ（0 個のものは出ない）ので、並び順は決め打ちできない。
一度端から端まで読んで並びを覚え（berry_list.json）、ずれていたら読み直す。
カーソルが乗っている行の名前は OCR で読み、BERRIES の名前に当てはめる。

recipe.json の形:
    {"berries": {"いじげんロゼル": 6, "いじげんカシブ": 2}}
書いた順に選ぶ。厳選の途中でも Discord のコマンド（register_commands）で変えられる。
"""

import difflib
import json
import logging
import threading
from pathlib import Path

import cv2
import numpy as np

from .powers import _base, _ocr

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RECIPE_PATH = ROOT / "recipe.json"
LIST_PATH = ROOT / "berry_list.json"  # 覚えた一覧の並び（持っている数で変わるので git には入れない）
DEFAULT_RECIPE = {"いじげんロゼル": 6, "いじげんカシブ": 2}
MAX_BERRIES = 8

BASE = ["クラボ", "カゴ", "モモン", "チーゴ", "ナナシ", "オレン", "キー", "ラム", "オボン", "ザロク", "ネコブ", "タポル",
        "ロメ", "ウブ", "マトマ", "オッカ", "イトケ", "ソクノ", "リンド", "ヤチェ", "ヨプ", "ビアー", "シュカ", "バコウ",
        "ウタン", "タンガ", "ヨロギ", "カシブ", "ハバン", "ナモ", "リリバ", "ホズ", "ロゼル"]
DIM = "いじげん"
BERRIES = BASE + [DIM + b for b in BASE]  # 名前は「のみ」を除いたもの

# 一覧の行（1920x1080）。8 行見えていて、カーソルが乗っている行だけ明るい
ROWS_Y = [327 + 93 * k for k in range(8)]  # 行の中心
NAME_X = (120, 480)
CURSOR_MIN_V = 150

# OCR がよく取り違える文字
OCR_FIX = str.maketrans({"ョ": "ヨ", "ャ": "ヤ", "9": "タ", "八": "ハ", "こ": "ビ", "に": "ビ", "一": "ー"})


def cursor_row(frame: np.ndarray) -> int | None:
    """カーソルが乗っている行の中心 y。見つからなければ None（スクロール中など）。"""
    v = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[..., 2]
    means = [v[y - 30:y + 30, 150:450].mean() for y in ROWS_Y]
    k = int(np.argmax(means))
    return ROWS_Y[k] if means[k] >= CURSOR_MIN_V else None


def ocr_name(frame: np.ndarray, row_y: int) -> str:
    """行のきのみの名前を OCR で読む（暗い文字だけを取り出して読む）。"""
    img = frame[row_y - 45:row_y - 3, NAME_X[0]:NAME_X[1]]
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    b = np.where(g < 110, 0, 255).astype(np.uint8)
    b = cv2.copyMakeBorder(b, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    big = cv2.resize(b, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return _ocr(cv2.cvtColor(big, cv2.COLOR_GRAY2BGR)).replace(" ", "")


def scores(text: str) -> dict[str, float]:
    """OCR の結果が、それぞれのきのみの名前にどれだけ似ているか。"""
    t = text.translate(OCR_FIX)
    t = t.removesuffix("のみ")
    dim = _base(t[:4]) in (_base(DIM), "いしけん") or t[:3] in ("いしけ", "いじけ")
    body = t[4:] if dim else t
    prefix = DIM if dim else ""
    return {prefix + b: difflib.SequenceMatcher(None, _base(body), _base(b)).ratio() for b in BASE}


def identify(text: str) -> str | None:
    """OCR の結果を、一番似ているきのみの名前にする（似ているものが無ければ None）。"""
    s = scores(text)
    best = max(s, key=s.get)
    return best if s[best] >= 0.5 else None


def looks_like(text: str, name: str) -> bool:
    """OCR の結果が name と読めるか（name より似ている名前が無ければよい）。"""
    s = scores(text)
    return name in s and s[name] >= 0.5 and s[name] >= max(s.values())


def load_list() -> list[str] | None:
    if LIST_PATH.exists():
        return json.loads(LIST_PATH.read_text(encoding="utf-8"))
    return None


def save_list(names: list[str]) -> None:
    LIST_PATH.write_text(json.dumps(names, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ---- レシピの設定 ----

Recipe = dict[str, int]


def validate(recipe: Recipe) -> None:
    if not recipe:
        raise ValueError("きのみが指定されていません")
    for name, n in recipe.items():
        if name not in BERRIES:
            raise ValueError(f"知らないきのみです: {name}")
        if n < 1:
            raise ValueError(f"個数は 1 以上にしてください: {name} {n}")
    if sum(recipe.values()) > MAX_BERRIES:
        raise ValueError(f"きのみは合わせて {MAX_BERRIES} 個までです")


def describe(recipe: Recipe) -> str:
    return " ＋ ".join(f"{name} ×{n}" for name, n in recipe.items())


class RecipeStore:
    """レシピの設定。厳選のループと Discord のコマンド（別スレッド）の両方から使う。"""

    def __init__(self, path: Path = RECIPE_PATH):
        self.path = path
        self._lock = threading.Lock()
        if path.exists():
            recipe = json.loads(path.read_text(encoding="utf-8"))["berries"]
            validate(recipe)
        else:
            recipe = dict(DEFAULT_RECIPE)
        self._recipe = recipe

    def get(self) -> Recipe:
        with self._lock:
            return dict(self._recipe)

    def set(self, recipe: Recipe) -> None:
        validate(recipe)
        with self._lock:
            self._recipe = dict(recipe)
            self.path.write_text(json.dumps({"berries": self._recipe}, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")

    def describe(self) -> str:
        return describe(self.get())


def register_commands(discord, store: RecipeStore) -> None:
    """Discord に /recipe_show・/recipe_set を登録する（使えるのは discord.json の本人だけ）。"""
    from discord import Interaction, app_commands

    tree = discord.tree
    choices = [DIM + b for b in BASE] + BASE  # いじげんを先に

    async def autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=n, value=n) for n in choices if current in n][:25]

    count = app_commands.Range[int, 1, MAX_BERRIES]

    @tree.command(name="recipe_show", description="今のレシピ（使うきのみ）を表示する")
    async def recipe_show(interaction: Interaction) -> None:
        await interaction.response.send_message(f"今のレシピ: {store.describe()}")

    @tree.command(name="recipe_set", description="レシピ（使うきのみと個数、合わせて 8 個まで）を変える")
    @app_commands.describe(berry1="きのみ", count1="個数", berry2="きのみ（任意）", count2="個数",
                           berry3="きのみ（任意）", count3="個数", berry4="きのみ（任意）", count4="個数")
    @app_commands.autocomplete(berry1=autocomplete, berry2=autocomplete, berry3=autocomplete, berry4=autocomplete)
    async def recipe_set(interaction: Interaction, berry1: str, count1: count,
                         berry2: str | None = None, count2: count | None = None,
                         berry3: str | None = None, count3: count | None = None,
                         berry4: str | None = None, count4: count | None = None) -> None:
        if interaction.user.id != discord.user_id:
            await interaction.response.send_message("このコマンドは使えません。", ephemeral=True)
            return
        recipe = {name: n for name, n in ((berry1, count1), (berry2, count2), (berry3, count3), (berry4, count4))
                  if name and n}
        try:
            store.set(recipe)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        log.info(f"Discord からレシピを変更しました: {store.describe()}")
        await interaction.response.send_message(f"レシピを変更しました（次の試行から）: {store.describe()}")
