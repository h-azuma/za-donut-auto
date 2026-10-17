"""テンプレートマッチングによる画面判定と、きのみの残り個数の読み取り。

テンプレートは tools/make_templates.py が SPECS の座標で実機スクショから切り出したもの。
"""

from pathlib import Path

import cv2
import numpy as np

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
THRESHOLD = 0.85
MARGIN = 40  # 元の位置から上下左右にこれだけずれていても見つける

# name: (元画像, x, y, w, h)  ※ 1920x1080 基準。元画像は screenshots/steps/ 内
SPECS = {
    "home_gear":        ("20261017_121828_reset_home.png",             1360, 805, 70, 70),
    "home_playing":     ("20261017_121044_obs_close_dialog.png",        325, 595, 140, 55),
    "close_dialog":     ("20261017_121056_obs_close_dialog_later.png",  875, 430, 430, 50),
    "user_select":      ("20261017_121204_launch_user_select.png",      115, 505, 480, 45),
    "title":            ("20261017_121215_launch_title.png",            712, 860, 495, 48),
    "backup_menu":      ("20261017_121228_backup_menu_backup_menu.png", 325, 195, 235, 50),
    "backup_loaded":    ("20261017_121253_backup_load_backup_loaded.png", 540, 880, 680, 50),
    "backup_warning":   ("20261017_121256_backup_load_backup_warning.png", 540, 880, 700, 110),
    "field_party":      ("20261017_121321_backup_load_pokecenter.png",   80, 935, 480, 100),
    "map":              ("20261017_121340_to_hotel_map_map_open.png",    80, 25, 195, 55),
    "map_hotel":        ("20261017_121404_map_hotel_t022.png",         1250, 180, 170, 50),
    "travel_confirm":   ("20261017_121420_to_hotel_travel_travel_confirm.png", 535, 860, 495, 75),
    "door_prompt":      ("20261017_121518_door_retry_before_a.png",    1030, 608, 130, 44),
    "talk_anshya":      ("20261017_121609_lobby_left_0.75s.png",       1066, 664, 112, 34),  # 「A 話しかける」の下の名前「アンシャ」（ゴクリン等と区別する）
    "talk_prompt":      ("20261017_121609_lobby_left_0.75s.png",       1030, 612, 170, 48),  # 「A 話しかける」（相手は問わない）
    "donut_again":      ("20261017_142832_bk_result_closed.png",       540, 945, 560, 50),  # 「まだドーナツをつくりますか？」
    "map_pc":           ("20261017_142913_bk_map_pc.png",             1300, 180, 350, 50),  # マップで「ポケモンセンター：ベール」を選択中
    "talk_pc":          ("20261017_143011_bk_pc_counter.png",         1030, 625, 170, 45),  # ポケセンのカウンターで「A 話しかける」
    "reporting":        ("20261017_153602_bk_reporting.png",          1695, 75, 140, 40),  # 右上の「レポート中・・・」（保存中）
    "pc_menu":          ("20261017_143258_bk_pc_choice.png",          1430, 660, 280, 130),  # ポケセンの「ショッピング / なんでもない」
    "menu_make":        ("20261017_121639_select_berries_menu_make.png", 1440, 662, 222, 44),
    "berry_order":      ("20261017_121641_select_berries_berry_top.png",  710, 600, 190, 70),
    "cook_dialog":      ("20261017_121716_cook_cook_dialog.png",        540, 880, 440, 110),
    "cook_present":     ("20261017_1400_cook_present.png",            1310, 640, 200, 140),  # できたドーナツを見せる場面（A で結果画面へ）。アンシャのエプロン
    "result_pluslv":    ("20261017_121755_result_t3.png",               850, 683, 150, 34),
    "result_date":      ("20261017_121755_result_t3.png",               712, 976, 84, 36),
    "result_next":      ("20261017_122909_result_late.png",            1745, 1018, 125, 44),  # 「A つぎへ」＝パワー欄の表示完了
}

# キャラクターの位置で表示場所が変わるものは画面全体から探す
SEARCH_FULL = {"door_prompt", "talk_anshya", "talk_prompt", "talk_pc"}


_cache: dict[str, np.ndarray] = {}


def template(name: str) -> np.ndarray:
    if name not in _cache:
        img = cv2.imread(str(TEMPLATE_DIR / f"{name}.png"))
        if img is None:
            raise FileNotFoundError(f"テンプレート {name} がありません。tools/make_templates.py を実行してください。")
        _cache[name] = img
    return _cache[name]


def _best(region: np.ndarray, tpl: np.ndarray) -> tuple[float, tuple[int, int]]:
    if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
        return 0.0, (0, 0)
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    return float(score), loc


# 背景が透ける白文字は、白い画素だけを取り出して比べる（カメラの向きで背景が変わっても一致度が落ちにくい）
BINARIZE = {"talk_pc"}


def _white(img: np.ndarray) -> np.ndarray:
    return (cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) > 200).astype(np.uint8) * 255


