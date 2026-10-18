"""ドーナツ厳選の自動ループ（making-donut.md の手順）。

各段階で「想定した画面になったか」をテンプレートで確認しながら進む。
想定外の画面で止まったときは、ソフトを終了して最初からやり直す（終了してもバックアップは失われない）。
当たりが出たら結果画面のまま停止する。ポケモンセンターでのバックアップは手動で行う。
"""

import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from . import berries, powers, target, vision
from .pico import Pico
from .screen import Screen
from .steps import MAP_TO_HOTEL

MAP_TO_POKECENTER = (1.0, -0.1, 0.22)  # マップでホテルZ → ポケモンセンター：ベール（行きの逆）

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT / "screenshots" / "results"
ERROR_DIR = ROOT / "screenshots" / "errors"
BACKUP_DIR = ROOT / "screenshots" / "backups"
TEMP_DIR = Path(tempfile.gettempdir()) / "za-donut"  # デバッグモード以外で、Discord に送るまで画面を置く場所

WALK_TO_COUNTER_UP = (0.0, 1.0, 2.3)     # ロビー入口 → カウンターの右手前 (x, y, 秒)
WALK_TO_COUNTER_LAST = (-1.0, 0.0)       # → 「アンシャ」が見えるまでこの向きに歩く
BERRY_HOLD = 0.04   # きのみ選択の 1 入力（0.03/0.04 でも取りこぼしなし。余裕を持たせた値）
BERRY_GAP = 0.06
BERRY_SETTLE = 0.3  # カーソル移動・選択の後、画面が落ち着くまで
BERRY_ROW_SPAN = (55, 38)  # 通知用に切り出すきのみの行（行の中心から上・下に何ピクセル）
BERRY_ROW_X = (20, 680)
COOK_SKIP_INTERVAL = 0.5  # 調理の演出を飛ばす A の間隔
CUTSCENE_TIMEOUT = 75  # 昼夜の切り替わりの演出は 1 分弱。パーティ欄が戻った時点で待つのをやめる（これは上限）
PRESENT_A_INTERVAL = 0.3  # 完成場面の A の間隔。完成場面は表示後 1〜1.5 秒は A を受け付けない。結果画面が出た直後の A では閉じない


@dataclass(frozen=True)
class DonutResult:
    powers: tuple[powers.Power, ...]
    hit: bool
    uncertain: bool  # 読めないパワーがあり、当たりの可能性を否定できない

    def summary(self) -> str:
        return " / ".join(str(p) for p in self.powers) or "（パワーなし）"


class StepFailed(Exception):
    """想定した画面にならなかった。"""


class OutOfBerries(Exception):
    """レシピのきのみが足りない（やり直しても同じなので、厳選を止める）。"""


