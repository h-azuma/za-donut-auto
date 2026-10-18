"""狙うパワーの設定（config/target.json）と、当たりの判定。

target.json の形:
    {
      "targets": [
        {"powers": {"かがやきパワー：ドラゴン": 3}, "count": 2},
        {"powers": {"かがやきパワー：ゴースト": 3}}
      ],
      "always": [{"かがやきパワー：ぜんぶ": 3}]
    }
targets の 1 つ 1 つが「この組み合わせがすべてそろえば当たり」（powers はパワー名: 最低レベル）。
複数書けば、どれか 1 つがそろえば当たり。count を書くと、その組み合わせで当たりをその数だけバックアップしたら
狙いから外す（書かなければ上限なし）。{"パワー名": レベル} だけの書き方は上限なしと同じ。
always は狙いを Discord から置き換えても消えずに残る、いつも当たりにしたい組み合わせ（上限なし）。

厳選の途中でも、Discord のコマンド（register_commands）で変えられる。変えた内容は target.json に保存され、
次の試行から使われる。
"""

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .powers import CATALOG, CATEGORY, SOUR, SWEET, Power

log = logging.getLogger(__name__)

DEFAULT = [{"どうぐパワー：きのみ": 2, "どっさりパワー": 3}]

Combo = dict[str, int]


@dataclass
class Entry:
    powers: Combo
    count: int | None = None  # あと何個ほしいか（None は上限なし）

    @classmethod
    def parse(cls, raw: dict) -> "Entry":
        if "powers" in raw:
            entry = cls(dict(raw["powers"]), raw.get("count"))
        else:
            entry = cls(dict(raw))  # {"パワー名": レベル} だけの書き方
        validate(entry.powers)
        if entry.count is not None and entry.count < 1:
            raise ValueError(f"count は 1 以上にしてください: {describe_combo(entry.powers)}")
        return entry

    def dump(self) -> dict:
        return {"powers": self.powers} | ({"count": self.count} if self.count is not None else {})

    def describe(self) -> str:
        return describe_combo(self.powers) + (f"（あと {self.count} 個）" if self.count is not None else "")


class TargetStore:
    """狙いの設定。厳選のループと Discord のコマンド（別スレッド）の両方から使う。"""

    def __init__(self, path: Path = paths.TARGET):
        self.path = path
        self._lock = threading.Lock()
        config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"targets": DEFAULT}
        self._targets = [Entry.parse(raw) for raw in config.get("targets", [])]
        self._always = [Entry.parse(raw).powers for raw in config.get("always", [])]
        if not self._targets and not self._always:
            raise ValueError(f"{path} に狙いが 1 つもありません")

    def get(self) -> list[Combo]:
        """判定に使う組み合わせ（狙い ＋ いつも当たりにする組み合わせ）。"""
        with self._lock:
            return [dict(e.powers) for e in self._targets] + [dict(c) for c in self._always]

    def entries(self) -> list[Entry]:
        """狙い（Discord から置き換えられる部分）。"""
        with self._lock:
            return [Entry(dict(e.powers), e.count) for e in self._targets]

    def empty(self) -> bool:
        with self._lock:
            return not self._targets and not self._always

    def set(self, entries: list[Entry]) -> None:
        """狙いを置き換える（always はそのまま残す）。"""
        for e in entries:
            validate(e.powers)
        with self._lock:
            self._targets = [Entry(dict(e.powers), e.count) for e in entries]
            self._save()

    def consume(self, powers: tuple[Power, ...]) -> list[str]:
        """当たりをバックアップしたときに呼ぶ。そろった組み合わせ（上限があるもの）の残りを 1 つ減らし、
        0 になったものは狙いから外す。何が変わったかの説明を返す。"""
        messages = []
        with self._lock:
            for e in list(self._targets):
                if e.count is None or not satisfied(powers, e.powers):
                    continue
                e.count -= 1
                if e.count <= 0:
                    self._targets.remove(e)
                    messages.append(f"{describe_combo(e.powers)} がそろったので狙いから外しました")
                else:
                    messages.append(f"{describe_combo(e.powers)}: あと {e.count} 個")
                break  # 1 つのドーナツで数えるのは 1 つの組み合わせだけ
            if messages:
                self._save()
        return messages

    def _save(self) -> None:
        config = {"targets": [e.dump() for e in self._targets]}
        if self._always:
            config["always"] = self._always
        self.path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def describe(self) -> str:
        with self._lock:
            targets, always = list(self._targets), list(self._always)
        text = "\nまたは ".join(e.describe() for e in targets) if targets else "（なし）"
        if always:
            text += f"\nいつも当たり: {describe(always)}"
        return text


