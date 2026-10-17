"""狙うパワーの設定（target.json）と、当たりの判定。

target.json の形:
    {"targets": [{"どうぐパワー：きのみ": 2, "どっさりパワー": 3}]}
targets の 1 つ 1 つが「この組み合わせがすべてそろえば当たり」（パワー名: 最低レベル）。
複数書けば、どれか 1 つがそろえば当たり。

厳選の途中でも、Discord のコマンド（register_commands）で変えられる。変えた内容は target.json に保存され、
次の試行から使われる。
"""

import json
import logging
import threading
from pathlib import Path

from .powers import CATALOG, CATEGORY, SOUR, Power

log = logging.getLogger(__name__)

TARGET_PATH = Path(__file__).resolve().parent.parent / "target.json"
DEFAULT = [{"どうぐパワー：きのみ": 2, "どっさりパワー": 3}]

Combo = dict[str, int]


class TargetStore:
    """狙いの設定。厳選のループと Discord のコマンド（別スレッド）の両方から使う。"""

    def __init__(self, path: Path = TARGET_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._targets = self._load()

    def _load(self) -> list[Combo]:
        if not self.path.exists():
            return [dict(c) for c in DEFAULT]
        targets = json.loads(self.path.read_text(encoding="utf-8"))["targets"]
        for combo in targets:
            validate(combo)
        return targets

    def get(self) -> list[Combo]:
        with self._lock:
            return [dict(c) for c in self._targets]

    def set(self, targets: list[Combo]) -> None:
        for combo in targets:
            validate(combo)
        with self._lock:
            self._targets = [dict(c) for c in targets]
            self.path.write_text(json.dumps({"targets": self._targets}, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")

    def describe(self) -> str:
        return describe(self.get())


def validate(combo: Combo) -> None:
    if not combo:
        raise ValueError("狙うパワーが空です")
    for name, level in combo.items():
        if name not in CATALOG:
            raise ValueError(f"知らないパワー名です: {name}")
        if level not in (1, 2, 3):
            raise ValueError(f"レベルは 1〜3 で指定してください: {name} {level}")


def describe(targets: list[Combo]) -> str:
    """例:「どうぐパワー：きのみ Lv.2 以上 ＋ どっさりパワー Lv.3」（複数あれば「または」でつなぐ）"""
    def one(combo: Combo) -> str:
        return " ＋ ".join(f"{name} Lv.{lv}" + (" 以上" if lv < 3 else "") for name, lv in combo.items())
    return "\nまたは ".join(one(c) for c in targets)


def evaluate(powers: tuple[Power, ...], targets: list[Combo]) -> tuple[bool, bool]:
    """（当たりか, 当たりかもしれないか）を返す。

    「当たりかもしれない」は、読めないパワー（名前が未知・レベルが読めない）が狙いを満たしている可能性を
    否定できないとき。捨てずに人が確認できるようにする。
    """
    known = {p.name: p.level for p in powers if p.name is not None}
    unknown_rows = sum(1 for p in powers if p.name is None)
    # 同じカテゴリのパワーは 1 つまでなので、別のパワーで埋まっているカテゴリの狙いは満たせない
    taken = {CATEGORY[name] for name in known if name in CATEGORY}
    hit = False
    maybe = False
    for combo in targets:
        if all((known.get(name) or 0) >= lv for name, lv in combo.items()):
            hit = True
            continue
        spare = unknown_rows
        possible = True
        for name, lv in combo.items():
            if name in known:
                if known[name] is None or known[name] >= lv:
                    continue  # レベルが読めていないだけなら満たしている可能性がある
                possible = False
                break
            if CATEGORY.get(name) in taken:
                possible = False
                break
            if spare:
                spare -= 1   # 名前が読めない行がこのパワーかもしれない
                continue
            possible = False
            break
        maybe = maybe or possible
    return hit, (not hit and maybe)


def choices() -> list[str]:
    """Discord で選べるパワー名（サワー系を先に）。"""
    sour = [name for names in SOUR.values() for name in names]
    return sour + [name for name in CATALOG if name not in sour]


def register_commands(discord, store: TargetStore) -> None:
    """Discord に /target_show・/target_set・/target_add を登録する（押せるのは discord.json の本人だけ）。"""
    from discord import Interaction, app_commands

    tree = discord.tree

    async def autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=n, value=n) for n in choices() if current in n][:25]

    def allowed(interaction: Interaction) -> bool:
        return interaction.user.id == discord.user_id

    level = app_commands.Range[int, 1, 3]

    def combo_of(*pairs: tuple[str | None, int | None]) -> Combo:
        return {name: lv for name, lv in pairs if name and lv}

    @tree.command(name="target_show", description="今の狙いを表示する")
    async def target_show(interaction: Interaction) -> None:
        await interaction.response.send_message(f"今の狙い:\n{store.describe()}")

    async def update(interaction: Interaction, combo: Combo, add: bool) -> None:
        if not allowed(interaction):
            await interaction.response.send_message("このコマンドは使えません。", ephemeral=True)
            return
        try:
            store.set(store.get() + [combo] if add else [combo])
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        log.info(f"Discord から狙いを変更しました: {store.describe()}")
        await interaction.response.send_message(f"狙いを変更しました（次の試行から）:\n{store.describe()}")

    @tree.command(name="target_set", description="狙いをこの組み合わせに置き換える（すべてそろったら当たり）")
    @app_commands.describe(power1="パワー", level1="最低レベル", power2="パワー（任意）", level2="最低レベル",
                           power3="パワー（任意）", level3="最低レベル")
    @app_commands.autocomplete(power1=autocomplete, power2=autocomplete, power3=autocomplete)
    async def target_set(interaction: Interaction, power1: str, level1: level,
                         power2: str | None = None, level2: level | None = None,
                         power3: str | None = None, level3: level | None = None) -> None:
        await update(interaction, combo_of((power1, level1), (power2, level2), (power3, level3)), add=False)

    @tree.command(name="target_add", description="別の組み合わせを「または」で追加する")
    @app_commands.describe(power1="パワー", level1="最低レベル", power2="パワー（任意）", level2="最低レベル",
                           power3="パワー（任意）", level3="最低レベル")
    @app_commands.autocomplete(power1=autocomplete, power2=autocomplete, power3=autocomplete)
    async def target_add(interaction: Interaction, power1: str, level1: level,
                         power2: str | None = None, level2: level | None = None,
                         power3: str | None = None, level3: level | None = None) -> None:
        await update(interaction, combo_of((power1, level1), (power2, level2), (power3, level3)), add=True)
