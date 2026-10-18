"""ドーナツ厳選を自動で繰り返す。

当たりが出たら Discord に結果画面を送り、そのままポケモンセンターで回復してバックアップする。
読めないパワーがあって判定できないときは、Discord で「バックアップする / 捨てて続ける / 止める」を聞く。
バックアップが終わったら「続ける / きのみがなくなるまで続ける / 止める」をボタンで聞く。
「きのみがなくなるまで続ける」を選ぶと以後は聞かず、きのみを選ぶときに足りないと分かったところで止まる。
バックアップ完了の通知には、使ったきのみの残り個数が写った画像（選んだときの一覧の行）を添付する。
config/discord.json が無いとき（または --no-discord）は、結果画面のままアラーム音を鳴らして停止する。
Discord の /stop で止めると、今の操作をその場で中断し、ソフトを終了 → バックアップから再開 → ポケモンセンターで
レポートを書く → 本体をスリープ、の順に行ってから Discord に報告して終了する（当たりのバックアップ中なら終わってから）。

通常は手元に何も残さない（ログは画面に出すだけ。Discord に送った画面は送った後で消す）。
--debug を付けると、logs/ にログファイル（所要時間などの詳細も）を、screenshots/ に画面を残す。

使い方:
    python tools/run.py --device "OBS Virtual Camera" [--max 100]

開始前の状態: Switch の HOME 画面で ZA にカーソルが合っていること（ZA は起動中でも可）。
停止: Discord の /stop（バックアップから再開してレポートを書き、本体をスリープさせて終了）、または Ctrl+C
"""

import argparse
import logging
import sys
import time
import winsound
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from za_donut.notify import Discord  # noqa: E402
from za_donut.pico import Pico  # noqa: E402
from za_donut.berries import RecipeStore  # noqa: E402
from za_donut.berries import register_commands as register_recipe_commands  # noqa: E402
from za_donut.runner import ERROR_DIR, OutOfBerries, Runner, StepFailed, StopRequested  # noqa: E402
from za_donut.screen import DEFAULT_DEVICE, Screen  # noqa: E402
from za_donut.target import TargetStore, register_commands  # noqa: E402

LOG_DIR = ROOT / "logs"
MAX_CONSECUTIVE_FAILURES = 3
CUTSCENE_ASK_MINUTES = 10

log = logging.getLogger("run")


def alarm(message: str) -> None:
    """Ctrl+C されるまで鳴らし続ける。"""
    print(message)
    try:
        while True:
            for _ in range(3):
                winsound.Beep(1200, 300)
                time.sleep(0.1)
            time.sleep(3)
    except KeyboardInterrupt:
        pass


def register_stop_command(discord: Discord) -> None:
    """Discord に /stop を登録する（押せるのは discord.json の本人だけ）。"""
    from discord import Interaction

    @discord.tree.command(name="stop", description="今の操作を中断し、バックアップから再開してレポートを書き、本体をスリープさせて終了する")
    async def stop(interaction: Interaction) -> None:
        if interaction.user.id != discord.user_id:
            await interaction.response.send_message("このコマンドは使えません。", ephemeral=True)
            return
        if discord.stop_requested.is_set():
            await interaction.response.send_message("すでに停止を受け付けています。", ephemeral=True)
            return
        discord.stop_requested.set()
        log.info("Discord から停止を頼まれました")
        await interaction.response.send_message(
            "停止を受け付けました。今の操作を中断して、ソフトを終了 → バックアップから再開 → レポート → スリープ の順に行って終了します。"
            "（当たりのバックアップ中なら、終わってから止めます）")


def stop_by_user(runner: Runner, discord: Discord, tries: int, started: float) -> None:
    """/stop の後始末をして、何をしたかを Discord に報告する（この後プログラムは終了する）。"""
    log.info("Discord の /stop で中断しました。ソフトを終了してバックアップから再開し、レポートを書きます")
    discord.send("中断しました。ソフトを終了 → バックアップから再開 → レポート → スリープ を行います。")
    path, error, slept = runner.restore_and_sleep()
    lines = [
        "**自動実行を停止しました**（/stop）",
        f"- 試行: {tries} 回（{(time.monotonic() - started) / 60:.0f} 分）",
        "- ゲーム: ソフトを終了してバックアップから再開し、ポケモンセンターでレポートを書きました" if not error else
        f"- ゲーム: **レポートを書けませんでした**（{error}）。起こしたら画面を確認してください",
        "- 本体: スリープしました" if slept else "- 本体: **スリープを確認できませんでした**（画面が消えませんでした）",
    ]
    message = "\n".join(lines)
    log.info(message)
    send_images = [path] if path else []
    discord.send(message, send_images)
    runner.discard(*send_images)


