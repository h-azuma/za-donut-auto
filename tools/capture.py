"""キャプチャボードの映像をプレビューし、スクリーンショットを保存するツール。

使い方:
    python tools/capture.py [--device "MiraBox Video Capture"] [--out screenshots]

操作（プレビューウィンドウ上で）:
    s : スクリーンショットを保存（1920x1080 の原寸 PNG）
    q / Esc : 終了
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import cv2
from pygrabber.dshow_graph import FilterGraph

WIDTH, HEIGHT = 1920, 1080
DEFAULT_DEVICE = "MiraBox Video Capture"


def find_device_index(name: str) -> int:
    devices = FilterGraph().get_input_devices()
    for i, d in enumerate(devices):
        if name.lower() in d.lower():
            return i
    sys.exit(f"'{name}' が見つかりません。接続中のデバイス: {devices}")


def open_capture(name: str = DEFAULT_DEVICE) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(find_device_index(name), cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    if not cap.isOpened():
        sys.exit(f"'{name}' を開けません。OBS など他のアプリが使用中でないか確認してください。")
    return cap


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--out", default="screenshots", type=Path)
    args = parser.parse_args()

    cap = open_capture(args.device)
    args.out.mkdir(parents=True, exist_ok=True)
    print("s: スクショ保存 / q: 終了")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("フレームを取得できませんでした。")
            break
        cv2.imshow("capture (s: save / q: quit)", cv2.resize(frame, (960, 540)))
        key = cv2.waitKey(1) & 0xFF
        if key == ord("s"):
            path = args.out / f"{datetime.now():%Y%m%d_%H%M%S_%f}.png"
            cv2.imwrite(str(path), frame)
            print(f"保存: {path}")
        elif key in (ord("q"), 27):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
