"""Pico W (PicoSwitchController) に手入力で命令を送るコンソール。

使い方:
    python tools/pico_console.py [COMポート]   # 省略時は Raspberry Pi の USB デバイスを自動検出

入力例: PRESS a / HOLD b / RELEASE b / STICK l_stick 1.0 0.0 / SLEEP 0.5
終了: quit または Ctrl+C

ファームウェアの PRESS は Switch に届かないため、PRESS は HOLD → 待機 → RELEASE に置き換えて送る。
"""

import sys
import threading
import time

import serial
from serial.tools import list_ports

RASPBERRY_PI_VID = 0x2E8A
PRESS_HOLD_SEC = 0.1


def find_port() -> str:
    for p in list_ports.comports():
        if p.vid == RASPBERRY_PI_VID:
            return p.device
    sys.exit("Pico W が見つかりません。USB 接続とファームウェアを確認してください。")


def reader(ser: serial.Serial) -> None:
    while ser.is_open:
        try:
            line = ser.readline()
        except serial.SerialException:
            break
        if line:
            print(f"\n[pico] {line.decode(errors='replace').rstrip()}\n> ", end="", flush=True)


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else find_port()
    ser = serial.Serial(port, 115200, timeout=0.2)
    # ファームウェアのスティック初期値は端（0）なので、接続直後に中央へ戻す
    ser.write(b"STICK l_stick 0 0\nSTICK r_stick 0 0\n")
    print(f"{port} に接続しました。quit で終了します。")
    threading.Thread(target=reader, args=(ser,), daemon=True).start()
    try:
        while True:
            cmd = input("> ").strip()
            if cmd.lower() in ("quit", "exit"):
                break
            if not cmd:
                continue
            name, _, args = cmd.partition(" ")
            if name.upper() == "PRESS" and args:
                ser.write(f"HOLD {args}\n".encode())
                time.sleep(PRESS_HOLD_SEC)
                ser.write(f"RELEASE {args}\n".encode())
            else:
                ser.write((cmd + "\n").encode())
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        ser.close()


if __name__ == "__main__":
    main()