def loop(runner: Runner, discord: Discord | None, max_tries: int, use_alarm: bool, until_out: bool) -> None:
    started = time.monotonic()
    tries = failures = 0
    # until_out: 「きのみがなくなるまで続ける」。True ならバックアップ後に聞かず、きのみが足りなくなったら止まる
    # 直前の当たりのときのきのみの残り個数（次の当たりの答え合わせ用）。再起動しても忘れないようにファイルにも残す
    def send(text: str, images: list[Path | None]) -> None:
        """Discord に送り、送った画面は（デバッグモードでなければ）消す。"""
        discord.send(text, images)
        runner.discard(*images)

    def stop(message: str, images: list[Path | None] | None = None) -> None:
        log.info(message)
        if discord:
            send(message, images or [])
        else:
            runner.discard(*(images or []))
            if use_alarm:
                alarm(f"{message} Ctrl+C でアラームを止めます。")

    def on_cutscene() -> bool:
        """昼夜の切り替わりの演出の後、バックアップを取り直すか聞く。返事がなければ取り直さない。"""
        if not discord:
            return False
        choice = discord.ask(
            "昼夜の切り替わりの演出が入りました（ドーナツを作る前）。\n"
            "今の時刻でバックアップを取り直すと、次の試行から同じ演出に当たらなくなります。取り直しますか？\n"
            f"（{CUTSCENE_ASK_MINUTES} 分以内に返事がなければ、取り直さずに続けます）",
            [("refresh", "取り直す", "primary"), ("skip", "取り直さない", "secondary")],
            timeout=CUTSCENE_ASK_MINUTES * 60, default="skip",
        )
        log.info(f"Discord の返事（演出後のバックアップ）: {choice}")
        return choice == "refresh"

    try:
        while not max_tries or tries < max_tries:
            runner.check_stop()
            tries += 1
            t0 = time.monotonic()
            try:
                result = runner.attempt(on_cutscene)
            except OutOfBerries as e:
                stop(f"きのみが足りないので止めます: {e}")
                return
            except StepFailed as e:
                failures += 1
                path = runner.save(ERROR_DIR, f"try{tries}")
                log.info(f"#{tries} 失敗: {e} 連続 {failures} 回")
                log.debug(f"失敗したときの画面: {path}")
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    stop(f"連続で {failures} 回失敗したため停止しました。最後の失敗: {e}", [path])
                    return
                runner.discard(path)
                continue
            failures = 0
            elapsed = time.monotonic() - started
            summary = result.summary()
            log.info(
                f"#{tries} {summary} → {'当たり' if result.hit else '外れ'}"
                f"（{time.monotonic() - t0:.0f} 秒、累計 {elapsed / 60:.1f} 分）"
            )
            if not (result.hit or result.uncertain):
                continue

            kind = "当たり" if result.hit else "当たりかもしれない結果（読めないパワーあり）"
            log.info(f"{kind}が出ました。結果画面のままにしてあります。")
            if not discord:
                runner.discard(runner.last_result_path)
                if use_alarm:
                    alarm(f"{kind}！ 画面を確認してください。Ctrl+C でアラームを止めます。")
                return

            if result.hit:
                # 当たりを捨てることはないので、確認せずにそのままバックアップする
                send(f"**{kind}**！ {summary}（{tries} 回目、{elapsed / 60:.0f} 分）\nバックアップを取りに行きます。",
                     [runner.last_result_path])
            else:
                # 判定できないときは人が決める。外れをバックアップすると、そのドーナツの分のきのみが本当に減ってしまう
                choice = discord.ask(
                    f"**判定できない結果です**: {summary}（{tries} 回目）\n"
                    "読めないパワーがあり、当たりかどうか判定できませんでした。画面を見て選んでください。\n"
                    "（バックアップすると、このドーナツの分のきのみが減ります）",
                    [("backup", "バックアップする", "primary"), ("discard", "捨てて続ける", "secondary"),
                     ("stop", "止める", "danger")],
                    [runner.last_result_path],
                )
                runner.discard(runner.last_result_path)
                log.info(f"Discord の返事（判定できない結果）: {choice}")
                runner.check_stop()  # 返事を待っている間に /stop された（この結果は捨てる）
                if choice == "stop":
                    return
                if choice == "discard":
                    continue

            # きのみの残り個数は、選んだときの一覧の行を画像で送る（OCR の数字は当てにしない）
            berries_image = runner.save_berries()
            try:
                done = runner.backup()
            except StepFailed as e:
                # 途中の状態が分からないので、自動ではやり直さない
                runner.discard(berries_image)
                with runner.no_stop():  # 当たりが残っている可能性があるので、/stop でもリセットしない
                    path = runner.save(ERROR_DIR, "backup")
                stop(f"バックアップの途中で失敗したため停止しました: {e}\n画面を確認して、手動で続きを行ってください。",
                     [path])
                return
            log.info("バックアップしました。")
            runner.discard(done)
            images = [berries_image]  # 画像はきのみの残り個数（このドーナツを作った後の数）
            # 個数の上限がある狙いは 1 つ減らし、そろったら外す
            changes = runner.targets.consume(result.powers) if result.hit else []
            for change in changes:
                log.info(change)
            status = "".join(f"\n{c}" for c in changes)
            if runner.targets.empty():
                stop(f"バックアップしました。{status}\n狙いがすべてそろったので止めます。", images)
                return
            if discord.stop_requested.is_set():
                send(f"バックアップしました。{status}", images)
                continue  # 次の周の頭で止まる
            if until_out:
                # きのみが足りなくなったら、次の試行できのみを選ぶときに分かって止まる
                send(f"バックアップしました。{status}\nきのみがなくなるまで続けます。", images)
                continue
            choice = discord.ask(
                f"ポケモンセンターで回復して、バックアップしました。{status}\n厳選を続けますか？",
                [("continue", "続ける", "primary"), ("until_out", "きのみがなくなるまで続ける", "primary"),
                 ("stop", "止める", "danger")],
                images,
            )
            runner.discard(*images)
            log.info(f"Discord の返事: {choice}")
            runner.check_stop()  # 返事を待っている間に /stop された
            if choice == "stop":
                return
            until_out = until_out or choice == "until_out"
    except StopRequested:
        stop_by_user(runner, discord, tries, started)
        return
    log.info(f"{tries} 回試行して終了しました。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default=DEFAULT_DEVICE, help='OBS 経由なら "OBS Virtual Camera"')
    parser.add_argument("--max", type=int, default=0, help="最大試行回数（0 は無制限）")
    parser.add_argument("--no-alarm", action="store_true")
    parser.add_argument("--no-discord", action="store_true", help="config/discord.json があっても Discord を使わない")
    parser.add_argument("--until-out", action="store_true",
                        help="最初から「きのみがなくなるまで続ける」で動かす（バックアップ後に聞かない）")
    parser.add_argument("--debug", action="store_true",
                        help="logs/ にログファイルを残し（所要時間などの詳細も）、画面を screenshots/ に残す")
    args = parser.parse_args()

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers[0].setLevel(logging.INFO)
    if args.debug:
        LOG_DIR.mkdir(exist_ok=True)
        handlers.append(logging.FileHandler(LOG_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.log", encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(message)s", handlers=handlers)
    logging.getLogger("discord").setLevel(logging.ERROR)  # 音声機能が無いという警告などは出さない

    targets = TargetStore()
    recipes = RecipeStore()
    log.info(f"レシピ: {recipes.describe()}")
    log.info(f"狙い: {targets.describe()}")
    discord = None if args.no_discord else Discord.from_config()
    if discord:
        register_commands(discord, targets)
        register_recipe_commands(discord, recipes)
        register_stop_command(discord)
        discord.start()
    try:
        with Screen(args.device) as screen, Pico() as pico:
            stop_requested = discord.stop_requested.is_set if discord else (lambda: False)
            runner = Runner(pico, screen, targets, recipes, keep_screenshots=args.debug, stop_requested=stop_requested)
            loop(runner, discord, args.max, not args.no_alarm, args.until_out)
    except Exception as e:
        if discord:
            discord.send(f"エラーで停止しました: {e!r}")
        raise
    finally:
        if discord:
            discord.close()


if __name__ == "__main__":
    main()
