"""Pico W（PicoSwitchController ファームウェア）をシリアル経由で操作する。

ファームウェアの癖への対処:
- PRESS は押下と解放が同じ送信周期に収まり Switch に届かないため、HOLD → 待機 → RELEASE で押す
- スティックの初期値が端（0）なので、接続直後に中央へ戻す
"""

import time

import serial
from serial.tools import list_ports

RASPBERRY_PI_VID = 0x2E8A
BUTTONS = (
    "a", "b", "x", "y", "l", "r", "zl", "zr", "plus", "minus", "home", "capture",
    "l_stick", "r_stick", "dpad_up", "dpad_down", "dpad_left", "dpad_right",
)


def find_port() -> str:
    for p in list_ports.comports():
        if p.vid == RASPBERRY_PI_VID:
            return p.device
    raise RuntimeError("Pico W が見つかりません。USB 接続とファームウェアを確認してください。")


class Pico:
    def __init__(self, port: str | None = None):
        self._ser = serial.Serial(port or find_port(), 115200, timeout=0.05)
        self.release_all()

    def close(self) -> None:
        self.release_all()
        self._ser.close()

    def __enter__(self) -> "Pico":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _send(self, line: str) -> None:
        self._ser.write((line + "\n").encode())

    def press(self, *buttons: str, hold: float = 0.1, wait: float = 0.5) -> None:
        """ボタンを同時に押して離し、wait 秒待つ。"""
        names = " ".join(buttons)
        self._send(f"HOLD {names}")
        time.sleep(hold)
        self._send(f"RELEASE {names}")
        time.sleep(wait)

    def tilt(self, x: float, y: float, sec: float, stick: str = "l_stick", wait: float = 0.5) -> None:
        """スティックを (x, y) に sec 秒倒して戻し、wait 秒待つ。x: 右が +、y: 上が +。"""
        self._send(f"STICK {stick} {x} {y}")
        time.sleep(sec)
        self._send(f"STICK {stick} 0 0")
        time.sleep(wait)

    def stick(self, x: float, y: float, stick: str = "l_stick") -> None:
        """スティックを (x, y) に倒したままにする（戻すときは stick(0, 0)）。"""
        self._send(f"STICK {stick} {x} {y}")

    def release_all(self) -> None:
        for b in BUTTONS:
            self._send(f"RELEASE {b}")
        self._send("STICK l_stick 0 0")
        self._send("STICK r_stick 0 0")