def validate(combo: Combo) -> None:
    if not combo:
        raise ValueError("狙うパワーが空です")
    for name, level in combo.items():
        if name not in CATALOG:
            raise ValueError(f"知らないパワー名です: {name}")
        if level not in (1, 2, 3):
            raise ValueError(f"レベルは 1〜3 で指定してください: {name} {level}")


def describe_combo(combo: Combo) -> str:
    """例:「どうぐパワー：きのみ Lv.2 以上 ＋ どっさりパワー Lv.3」"""
    return " ＋ ".join(f"{name} Lv.{lv}" + (" 以上" if lv < 3 else "") for name, lv in combo.items())


def describe(targets: list[Combo]) -> str:
    """組み合わせを「または」でつなぐ。"""
    return "\nまたは ".join(describe_combo(c) for c in targets)


def satisfied(powers: tuple[Power, ...], combo: Combo) -> bool:
    known = {p.name: p.level for p in powers if p.name is not None}
    return all((known.get(name) or 0) >= lv for name, lv in combo.items())


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
        if satisfied(powers, combo):
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
    """Discord で選べるパワー名（サワー系・スイート系を先に）。"""
    first = [name for group in (SOUR, SWEET) for names in group.values() for name in names]
    return first + [name for name in CATALOG if name not in first]


def register_commands(discord, store: TargetStore) -> None:
    """Discord に /target_show・/target_set・/target_add を登録する（押せるのは discord.json の本人だけ）。"""
    from discord import Interaction, app_commands

    tree = discord.tree

    async def autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=n, value=n) for n in choices() if current in n][:25]

    def allowed(interaction: Interaction) -> bool:
        return interaction.user.id == discord.user_id

    level = app_commands.Range[int, 1, 3]
    quota = app_commands.Range[int, 1, 99]

    def entry_of(count: int | None, *pairs: tuple[str | None, int | None]) -> Entry:
        return Entry({name: lv for name, lv in pairs if name and lv}, count)

    @tree.command(name="target_show", description="今の狙いを表示する")
    async def target_show(interaction: Interaction) -> None:
        await interaction.response.send_message(f"今の狙い:\n{store.describe()}")

    async def update(interaction: Interaction, entry: Entry, add: bool) -> None:
        if not allowed(interaction):
            await interaction.response.send_message("このコマンドは使えません。", ephemeral=True)
            return
        try:
            store.set(store.entries() + [entry] if add else [entry])
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        log.info(f"Discord から狙いを変更しました: {store.describe()}")
        await interaction.response.send_message(f"狙いを変更しました（次の試行から）:\n{store.describe()}")

    describe_args = dict(power1="パワー", level1="最低レベル", power2="パワー（任意）", level2="最低レベル",
                         power3="パワー（任意）", level3="最低レベル",
                         count="この組み合わせを何個そろえたら狙いから外すか（省略すると上限なし）")

    @tree.command(name="target_set", description="狙いをこの組み合わせに置き換える（すべてそろったら当たり）")
    @app_commands.describe(**describe_args)
    @app_commands.autocomplete(power1=autocomplete, power2=autocomplete, power3=autocomplete)
    async def target_set(interaction: Interaction, power1: str, level1: level,
                         power2: str | None = None, level2: level | None = None,
                         power3: str | None = None, level3: level | None = None,
                         count: quota | None = None) -> None:
        await update(interaction, entry_of(count, (power1, level1), (power2, level2), (power3, level3)), add=False)

    @tree.command(name="target_add", description="別の組み合わせを「または」で追加する")
    @app_commands.describe(**describe_args)
    @app_commands.autocomplete(power1=autocomplete, power2=autocomplete, power3=autocomplete)
    async def target_add(interaction: Interaction, power1: str, level1: level,
                         power2: str | None = None, level2: level | None = None,
                         power3: str | None = None, level3: level | None = None,
                         count: quota | None = None) -> None:
        await update(interaction, entry_of(count, (power1, level1), (power2, level2), (power3, level3)), add=True)
