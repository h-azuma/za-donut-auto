"""実機のスクリーンショットから、画面判定用のテンプレート画像を切り出す。

使い方:
    python tools/make_templates.py            # templates/ に書き出し
    python tools/make_templates.py --sheet x.png  # 切り出し結果の一覧画像も作る（確認用）

座標の定義は za_donut/vision.py の SPECS（1920x1080 基準）。
"""

import argparse
from pathlib import Path

import cv2
import numpy as np

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from za_donut.vision import SPECS, TEMPLATE_DIR as OUT  # noqa: E402

SHOTS = ROOT / "screenshots" / "steps"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet", type=Path)
    args = parser.parse_args()

    OUT.mkdir(exist_ok=True)
    tiles = []
    for name, (src, x, y, w, h) in SPECS.items():
        img = cv2.imread(str(SHOTS / src))
        if img is None:
            raise FileNotFoundError(SHOTS / src)
        crop = img[y:y + h, x:x + w]
        cv2.imwrite(str(OUT / f"{name}.png"), crop)
        if args.sheet:
            tile = np.full((150, 760, 3), 40, np.uint8)
            s = min(700 / w, 100 / h, 4)
            c = cv2.resize(crop, None, fx=s, fy=s)
            tile[40:40 + c.shape[0], 10:10 + c.shape[1]] = c
            cv2.putText(tile, name, (10, 28), 0, 0.8, (0, 255, 255), 2)
            tiles.append(tile)
    if args.sheet:
        if len(tiles) % 2:
            tiles.append(np.full_like(tiles[0], 40))
        rows = [np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]
        cv2.imwrite(str(args.sheet), np.vstack(rows))
    print(f"{len(SPECS)} 個のテンプレートを {OUT} に書き出しました。")


if __name__ == "__main__":
    main()