class Runner:
    def __init__(self, pico: Pico, screen: Screen, targets: target.TargetStore, recipes: berries.RecipeStore,
                 keep_screenshots: bool = True):
        self.p = pico
        self.s = screen
        self.targets = targets  # 狙うパワー（Discord から途中で変えられる）
        self.recipes = recipes  # 使うきのみ（同上）
        self.berry_list = berries.load_list()  # きのみ一覧の並び（ずれていたら読み直す）
        self.cursor_index: int | None = None   # きのみ一覧でカーソルがある位置（berry_list の何番目か）
        # False のときは画面を一時フォルダに保存する（Discord に送った後で discard() で消す）
        self.keep_screenshots = keep_screenshots
        self.last_result_path: Path | None = None  # 直近の当たり / 要確認の結果画面
        self.last_recipe: berries.Recipe = {}      # 直近の試行で使ったレシピ
        # 直近の試行で、きのみを選んだ直後の残り個数（読めなければ None）と、そのときの画面・行（通知の画像用）
        self.last_counts: dict[str, tuple[int | None, object, int]] = {}

    # ---- 画面待ち ----

    def wait_for(self, name: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if vision.visible(self.s.grab(), name):
                return
            time.sleep(0.1)
        raise StepFailed(f"{name} が {timeout:.0f} 秒以内に表示されませんでした")

    def press_until(self, button: str, name: str, tries: int, timeout: float) -> None:
        """name が表示されるまで button を押す（押すたびに timeout 秒待つ）。"""
        for _ in range(tries):
            self.p.press(button, wait=0)
            try:
                self.wait_for(name, timeout)
                return
            except StepFailed:
                pass
        raise StepFailed(f"{button} を {tries} 回押しても {name} が表示されませんでした")

    def save(self, directory: Path, name: str, frame=None) -> Path:
        if not self.keep_screenshots:
            directory = TEMP_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{datetime.now():%Y%m%d_%H%M%S}_{name}.png"
        cv2.imwrite(str(path), self.s.grab() if frame is None else frame)
        return path

    def discard(self, *paths: Path | None) -> None:
        """一時フォルダに保存した画面を消す（screenshots/ に残す設定のときは何もしない）。"""
        if self.keep_screenshots:
            return
        for path in paths:
            if path:
                path.unlink(missing_ok=True)

    # ---- 各段階 ----

    def close_software(self) -> None:
        """HOME に戻り、ZA が起動中なら終了する。"""
        self.p.press("home", wait=1.5)
        frame = self.s.grab()
        if not vision.visible(frame, "home_gear"):
            # HOME 以外（ゲーム中で HOME が効かなかった等）。もう一度 HOME を押す
            self.p.press("home", wait=1.5)
            self.wait_for("home_gear", 5)
        if vision.visible(self.s.grab(), "home_playing"):
            self.p.press("x", wait=0)
            self.wait_for("close_dialog", 5)
            self.p.press("a", wait=0)
            deadline = time.monotonic() + 15
            while vision.visible(self.s.grab(), "home_playing") or not vision.visible(self.s.grab(), "home_gear"):
                if time.monotonic() > deadline:
                    raise StepFailed("ソフトの終了を確認できませんでした")
                time.sleep(0.3)
        time.sleep(1.0)

    def launch_to_title(self) -> None:
        self.p.press("b", wait=0.5)  # コントローラーを起こす（HOME で B は何もしない）
        self.p.press("a", wait=0)
        self.wait_for("user_select", 5)
        self.p.press("a", wait=0)
        self.wait_for("title", 40)
        time.sleep(1.0)

    def load_backup(self) -> None:
        for _ in range(3):
            self.p.press("x", "b", "dpad_up", hold=0.5, wait=0)
            try:
                self.wait_for("backup_menu", 4)
                break
            except StepFailed:
                if not vision.visible(self.s.grab(), "title"):
                    raise
        else:
            raise StepFailed("BACKUP 画面を開けませんでした")
        # ここを確認できた場合だけ先へ進む（オートセーブで遊ばない）。メニュー表示直後の A は取りこぼすことがある
        self.press_until("a", "backup_loaded", tries=3, timeout=2.5)
        self.p.press("a", wait=1.0)
        self.p.press("a", wait=0)
        self.wait_for("backup_warning", 5)
        self.p.press("a", wait=0)
        self.wait_for("field_party", 45)
        time.sleep(1.0)

    def travel_to_hotel(self) -> None:
        for _ in range(3):
            self.p.press("plus", wait=0)
            self.wait_for("map", 5)
            time.sleep(0.5)
            self.p.tilt(*MAP_TO_HOTEL, wait=0.8)
            if vision.visible(self.s.grab(), "map_hotel"):
                break
            self.p.press("b", wait=1.5)  # 閉じて開き直すとカーソルが現在地に戻る
        else:
            raise StepFailed("マップでホテルZ を選べませんでした")
        self.p.press("a", wait=0)
        self.wait_for("travel_confirm", 3)
        self.p.press("a", wait=1.0)
        self.wait_for("field_party", 15)  # 読み込みが終わるとパーティ欄が出る（実測 3.7 秒）
        time.sleep(0.3)

    def walk_until(self, x: float, y: float, name: str, timeout: float) -> bool:
        """スティックを倒したまま歩き、name が見えたらすぐ止まる。見えないまま timeout 秒たったら False。"""
        self.p.stick(x, y)
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if vision.visible(self.s.grab(), name):
                    return True
            return False
        finally:
            self.p.stick(0, 0)

    def walk_to_counter(self) -> None:
        if not self.walk_until(0.0, 1.0, "door_prompt", 4.0):
            raise StepFailed("ホテルZ のドアの前に着けませんでした")
        self.p.press("a", wait=0)
        # 暗転でパーティ欄が消え、ロビーで再び出たら操作できる（実測 3.1 秒）
        deadline = time.monotonic() + 3
        while vision.visible(self.s.grab(), "field_party"):
            if time.monotonic() > deadline:
                raise StepFailed("ホテルZ に入れませんでした")
        self.wait_for("field_party", 10)
        time.sleep(0.3)

        self.p.tilt(*WALK_TO_COUNTER_UP, wait=0)
        if self.walk_until(*WALK_TO_COUNTER_LAST, "talk_anshya", 1.5):
            return
        nudges = [(-1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, 1.0), (1.0, 0.0), (0.0, -1.0)]
        for x, y in nudges:
            self.p.tilt(x, y, 0.15, wait=0.5)
            if vision.visible(self.s.grab(), "talk_anshya"):
                return
        raise StepFailed("アンシャに話しかけられる位置に着けませんでした")

    def make_donut(self) -> None:
        self.p.press("a", wait=1.0)  # 話しかける
        self.p.press("a", wait=0)    # 「ようこそ！」
        self.wait_for("menu_make", 5)
        self.p.press("a", wait=0)    # 「ドーナツをつくる」
        self.wait_for("berry_order", 5)
        time.sleep(0.5)

        self.select_berries()

        self.p.press("plus", wait=0)
        self.wait_for("cook_dialog", 5)
        # 調理の演出は A で飛ばせるので、完成したドーナツを見せる場面まで A を送り続ける。
        # 結果画面で A を押すと閉じてしまうので、結果画面へ進める A は完成場面で 1 回だけ押す
        deadline = time.monotonic() + 25
        last_a = 0.0
        while True:
            frame = self.s.grab()
            if vision.visible(frame, "result_pluslv"):
                break
            if vision.visible(frame, "cook_present"):
                self.press_until("a", "result_pluslv", tries=10, timeout=PRESENT_A_INTERVAL)
                break
            if time.monotonic() > deadline:
                raise StepFailed("完成したドーナツの場面になりませんでした")
            if time.monotonic() - last_a >= COOK_SKIP_INTERVAL:
                self.p.press("a", wait=0)
                last_a = time.monotonic()
        # パワー欄はピンクの帯で隠れた状態から順に表示される。「A つぎへ」が出たら表示完了
        self.wait_for("result_next", 15)
        time.sleep(0.5)

    def read_result(self) -> DonutResult:
        frames = [self.s.grab() for _ in range(3)]
        readings = [powers.read_powers(f) for f in frames]
        if len(set(readings)) != 1:
            raise StepFailed(f"パワーの読み取り結果がフレームごとに違います: {readings}")
        for p in readings[0]:
            if p.name is None:
                log.info(f"知らないパワーです（OCR: {p.raw}）")
        hit, uncertain = target.evaluate(readings[0], self.targets.get())
        r = DonutResult(readings[0], hit, uncertain)
        self.last_result_path = None
        if r.hit or r.uncertain:  # 外れの画面は残さない
            self.last_result_path = self.save(RESULT_DIR, "HIT" if r.hit else "CHECK", frames[0])
        return r

    # ---- きのみ選択 ----

    def _tap(self, button: str, times: int) -> None:
        for _ in range(times):
            self.p.press(button, hold=BERRY_HOLD, wait=BERRY_GAP)

    def _cursor(self) -> tuple[object, int | None]:
        """（画面, カーソルが乗っている行の中心 y）。スクロール中で分からなければ y は None。"""
        time.sleep(BERRY_SETTLE)
        frame = self.s.grab()
        return frame, berries.cursor_row(frame)

    def select_berries(self) -> None:
        """レシピのきのみを選ぶ。選ぶ前と後で残り個数を読み、使う数だけ減ったかを確かめる。

        きのみ選択画面を開いた直後のカーソルは一覧の一番上にあり、選んだ後はそのきのみの上に残るので、
        カーソルの位置は読まずに覚えておき、覚えた並びの何番目かで近いほうへ動かす。
        """
        recipe = self.recipes.get()
        self.last_recipe = recipe
        self.last_counts = {}
        self.cursor_index = 0
        for name in self._selection_order(recipe):
            n = recipe[name]
            frame, y = self.move_cursor_to(name)
            before = vision.read_count(frame, y)
            if before is not None and before < n:
                raise OutOfBerries(f"{name} が足りません（{before} 個、使うのは {n} 個）")
            self._tap("a", n)
            frame, y2, after = self._count_after(y)
            if before is not None and after is not None and before - after < n:
                # 読むのが早すぎたか、A を取りこぼした。少し待って読み直し、それでも足りなければ押し足す
                time.sleep(0.5)
                frame, y2, after = self._count_after(y)
                if after is not None and 0 <= before - after < n:
                    log.info(f"{name} が {before - after} 個しか選べていないので、{n - (before - after)} 個押し足します")
                    self._tap("a", n - (before - after))
                    frame, y2, after = self._count_after(y)
            if before is not None and after is not None and before - after != n:
                raise StepFailed(f"{name} を {n} 個選べませんでした（{before} 個 → {after} 個）")
            self.last_counts[name] = (after, frame, y2)

    def _selection_order(self, recipe: berries.Recipe) -> list[str]:
        """カーソルから近い順に並べる（並びを覚えていなければ書いた順）。"""
        order = self.berry_list
        if not order or any(name not in order for name in recipe):
            return list(recipe)
        result, pos, rest = [], self.cursor_index or 0, list(recipe)
        while rest:
            name = min(rest, key=lambda b: self._distance(pos, order.index(b), len(order)))
            result.append(name)
            rest.remove(name)
            pos = order.index(name)
        return result

    @staticmethod
    def _distance(i: int, j: int, n: int) -> int:
        return min((j - i) % n, (i - j) % n)  # 一覧は端でつながっている

    def _move(self, i: int, j: int, n: int) -> None:
        down, up = (j - i) % n, (i - j) % n
        self._tap("dpad_down", down) if down <= up else self._tap("dpad_up", up)

    def _count_after(self, y: int) -> tuple[object, int, int | None]:
        """（画面, 行の中心 y, カーソルの行の残り個数）"""
        frame, y2 = self._cursor()
        y2 = y2 or y
        return frame, y2, vision.read_count(frame, y2)

    def move_cursor_to(self, name: str) -> tuple[object, int]:
        """きのみ一覧のカーソルを name に合わせ、（画面, 行の中心 y）を返す。"""
        order = self.berry_list
        if order and name in order and self.cursor_index is not None:
            # 覚えた位置から動かし、着いた先の名前だけ確かめる
            j = order.index(name)
            self._move(self.cursor_index, j, len(order))
            frame, y = self._cursor()
            if y is not None and berries.looks_like(berries.ocr_name(frame, y), name):
                self.cursor_index = j
                return frame, y
            log.info(f"{name} の位置がずれていたので、カーソルの行を読んで合わせ直します")
        self.cursor_index = None
        for attempt in range(6):
            frame, y = self._cursor()
            if y is None:
                continue
            text = berries.ocr_name(frame, y)
            if berries.looks_like(text, name):
                if self.berry_list and name in self.berry_list:
                    self.cursor_index = self.berry_list.index(name)
                return frame, y
            current = berries.identify(text)
            order = self.berry_list
            if attempt >= 3 or order is None or current not in order or name not in order:
                order = self.scan_berry_list()  # 覚えた並びがずれている
                if name not in order:
                    raise OutOfBerries(f"{name} が一覧にありません（持っていない）")
                if current not in order:
                    continue
            self._move(order.index(current), order.index(name), len(order))
        raise StepFailed(f"{name} にカーソルを合わせられませんでした")

    def scan_berry_list(self) -> list[str]:
        """きのみ一覧を下へ一周して並びを読み、覚える（終わるとカーソルは元の位置に戻る）。"""
        log.info("きのみ一覧の並びを読み直します")
        names: list[str | None] = []
        for _ in range(120):
            frame, y = self._cursor()
            if y is None:
                frame, y = self._cursor()
            name = berries.identify(berries.ocr_name(frame, y)) if y else None
            if len(names) > 1 and name is not None and name == names[0]:
                break
            names.append(name)
            self._tap("dpad_down", 1)
        else:
            raise StepFailed("きのみ一覧を一周できませんでした")
        log.info(f"きのみ一覧: {len(names)} 種類（読めなかった行 {names.count(None)}）")
        self.berry_list = names
        berries.save_list(names)
        return names

    def save_berries(self) -> Path | None:
        """直近の試行で選んだきのみの行（名前と「×残り個数」）を、選んだ直後の画面から切り出して並べて保存する。"""
        rows = [frame[y - BERRY_ROW_SPAN[0]:y + BERRY_ROW_SPAN[1], BERRY_ROW_X[0]:BERRY_ROW_X[1]]
                for _, frame, y in self.last_counts.values()]
        return self.save(RESULT_DIR, "berries", np.vstack(rows)) if rows else None

    # ---- 当たりのバックアップ ----

    def backup(self) -> Path:
        """結果画面から、ポケモンセンター：ベールで回復してバックアップを取る。最後の画面の保存先を返す。

        ポケモンセンターの NPC に話しかけるとバックアップが上書きされる。当たりのときだけ呼ぶこと。
        """
        self.press_until("a", "donut_again", tries=3, timeout=2.5)  # 結果画面を閉じる
        self.p.press("b", wait=0)                                    # 「またあとで」
        self.wait_for("talk_anshya", 8)
        self.travel_to_pokecenter()
        return self.save_at_counter()

    def travel_to_pokecenter(self) -> None:
        """ホテルZ（の中か前）から、ポケモンセンター：ベールのカウンター前まで行く。"""
        for _ in range(3):
            self.p.press("plus", wait=0)
            self.wait_for("map", 5)
            time.sleep(0.5)
            self.p.tilt(*MAP_TO_POKECENTER, wait=0.8)
            if vision.visible(self.s.grab(), "map_pc"):
                break
            self.p.press("b", wait=1.5)
        else:
            raise StepFailed("マップでポケモンセンター：ベールを選べませんでした")
        self.p.press("a", wait=0)
        self.wait_for("travel_confirm", 3)
        self.p.press("a", wait=1.0)
        self.wait_for("field_party", 15)
        time.sleep(0.3)
        if not self.walk_until(0.0, 1.0, "talk_pc", 7.0):
            raise StepFailed("ポケモンセンターのカウンターに着けませんでした")

    def refresh_backup(self) -> Path:
        """ドーナツを作る前の状態で、今の時刻のバックアップを取り直す（昼夜の切り替わりを越えた後に使う）。"""
        if not vision.visible(self.s.grab(), "talk_pc"):  # 読み込み直後ならカウンター前にいる
            self.travel_to_pokecenter()
        return self.save_at_counter()

    def save_at_counter(self) -> Path:
        """ポケモンセンターのカウンター前（「A 話しかける」が出ている状態）から、回復してバックアップを取る。"""
        if not vision.visible(self.s.grab(), "talk_pc"):
            raise StepFailed("ポケモンセンターのカウンター前ではありません")
        # 話しかける →「おつかれさまです！」→ メニュー
        self.press_until("a", "pc_menu", tries=4, timeout=2.5)
        # 「休ませる」で回復する（回復しないとバックアップされない可能性があるため）。
        # 回復が終わると「他になにをなさいますか？」でメニューに戻る
        self.p.press("a", wait=2.5)
        for _ in range(10):
            if vision.visible(self.s.grab(), "pc_menu"):
                break
            self.p.press("a", wait=2.5)
        else:
            raise StepFailed("回復後のメニューに戻りませんでした")
        self.p.press("b", wait=1.5)                                  # 「なんでもない」
        self.press_until("a", "field_party", tries=3, timeout=2.5)   # 「またのご利用をお待ちしていますね！」
        # 会話が終わると右上に「レポート中・・・」が出る。消えるまで待ってから完了とする（保存中に終了しないため）
        try:
            self.wait_for("reporting", 5)
        except StepFailed:
            log.info("「レポート中」の表示を確認できませんでした（すでに保存済みの可能性）")
        deadline = time.monotonic() + 30
        clear_since = None
        while clear_since is None or time.monotonic() - clear_since < 1.0:
            if vision.visible(self.s.grab(), "reporting"):
                clear_since = None
            elif clear_since is None:
                clear_since = time.monotonic()
            if time.monotonic() > deadline:
                raise StepFailed("「レポート中」が 30 秒たっても消えませんでした")
            time.sleep(0.1)
        return self.save(BACKUP_DIR, "backup_done")

    # ---- 1 回分 ----

    def wait_cutscene(self) -> bool:
        """昼夜の切り替わりなどの演出中ならパーティ欄が戻るまで待って True。演出中でなければ False。"""
        if vision.visible(self.s.grab(), "field_party"):
            return False
        deadline = time.monotonic() + CUTSCENE_TIMEOUT
        while time.monotonic() < deadline:
            if vision.visible(self.s.grab(), "field_party"):
                time.sleep(1.0)
                if vision.visible(self.s.grab(), "field_party"):
                    return True
            time.sleep(0.5)
        return False

    def attempt(self, on_cutscene: Callable[[], bool] | None = None) -> DonutResult:
        """1 回分。途中で昼夜の切り替わりの演出が入ったら、終わるのを待ってホテルZ への移動からやり直す。

        on_cutscene は演出の後に呼ばれ、True を返すとその場でバックアップを取り直す
        （ドーナツを作る前なので失うものはない。次の試行から同じ演出に当たらなくなる）。
        """
        times = []

        def run(step: Callable[[], None]) -> None:
            t = time.monotonic()
            step()
            times.append(f"{step.__name__}={time.monotonic() - t:.1f}")

        for step in (self.close_software, self.launch_to_title, self.load_backup):
            run(step)
        for retry in range(2):
            try:
                run(self.travel_to_hotel)
                run(self.walk_to_counter)
                break
            except StepFailed as e:
                self.p.release_all()
                if retry or not self.wait_cutscene():
                    raise
                log.info(f"演出が入ったため、ホテルZ への移動からやり直します（{e}）")
                if on_cutscene and on_cutscene():
                    path = self.refresh_backup()
                    log.info(f"バックアップを取り直しました: {path.name}")
                    self.discard(path)
        run(self.make_donut)
        log.debug("所要時間: " + " ".join(times))
        return self.read_result()