def score(frame: np.ndarray, name: str) -> float:
    """テンプレート name が画面にどれだけ一致するか（0〜1）。"""
    tpl = template(name)
    if name in SEARCH_FULL:
        region = frame
    else:
        _, x, y, w, h = SPECS[name]
        y0, x0 = max(0, y - MARGIN), max(0, x - MARGIN)
        region = frame[y0:y + h + MARGIN, x0:x + w + MARGIN]
    if name in BINARIZE:
        region, tpl = _white(region), _white(tpl)
    return _best(region, tpl)[0]


# テンプレートごとのしきい値（既定は THRESHOLD）
THRESHOLDS = {
    "result_next": 0.75,     # 背景が透けて一致度が下がりやすい（誤検出側は 0.3 程度）
    "door_prompt": 0.75,     # 表示がフェードインする途中で止まれるように（出ていないときは 0.4 程度）
    "talk_pc": 0.7,          # 白文字だけで比べて 0.82〜1.0（出ていないときは 0.35 程度）
    "field_party": 0.7,      # 夜のホテルZ前では 0.82 程度（読み込み中は 0.13 以下）
    "cook_present": 0.93,    # フェードイン途中（0.85 前後）の A は無視されるので、表示しきってから（静止時 0.97）
}


def visible(frame: np.ndarray, name: str, threshold: float | None = None) -> bool:
    return score(frame, name) >= (threshold or THRESHOLDS.get(name, THRESHOLD))


# ---- きのみの残り個数（きのみ選択画面の「×233」など）----

COUNT_X = (28, 120)       # 「×数字」の横の範囲（右寄せ）
COUNT_HALF_H = 18         # 行の中心から上下にこれだけ
COUNT_DIGIT_SIZE = (16, 24)
COUNT_MIN_SCORE = 0.7      # これより低い文字があれば読めなかったことにする（実測の最低は 0.77）


def count_glyphs(frame: np.ndarray, row_y: int) -> list[np.ndarray]:
    """行の「×数字」から、数字 1 文字ずつの白黒画像（COUNT_DIGIT_SIZE）を左から順に返す。"""
    region = frame[row_y - COUNT_HALF_H:row_y + COUNT_HALF_H, COUNT_X[0]:COUNT_X[1]]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    if np.median(v) > 128:  # カーソルが乗っている行は明るい背景に暗い紺色の文字
        mask = v < 110
    else:                   # それ以外は暗い背景に白い文字（色の付いたきのみのアイコンは除く）
        mask = (v > 190) & (s < 50)
    b = mask.astype(np.uint8) * 255
    _, _, stats, _ = cv2.connectedComponentsWithStats(b)
    boxes = sorted((x, y, w, h) for x, y, w, h, area in stats[1:] if 15 <= h <= 24 and area >= 20)  # 「×」は背が低いので落ちる
    return [_normalize_glyph(b[y:y + h, x:x + w]) for x, y, w, h in boxes]


def _normalize_glyph(glyph: np.ndarray) -> np.ndarray:
    """高さをそろえ、横幅は縦横比のまま中央に置く（細い「1」を引き伸ばすと形が崩れるため）。"""
    W, H = COUNT_DIGIT_SIZE
    h, w = glyph.shape
    nw = max(1, min(W, round(w * H / h)))
    resized = cv2.resize(glyph, (nw, H), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((H, W), np.uint8)
    x0 = (W - nw) // 2
    canvas[:, x0:x0 + nw] = resized
    return canvas


def _count_template(d: int) -> np.ndarray | None:
    path = TEMPLATE_DIR / f"count_{d}.png"
    return cv2.imread(str(path), cv2.IMREAD_GRAYSCALE) if path.exists() else None


def read_count(frame: np.ndarray, row_y: int) -> int | None:
    """きのみの残り個数を読む。どの見本にも十分に合わない文字があれば None。"""
    glyphs = count_glyphs(frame, row_y)
    if not glyphs or len(glyphs) > 4:
        return None
    templates = {d: t for d in range(10) if (t := _count_template(d)) is not None}
    digits = []
    for glyph in glyphs:
        scores = {d: float(cv2.matchTemplate(glyph, t, cv2.TM_CCOEFF_NORMED)[0, 0]) for d, t in templates.items()}
        best = max(scores, key=scores.get)
        if scores[best] < COUNT_MIN_SCORE:
            return None
        digits.append(best)
    return int("".join(map(str, digits)))


def learn_count(frame: np.ndarray, row_y: int, value: int) -> list[int]:
    """正しい個数 value が分かっているとき、まだ見本の無い数字をこの画面から見本として保存する。保存した数字を返す。"""
    glyphs = count_glyphs(frame, row_y)
    if len(glyphs) != len(str(value)):
        return []
    learned = []
    for ch, glyph in zip(str(value), glyphs):
        d = int(ch)
        if _count_template(d) is None and d not in learned:
            cv2.imwrite(str(TEMPLATE_DIR / f"count_{d}.png"), glyph)
            learned.append(d)
    return learned
