# Discord で当たりを報告し、ボタンでバックアップを指示する

当たりが出ると、結果画面のスクショを Discord に送り「バックアップする / 捨てて続ける / 止める」のボタンで返事を待ちます。
Bot は PC から Discord へ接続しに行くので、ルーターのポート開放などは不要です。

## 1. Bot を作る

1. https://discord.com/developers/applications を開き、「New Application」で作成（名前は自由）。
2. 左の「Bot」→「Reset Token」でトークンを表示してコピーする。**トークンはパスワードと同じなので、人に見せたりチャットに貼ったりしないこと。**
3. 同じ画面の Privileged Gateway Intents はすべてオフのままでよい。

## 2. 自分のサーバーに招待する

1. 左の「OAuth2」→「OAuth2 URL Generator」で、Scopes の `bot` にチェック。
2. 下に出る Bot Permissions で `View Channels`・`Send Messages`・`Attach Files` にチェック。
3. 一番下の URL をブラウザで開き、報告を受け取りたいサーバー（自分専用のサーバー推奨）に追加する。

## 3. チャンネル ID と自分のユーザー ID を調べる

1. Discord の「ユーザー設定」→「詳細設定」→「開発者モード」をオン。
2. 報告を受け取るチャンネルを右クリック →「チャンネル ID をコピー」。
3. 自分のアイコンを右クリック →「ユーザー ID をコピー」。

## 4. 設定ファイルを作る

`config/discord.example.json` をコピーして `config/discord.json` を作り、3 つの値を書き換える（`config/discord.json` は .gitignore 済み）。

```json
{
  "token": "コピーしたトークン",
  "channel_id": "123456789012345678",
  "user_id": "123456789012345678"
}
```

## 5. 動作確認

```
python tools/discord_test.py
```

チャンネルにボタン付きのテストメッセージが届き、押すとターミナルに結果が表示されれば完了です。
ボタンは `user_id` の本人しか押せず、1 つのメッセージで 1 回しか効きません。
