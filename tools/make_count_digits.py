"""きのみの残り個数を読むための数字の見本（templates/count_N.png）を、実機のスクショから作る。

使い方:
    python tools/make_count_digits.py

SAMPLES は（画像、行の中心の y、写っている数）。7 はまだ見本がなく、初めて読んだときに自動で登録される
（za_donut/vision.py の read_count）。
"""

import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from za_donut.vision import TEMPLATE_DIR, count_glyphs  # noqa: E402

STEPS = ROOT / "screenshots" / "steps"
LIST_ROWS = [327 + 93 * k for k in range(8)]  # きのみ一覧の各行の中心（1920x1080）

SAMPLES = [
    (STEPS / "20261017_121641_select_berries_berry_top.png", LIST_ROWS, [32, 50, 53, 55, 58, 60, 32, 36]),
    (STEPS / "20261017_121647_select_berries_berry_selected.png", LIST_ROWS, [4, 290, 28, 2, 249, 246, 230, 233]),
    (STEPS / "20261017_205922_bk_berries.png", [65, 160], [18, 203]),  # カシブとロゼルの行の切り出し
    (STEPS / "20261017_2200_bk_order_live.png", [513, 978], [14, 191]),
]


def main() -> None:
    glyphs = defaultdict(list)
    for path, rows, values in SAMPLES:
        frame = cv2.imread(str(path))
        if frame is None:
            raise FileNotFoundError(path)
        for y, value in zip(rows, values):
            g = count_glyphs(frame, y)
            if len(g) != len(str(value)):
                raise ValueError(f"{path.name} y={y}: {value} のはずが {len(g)} 文字に分かれました")
            for d, img in zip(str(value), g):
                glyphs[int(d)].append(img.astype(np.float32))
    for d, imgs in sorted(glyphs.items()):
        cv2.imwrite(str(TEMPLATE_DIR / f"count_{d}.png"), np.mean(imgs, axis=0).astype(np.uint8))
    print(f"数字 {sorted(glyphs)} の見本を作りました（見本の数: {', '.join(f'{d}={len(v)}' for d, v in sorted(glyphs.items()))}）")


if __name__ == "__main__":
    main()
