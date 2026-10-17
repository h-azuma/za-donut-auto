"""キャプチャボードからの画面取得。

DirectShow はフレームをバッファに溜めるため、読むのを止めていると数秒〜数十秒前の画面が返る。
そのため裏のスレッドで読み続け、常に最新の 1 枚だけを保持する。
"""

import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from pygrabber.dshow_graph import FilterGraph

WIDTH, HEIGHT = 1920, 1080
DEFAULT_DEVICE = "MiraBox Video Capture"


def find_device_index(name: str) -> int:
    devices = FilterGraph().get_input_devices()
    for i, d in enumerate(devices):
        if name.lower() in d.lower():
            return i
    raise RuntimeError(f"'{name}' が見つかりません。接続中のデバイス: {devices}")


class Screen:
    def __init__(self, device: str = DEFAULT_DEVICE):
        self._cap = cv2.VideoCapture(find_device_index(device), cv2.CAP_DSHOW)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        if not self._cap.isOpened():
            raise RuntimeError(f"'{device}' を開けません。OBS など他のアプリが使用中でないか確認してください。")
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._frame_time = 0.0
        self._running = True
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        while self._running:
            ok, frame = self._cap.read()
            if ok:
                with self._lock:
                    self._frame = frame
                    self._frame_time = time.monotonic()

    def close(self) -> None:
        self._running = False
        self._thread.join(timeout=2)
        self._cap.release()

    def __enter__(self) -> "Screen":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def grab(self, timeout: float = 3.0) -> np.ndarray:
        """呼び出した時点より後に届いたフレームを返す。"""
        since = time.monotonic()
        deadline = since + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._frame is not None and self._frame_time > since:
                    return self._frame.copy()
            time.sleep(0.005)
        raise RuntimeError("新しいフレームが届きません。キャプチャの接続を確認してください。")

    def save(self, out_dir: Path, name: str) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{name}.png"
        cv2.imwrite(str(path), self.grab())
        return path
