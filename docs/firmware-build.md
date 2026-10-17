# Pico W ファームウェアのビルド手順（Windows）

ファームウェア: [Wilstride/PicoSwitchController](https://github.com/Wilstride/PicoSwitchController)（commit `442550d`）

## 配置

| もの | 場所 |
|---|---|
| Pico SDK 2.2.0（サブモジュール込み） | `C:\Users\azhid\pico\pico-sdk` |
| ファームウェアのソース | `C:\Users\azhid\pico\PicoSwitchController` |
| picotool 2.1.1（ビルド済み） | `C:\Users\azhid\pico\picotool-dist\picotool` |
| pioasm 2.2.0（ビルド済み） | `C:\Users\azhid\pico\sdktools-dist\pioasm` |
| CMake / Ninja / Arm GNU Toolchain 14.2 | winget でインストール |

PC 用の C/C++ コンパイラがないため、picotool と pioasm は SDK にソースからビルドさせず、
[pico-sdk-tools](https://github.com/raspberrypi/pico-sdk-tools/releases) のビルド済みバイナリを使う。
pioasm は SDK と同じバージョンでないと採用されない点に注意。

## ビルド（Git Bash）

```bash
P=/c/Users/azhid/AppData/Local/Microsoft/WinGet/Packages
export PATH="$P/Kitware.CMake_Microsoft.Winget.Source_8wekyb3d8bbwe/cmake-4.4.4-windows-x86_64/bin:$P/Ninja-build.Ninja_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Program Files (x86)/Arm GNU Toolchain arm-none-eabi/14.2 rel1/bin:$PATH"
export PICO_SDK_PATH=C:/Users/azhid/pico/pico-sdk
cd /c/Users/azhid/pico/PicoSwitchController
cmake -S . -B build -G Ninja \
  -Dpicotool_DIR=C:/Users/azhid/pico/picotool-dist/picotool \
  -Dpioasm_DIR=C:/Users/azhid/pico/sdktools-dist/pioasm
cmake --build build
```

成果物: `build/src/autoshine_pico_firmware.uf2`

## 書き込み

BOOTSEL を押しながら USB 接続し、表示された `RPI-RP2` ドライブに uf2 をコピーする。
再起動後、`USB シリアル デバイス (COMx)`（VID 2E8A）として認識される。
