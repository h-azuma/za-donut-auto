"""making-donut.md の手順を、調整しやすい単位に区切った操作。

秒数は未検証の仮の値。tools/run_step.py で 1 ステップずつ実行し、スクショを見ながら調整する。
各ステップは shot(name) を呼ぶと、その時点の画面を保存する。
"""

from typing import Callable

from .pico import Pico

Shot = Callable[[str], None]

# ---- 調整用の値（秒） ----
WAIT_USER_SELECT = 1.5   # ソフト選択 → ユーザー選択画面が出るまで
WAIT_TITLE = 10.0        # ユーザー選択 → タイトル画面が操作可能になるまで（古い画面での観測では 5 秒以内。要再確認）
WAIT_BACKUP_MENU = 1.5   # 隠しコマンド → バックアップのモーダルが出るまで
WAIT_DIALOG = 1.0        # ダイアログ送りの間隔
WAIT_LOAD = 25.0         # バックアップ読み込み → ポケモンセンターで操作可能になるまで
WAIT_MAP = 1.5           # + でマップが開くまで
MAP_TO_HOTEL = (-1.0, 0.1, 0.22)   # マップでポケセン:ベール → ホテルZ へのカーソル移動 (x, y, 秒)。0.6 秒だと「いしや」まで行き過ぎる
WAIT_TRAVEL = 8.0        # ファストトラベル完了まで
WALK_TO_DOOR = (0.0, 1.0, 2.4)     # ホテルZ入口前 → 「A はいる」が出る位置まで (x, y, 秒)
WAIT_ENTER = 5.0         # ホテルZ に入る暗転（実測 3〜4 秒）
WALK_TO_COUNTER_UP = (0.0, 1.0, 2.3)     # ロビー入口 → カウンター右手前
WALK_TO_COUNTER_LEFT = (-1.0, 0.0, 0.75)  # → 「A 話しかける アンシャ」が出る位置
MENU_TAP = 0.1           # きのみ選択でスティックを 1 回入力する長さ
WAIT_MENU_TAP = 0.3
WAIT_BERRY_SCREEN = 2.0  # 「ドーナツをつくる」→ きのみ選択画面が出るまで
WAIT_COOK = 3.0          # アニメーションをスキップしてから完成画面まで
WAIT_RESULT = 2.0        # 完成画面 → 結果画面
WAIT_HOME = 1.5


def launch(p: Pico, shot: Shot) -> None:
    """HOME（カーソルは ZA）→ ユーザー選択 → タイトル画面。"""
    p.press("a", wait=WAIT_USER_SELECT)
    shot("user_select")
    p.press("a", wait=WAIT_TITLE)
    shot("title")


def backup_menu(p: Pico, shot: Shot) -> None:
    """タイトル画面で X + B + 上 → バックアップのモーダル。"""
    p.press("x", "b", "dpad_up", hold=0.5, wait=WAIT_BACKUP_MENU)
    shot("backup_menu")


def backup_load(p: Pico, shot: Shot) -> None:
    """「バックアップで始める」→ 確認ダイアログ 3 つ → ポケモンセンターで再開。"""
    p.press("a", wait=WAIT_DIALOG)
    shot("backup_loaded")
    p.press("a", wait=WAIT_DIALOG)
    p.press("a", wait=WAIT_DIALOG)
    shot("backup_warning")
    p.press("a", wait=WAIT_LOAD)
    shot("pokecenter")


def to_hotel_map(p: Pico, shot: Shot) -> None:
    """マップを開き、カーソルをホテルZ に合わせる（確定はしない）。"""
    p.press("plus", wait=WAIT_MAP)
    shot("map_open")
    x, y, sec = MAP_TO_HOTEL
    p.tilt(x, y, sec)
    shot("map_hotel")


def to_hotel_travel(p: Pico, shot: Shot) -> None:
    """ホテルZ へファストトラベル（「はい」はデフォルト選択）。"""
    p.press("a", wait=WAIT_DIALOG)
    shot("travel_confirm")
    p.press("a", wait=WAIT_TRAVEL)
    shot("hotel_front")


def to_counter(p: Pico, shot: Shot) -> None:
    """ホテルZ に入り、ドーナツのカウンターまで歩く。"""
    p.tilt(*WALK_TO_DOOR)
    shot("hotel_door")
    p.press("a", wait=WAIT_ENTER)
    shot("hotel_inside")
    p.tilt(*WALK_TO_COUNTER_UP)
    p.tilt(*WALK_TO_COUNTER_LEFT)
    shot("counter")


def _menu_up(p: Pico, times: int) -> None:
    for _ in range(times):
        p.tilt(0.0, 1.0, MENU_TAP, wait=WAIT_MENU_TAP)


def select_berries(p: Pico, shot: Shot) -> None:
    """アンシャに話しかけ、いじげんロゼル ×6 と いじげんカシブ ×2 を選ぶ。"""
    p.press("a", wait=WAIT_DIALOG)        # 話しかける
    p.press("a", wait=WAIT_DIALOG)        # 「ようこそ！」
    shot("menu_make")
    p.press("a", wait=WAIT_BERRY_SCREEN)  # 「ドーナツをつくる」
    shot("berry_top")
    _menu_up(p, 1)                        # クラボ → 一番下のいじげんロゼル
    shot("berry_rozel")
    for _ in range(6):
        p.press("a", wait=0.3)
    _menu_up(p, 5)                        # → いじげんカシブ
    shot("berry_kasib")
    for _ in range(2):
        p.press("a", wait=0.3)
    shot("berry_selected")


def cook(p: Pico, shot: Shot) -> None:
    """+ で作成 → アニメーションをスキップ → 結果画面。"""
    # 実測（2026-10-17）: 「うけたまわりましたわ」の直後の A は効かず、その次の A でアニメーションが
    # スキップされて完成ドーナツの提示画面になり、さらに A で結果画面になった。
    # 押しすぎると結果画面を閉じてしまうため、本番では画面判定で進める。
    p.press("plus", wait=WAIT_DIALOG)
    shot("cook_dialog")
    p.press("a", wait=1.5)                # 「うけたまわりましたわ！」
    p.press("a", wait=1.0)
    p.press("a", wait=WAIT_COOK)          # アニメーションをスキップ
    shot("donut_done")
    p.press("a", wait=WAIT_RESULT)
    shot("result")


def reset(p: Pico, shot: Shot) -> None:
    """HOME → X でソフト終了 → 「終了する」。"""
    p.press("home", wait=WAIT_HOME)
    p.press("x", wait=WAIT_DIALOG)
    shot("close_dialog")
    p.press("a", wait=3.0)
    shot("home")


STEPS = {
    "launch": launch,
    "backup_menu": backup_menu,
    "backup_load": backup_load,
    "to_hotel_map": to_hotel_map,
    "to_hotel_travel": to_hotel_travel,
    "to_counter": to_counter,
    "select_berries": select_berries,
    "cook": cook,
    "reset": reset,
}
