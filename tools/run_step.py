"""za_donut/steps.py のステップを 1 つずつ実行し、途中の画面を保存する（秒数の調整用）。

使い方:
    python tools/run_step.py launch
    python tools/run_step.py --list
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from za_donut.pico import Pico  # noqa: E402
from za_donut.screen import DEFAULT_DEVICE, Screen  # noqa: E402
from za_donut.steps import STEPS  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "screenshots" / "steps"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("step", nargs="?", choices=list(STEPS))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help='OBS 経由なら "OBS Virtual Camera"')
    args = parser.parse_args()

    if args.list or not args.step:
        print("\n".join(STEPS))
        return

    with Screen(args.device) as screen, Pico() as pico:
        def shot(name: str) -> None:
            print(f"保存: {screen.save(OUT_DIR, f'{args.step}_{name}')}")

        shot("start")
        STEPS[args.step](pico, shot)


if __name__ == "__main__":
    main()
