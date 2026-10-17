"""ドーナツ厳選を自動で繰り返す。

当たりが出たら Discord に結果画面を送り、そのままポケモンセンターで回復してバックアップする。
読めないパワーがあって判定できないときは、Discord で「バックアップする / 捨てて続ける / 止める」を聞く。
バックアップが終わったら「続ける / きのみがなくなるまで続ける / 止める」をボタンで聞く。
「きのみがなくなるまで続ける」を選ぶと以後は聞かず、レシピのきのみが 1 個分を下回ったところで止まる。
config/discord.json が無いとき（または --no-discord）は、結果画面のままアラーム音を鳴らして停止する。

通常は手元に何も残さない（ログは画面に出すだけ。Discord に送った画面は送った後で消す）。
--debug を付けると、logs/ にログファイル（所要時間などの詳細も）を、screenshots/ に画面を残す。

使い方:
    python tools/run.py --device "OBS Virtual Camera" [--max 100]

開始前の状態: Switch の HOME 画面で ZA にカーソルが合っていること（ZA は起動中でも可）。
停止: Ctrl+C
"""

import argparse
import json
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
from za_donut import paths  # noqa: E402
from za_donut.berries import RecipeStore  # noqa: E402
from za_donut.berries import register_commands as register_recipe_commands  # noqa: E402
from za_donut.runner import ERROR_DIR, OutOfBerries, Runner, StepFailed  # noqa: E402
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


def berries_left(berries: dict[str, int | None], recipe: dict[str, int]) -> tuple[int | None, str]:
    """レシピのきのみの残り個数から、あと何個作れるかと、通知用の文を返す（読めない数があれば None）。"""
    parts = [f"{name} {'?' if n is None else n} 個" for name, n in berries.items()]
    if any(n is None for n in berries.values()):
        return None, f"きのみの残り: {' / '.join(parts)}（読み取れない数がありました）"
    left = min(berries[name] // need for name, need in recipe.items())
    return left, f"きのみの残り: {' / '.join(parts)}（あと {left} 個作れます）"


def loop(runner: Runner, discord: Discord | None, max_tries: int, use_alarm: bool, until_out: bool) -> None:
    started = time.monotonic()
    tries = failures = 0
    # until_out: 「きのみがなくなるまで続ける」。True ならバックアップ後に聞かず、きのみが足りなくなったら止まる
    # 直前の当たりのときのきのみの残り個数（次の当たりの答え合わせ用）。再起動しても忘れないようにファイルにも残す
    counts_path = paths.BERRY_COUNTS
    last_berries: dict[str, int] = json.loads(counts_path.read_text(encoding="utf-8")) if counts_path.exists() else {}

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

    while not max_tries or tries < max_tries:
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
            if choice == "stop":
                return
            if choice == "discard":
                continue

        recipe = runner.last_recipe
        expected = {name: last_berries[name] - need for name, need in recipe.items() if name in last_berries}
        berries, fixed = runner.read_berries(expected)
        last_berries = {**last_berries, **{name: n for name, n in berries.items() if n is not None}}
        paths.write_state(counts_path, json.dumps(last_berries, ensure_ascii=False) + "\n")
        left, berry_text = berries_left(berries, recipe)
        if fixed:
            berry_text += f"\n（{'・'.join(fixed)} は読み取りが合わなかったため、前回の個数から計算しました）"
        unread = left is None
        log.info(berry_text)
        try:
            done = runner.backup()
        except StepFailed as e:
            # 途中の状態が分からないので、自動ではやり直さない
            stop(f"バックアップの途中で失敗したため停止しました: {e}\n画面を確認して、手動で続きを行ってください。",
                 [runner.save(ERROR_DIR, "backup")])
            return
        log.info("バックアップしました。")
        images = [done]
        if left == 0:
            stop(f"バックアップしました。{berry_text}\nきのみが足りないので止めます。", images)
            return
        if until_out and not unread:
            send(f"バックアップしました。{berry_text}\nきのみがなくなるまで続けます。", images)
            continue
        choice = discord.ask(
            f"ポケモンセンターで回復して、バックアップしました。{berry_text}\n厳選を続けますか？",
            [("continue", "続ける", "primary"), ("until_out", "きのみがなくなるまで続ける", "primary"),
             ("stop", "止める", "danger")],
            images,
        )
        runner.discard(*images)
        log.info(f"Discord の返事: {choice}")
        if choice == "stop":
            return
        until_out = until_out or choice == "until_out"
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
        discord.start()
    try:
        with Screen(args.device) as screen, Pico() as pico:
            loop(Runner(pico, screen, targets, recipes, keep_screenshots=args.debug), discord, args.max, not args.no_alarm, args.until_out)
    except Exception as e:
        if discord:
            discord.send(f"エラーで停止しました: {e!r}")
        raise
    finally:
        if discord:
            discord.close()


if __name__ == "__main__":
    main()
