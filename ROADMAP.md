# ZA ドーナツ厳選 自動化 ロードマップ

Pokémon Legends Z-A 追加コンテンツのドーナツ作成を自動化し、指定したパワーが付与されるまで「作成 → リセット」を繰り返す。

## 要件

- 使用するきのみを最大 7 つまで指定できる
- 指定したパワーがドーナツに付与されるまで、ドーナツ作成 → リセットを繰り返す
- 指定パワーのドーナツができたら、アラート音で知らせて操作を停止する

## システム構成

```
Switch ──HDMI──▶ キャプチャボード ──USB──▶ PC（Python + OpenCV）
  ▲                                            │ 画面を判定して次の操作を決定
  └──Bluetooth（プロコンとして接続）── Pico W ◀──USB（シリアル）┘
```

| 役割 | 担当 |
|---|---|
| 画面の取り込み | キャプチャボード（UVC 対応） |
| 判定・制御（頭脳） | PC 上の Python + OpenCV |
| ボタン入力（手） | Raspberry Pi Pico W（Bluetooth でプロコンを模倣） |

---

## Step 0. 機材の準備

- [x] Raspberry Pi Pico W（または Pico 2 W）を購入
- [x] データ通信対応の micro USB ケーブルを用意
- [x] UVC 対応キャプチャボード（1080p・パススルー付き推奨）を購入（MiraBox Video Capture）
- [x] PC に Python 3 をインストール（3.11）

## Step 1. ゲーム側の情報収集

- [x] ドーナツ作成の操作手順（ボタン操作の流れ）を書き出す → [making-donut.md](making-donut.md)
- [x] 各画面のスクリーンショットを撮る → `screenshots/steps/`（`tools/run_step.py` で区切りごとに実行・撮影）
- [x] オートセーブをオフにできるか確認する → できない。ドーナツ作成時に必ずオートセーブされる
- [x] リセット方法を決める → HOME → ソフト終了 → 再起動 → タイトルで X＋B＋上 → バックアップで始める
  - バックアップはポケモンセンターのカウンターで話しかけたときに取られる（ポケモンセンター：ベール前提）

## Step 2. キャプチャの動作確認

- [x] キャプチャボードを PC につなぎ、OpenCV で映像を取得できることを確認（MiraBox Video Capture、DirectShow）
- [x] 解像度（1920×1080 など）を固定し、スクリーンショットの保存機能を作る → `tools/capture.py`
- [x] Pico で操作 → キャプチャで画面変化を確認、の一巡を確認

## Step 3. Pico W のセットアップ

- [x] ビルド環境を用意する（Pico SDK・CMake・Ninja・Arm GNU Toolchain）→ [docs/firmware-build.md](docs/firmware-build.md)
- [x] Pico W にファームウェアを書き込む（Bluetooth Pro Controller 模倣 + USB シリアルで命令受信）
  - 採用: [Wilstride/PicoSwitchController](https://github.com/Wilstride/PicoSwitchController)（GPL-3.0、ビルド済み uf2 の配布なし）
  - 命令: `PRESS` / `HOLD` / `RELEASE` / `STICK` / `SLEEP`（115200bps、改行区切り）。README にある `CENTER_STICKS` / `RELEASE_ALL` は未実装
- [x] Switch とペアリングできることを確認（Switch 2 で「Pro Controller」として認識）
- [x] PC からシリアルで命令を送り、ボタンが押せることを確認（Switch 2 で A ボタンの入力を確認）
  - ファームウェアの `PRESS` は押下と解放が同じ送信周期内で済んでしまい、Switch に届かない。PC 側で `HOLD` → 待機 → `RELEASE` として送る
  - 「持ちかた/順番を変える」画面を開くと、Switch 側から一度切断・再接続される
  - ファームウェアのスティック初期値が端（0）で、倒しっぱなしになる。接続直後に `STICK l_stick 0 0` / `STICK r_stick 0 0` で中央に戻す（ファームウェア側の修正は書き込み直し＝再ペアリングになるので保留）
  - 起動ごとに Bluetooth アドレスがランダムに変わるため、Pico の抜き挿し後は再ペアリングが必要

## Step 4. 画像認識の作成

- [x] テンプレート画像を切り出す → `tools/make_templates.py`（座標は `za_donut/vision.py` の `SPECS`）
  - パワー名の表示（「どうぐパワー：きのみ Lv.」「どっさりパワー：Lv.」と数字 2・3）
  - 各画面を見分けるための目印（HOME、ユーザー選択、タイトル、BACKUP、マップ、ドア・話しかけるの表示、きのみ選択、結果画面など）
  - きのみアイコン（指定用）は未作成。今はカーソル移動回数で選んでいる
- [x] テンプレートマッチングで画面状態を判定する関数を作る → `za_donut/vision.py`
- [x] パワー判定の精度を、手持ちのスクリーンショットで検証する
  - 「どっさりパワー：Lv.3」の実物はまだ見ていない（数字 3 はきのみ Lv.3 の行から流用）。数字を読めない行があるときは捨てずに停止する
  - 結果画面のパワー欄はピンクの帯から順に表示される。「A つぎへ」が出てから読む

## Step 5. 自動化ロジックの実装

- [ ] 設定ファイル（使用きのみ最大 7 つ、目標パワー）を読み込む（今はレシピ 1「きのみ Lv.3＋どっさり Lv.3」を固定で実装）
- [x] 画面状態を見ながら進むステートマシンを作る → `za_donut/runner.py`、実行は `tools/run.py`（1 回 約 69 秒）
  1. きのみを選択（テンプレートマッチでカーソル位置を確認）
  2. ドーナツを作成
  3. 結果画面でパワーを判定
  4. 外れ → リセットして 1 に戻る
  5. 当たり → アラート音を鳴らし、スクリーンショットを保存して停止
- [x] 想定外の画面になったときの復帰処理・タイムアウトを入れる（ソフトを終了してやり直し。3 回連続で失敗したら停止）
- [x] 試行回数・経過時間をログに出す → `logs/`、結果画面は `screenshots/results/`
- [ ] 当たり時のバックアップ（ポケモンセンターへ戻って話しかける）の自動化。今は結果画面で止まるので手動

## Step 6. 実機での長時間テスト

- [ ] 数十回連続で回して、誤判定・接続切れがないか確認する
- [ ] 必要に応じて待ち時間・判定のしきい値を調整する

---

## 未確定事項・リスク

- Pico W による Bluetooth プロコン模倣の安定性（PicoSwitchController はコミット 4 件の若いプロジェクト）
- Switch 2 での動作可否（使用本体は Switch 2。PicoSwitchController は Switch 2 対応を明記していない。ダメなら [switch2-pico](https://github.com/joshbirnholz/switch2-pico) を検討）
- キャプチャは OBS の仮想カメラ経由（`--device "OBS Virtual Camera"`）。OBS を閉じると取り込めなくなる
