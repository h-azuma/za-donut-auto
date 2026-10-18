"""Discord への報告と、ボタンでの返事の受け取り。

Bot は別スレッドで動かし、厳選ループからは ask() / send() を同期的に呼ぶ。
設定は config/discord.json（docs/discord-setup.md 参照）。
"""

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import Future, InvalidStateError
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import discord
from discord import app_commands

from . import paths

log = logging.getLogger(__name__)

CONFIG_PATH = paths.DISCORD

Images = Path | list[Path | None] | None  # 添付する画像（複数可。None は無視）

STYLES = {
    "primary": discord.ButtonStyle.primary,
    "danger": discord.ButtonStyle.danger,
    "secondary": discord.ButtonStyle.secondary,
}


class _Choices(discord.ui.View):
    """押されたボタンの key を future に入れる。押せるのは user_id の本人だけ、1 回だけ。"""

    def __init__(self, user_id: int, choices: list[tuple[str, str, str]], future: Future):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.future = future
        for key, label, style in choices:
            button = discord.ui.Button(label=label, style=STYLES[style])
            button.callback = self._callback(key, label)
            self.add_item(button)

    def _callback(self, key: str, label: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("このボタンは操作できません。", ephemeral=True)
                return
            if self.future.done():
                await interaction.response.send_message("すでに選択済みです。", ephemeral=True)
                return
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(content=f"{interaction.message.content}\n→ **{label}**", view=self)
            self.future.set_result(key)
            self.stop()
        return callback


class Discord:
    def __init__(self, token: str, channel_id: int, user_id: int):
        self.token = token
        self.channel_id = channel_id
        self.user_id = user_id
        self.client = discord.Client(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self.client)  # スラッシュコマンド（start() の前に登録する）
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self.stop_requested = threading.Event()  # /stop で立つ（厳選のループが区切りで見て止まる）
        self.client.event(self.on_ready)
        self._thread = threading.Thread(target=self._run, daemon=True)

    @classmethod
    def from_config(cls, path: Path = CONFIG_PATH) -> "Discord | None":
        if not path.exists():
            return None
        c = json.loads(path.read_text(encoding="utf-8"))
        return cls(c["token"], int(c["channel_id"]), int(c["user_id"]))

    async def on_ready(self) -> None:
        log.info(f"Discord に接続しました（{self.client.user}）")
        if self.tree.get_commands():
            # 報告先チャンネルのサーバーにだけ登録する（全体への登録は反映に時間がかかるため）
            channel = self.client.get_channel(self.channel_id) or await self.client.fetch_channel(self.channel_id)
            self.tree.copy_global_to(guild=channel.guild)
            try:
                await self.tree.sync(guild=channel.guild)
            except discord.Forbidden:
                log.info("スラッシュコマンドを登録できませんでした（Bot の招待時に applications.commands の権限が必要です）")
        self._ready.set()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.client.start(self.token))
        # close() の後片付けが start() の終了後に残るので、終わらせてからループを抜ける
        pending = asyncio.all_tasks(self.loop)
        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

    def start(self, timeout: float = 30) -> None:
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Discord に接続できませんでした。config/discord.json のトークンを確認してください。")

    def close(self) -> None:
        # close() が終わると start() も終わってループが止まるので、close() の完了ではなくスレッドの終了を待つ
        if self._thread.is_alive():
            asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)
            self._thread.join(10)

    def __enter__(self) -> "Discord":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    async def _send(self, text: str, image: Images, view: discord.ui.View | None) -> None:
        channel = self.client.get_channel(self.channel_id) or await self.client.fetch_channel(self.channel_id)
        kwargs = {"content": f"<@{self.user_id}> {text}"}
        paths = [image] if isinstance(image, Path) else [p for p in (image or []) if p]
        if paths:
            kwargs["files"] = [discord.File(str(p)) for p in paths]
        if view:
            kwargs["view"] = view
        await channel.send(**kwargs)

    def send(self, text: str, image: Images = None) -> None:
        asyncio.run_coroutine_threadsafe(self._send(text, image, None), self.loop).result(30)

    def ask(self, text: str, choices: list[tuple[str, str, str]], image: Images = None,
            timeout: float | None = None, default: str | None = None) -> str:
        """ボタン付きで送り、押されるまで待って key を返す。choices は (key, ラベル, primary/danger/secondary)。

        timeout 秒たっても押されなければ default を返す（その後のボタンは効かない）。
        待っている間に /stop されたら、choices に "stop" があれば "stop"、なければ default を返す。
        """
        future: Future = Future()

        async def post() -> None:
            await self._send(text, image, _Choices(self.user_id, choices, future))

        asyncio.run_coroutine_threadsafe(post(), self.loop).result(30)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            wait = 1.0 if deadline is None else max(0.0, min(1.0, deadline - time.monotonic()))
            try:
                return future.result(wait)
            except FutureTimeout:
                stopping = self.stop_requested.is_set()
                if not stopping and (deadline is None or time.monotonic() < deadline):
                    continue
            value = "stop" if stopping and any(key == "stop" for key, _, _ in choices) else default
            try:
                future.set_result(value)  # 以後に押されたボタンは「選択済み」になる
            except InvalidStateError:     # ちょうど押された
                pass
            return future.result()
