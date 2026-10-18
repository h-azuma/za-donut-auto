"""結果画面のパワー欄（3 行）を OCR で読み、既知のパワー名に当てはめる。

Windows 標準の OCR（日本語）を使う。濁点や「エ」を取り違えることがあるので、読んだ文字列を
CATALOG の名前と照らし合わせて一番近いものを採る。どれにも十分近くなければ名前は None（未知のパワー）。
"""

import asyncio
import difflib
import re
import unicodedata
from dataclasses import dataclass

import cv2
import numpy as np
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

TYPES = ["ノーマル", "ほのお", "みず", "でんき", "くさ", "こおり", "かくとう", "どく", "じめん",
         "ひこう", "エスパー", "むし", "いわ", "ゴースト", "ドラゴン", "あく", "はがね", "フェアリー"]

# サワー系（実機の結果画面で表記を確認したもの）。カテゴリごとに 1 つのドーナツに 1 つまで付く
SOUR = {
    "どうぐ": ["どうぐパワー：きのみ", "どうぐパワー：ボール", "どうぐパワー：おたから", "どうぐパワー：アメ",
             "どうぐパワー：コイン", "どうぐパワー：とくべつ"],
    "異次元": ["どっさりパワー", "メガパワー：チャージ", "メガパワー：ながもち"],
}
# スイート系（表記は攻略サイトの情報。実機で確認したら更新する）
SWEET = {
    "かがやき": [f"かがやきパワー：{t}" for t in TYPES] + ["かがやきパワー：ぜんぶ"],
    "大きさ": ["オヤブンパワー", "でかでかパワー", "ちびちびパワー"],
}
# フレッシュ系（ハバンのドーナツで実機で確認したもの）
FRESH = {
    "ほかく": [f"ほかくパワー：{t}" for t in TYPES] + ["ほかくパワー：すべて"],
}
# 照らし合わせに使う名前の一覧（サワー・スイート・フレッシュ以外も、実機で見たものは入れておく）
CATALOG = ([name for group in (SOUR, SWEET, FRESH) for names in group.values() for name in names]
           + [f"わざパワー：{t}" for t in TYPES] + ["こうげきパワー", "とくこうパワー", "すばやさパワー"])
# パワー名 → カテゴリ（同じカテゴリのパワーは 1 つのドーナツに 1 つまで）。分かっているサワー系・スイート系だけ
CATEGORY = {name: category for group in (SOUR, SWEET, FRESH) for category, names in group.items() for name in names}

DAKUTEN = "\u3099\u309a"  # 結合用の濁点・半濁点
MATCH_MIN = 0.65  # 別のパワーどうしの似ている度合いは最大 0.5。1 文字の読み違い（こおり→こあり 0.67）は拾う

ROW_TOP = 830     # 1 行目の文字の上端（1920x1080）
ROW_PITCH = 50
ROWS = 3
ROW_X = (240, 760)

_engine = None


@dataclass(frozen=True)
class Power:
    name: str | None   # CATALOG の名前。未知なら None
    level: int | None  # 読めなければ None
    raw: str           # OCR の生の結果（空白を除いたもの）

    def __str__(self) -> str:
        name = self.name or f"?（{self.raw}）"
        return f"{name} Lv.{self.level if self.level is not None else '?'}"


def _ocr(img: np.ndarray) -> str:
    global _engine
    if _engine is None:
        _engine = OcrEngine.try_create_from_language(Language("ja"))
        if _engine is None:
            raise RuntimeError("Windows の日本語 OCR が使えません（言語パックを確認してください）")

    async def run() -> str:
        rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        h, w = rgba.shape[:2]
        writer = DataWriter()
        writer.write_bytes(rgba.tobytes())
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
        return (await _engine.recognize_async(bitmap)).text

    return asyncio.run(run())


def _normalize(text: str) -> str:
    return text.replace(" ", "").replace(":", "：")


# OCR がよく取り違える文字（「ゴースト」→「コ・一スト」、「エ」→「工」など）
OCR_FIX = str.maketrans({"一": "ー", "工": "エ", "八": "ハ", "・": "", "．": "", "…": ""})
LEVEL = re.compile(r"[LlI|1][_]?[vV]\.?([123])")  # 「Lv.3」が「l_v.3」などに化けても読む


def parse_row(text: str) -> Power | None:
    """OCR の 1 行（例「どうぐパワー：きのみLv.3」）を Power にする。空行なら None。"""
    raw = _normalize(text)
    if not raw:
        return None
    fixed = raw.translate(OCR_FIX)
    m = LEVEL.search(fixed)
    level = int(m.group(1)) if m else None
    body = fixed[:m.start()] if m else fixed
    best, score = None, 0.0
    for name in CATALOG:
        s = _similarity(body, name)
        if s > score:
            best, score = name, s
    return Power(best if score >= MATCH_MIN else None, level, raw)


def _base(text: str) -> str:
    """濁点・半濁点を取る（OCR は「ぐ」→「く」、「パ」→「ハ」のように落としやすい）。"""
    return "".join(c for c in unicodedata.normalize("NFD", text) if c not in DAKUTEN)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _base(a), _base(b)).ratio()


def _similarity(text: str, name: str) -> float:
    """「：」の前（パワーの種類）と後ろ（中身）を別々に比べ、低いほうを返す。

    全体で比べると「どうぐパワー：」が長く一致するだけで、未知の「どうぐパワー：○○」が
    既知のどうぐパワーに似てしまうため。
    """
    # 「：」は「.」などに化けることがあるので、「ワー」（パワー）の後ろで種類と中身に分ける
    i = text.find("ワー")
    t_head, t_tail = (text[:i + 2], text[i + 2:].strip("：:.")) if i >= 0 else (text, "")
    n_head, _, n_tail = name.partition("：")
    if not n_tail:  # 「どっさりパワー」のように中身が無いもの
        return _ratio(t_head, n_head) if not t_tail else 0.0
    return min(_ratio(t_head, n_head), _ratio(t_tail, n_tail))


def read_row(frame: np.ndarray, row: int) -> Power | None:
    top = ROW_TOP + ROW_PITCH * row
    img = frame[top - 6:top + 38, ROW_X[0]:ROW_X[1]]
    big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return parse_row(_ocr(big))


def read_powers(frame: np.ndarray) -> tuple[Power, ...]:
    """結果画面のパワー欄を上から読む（空の行は含めない）。"""
    return tuple(p for row in range(ROWS) if (p := read_row(frame, row)) is not None)
