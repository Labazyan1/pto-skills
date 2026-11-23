"""История работы со скиллом: запросы, итоги, сводки чатов, тройки «вход → итог → правки».

python log.py запрос "текст запроса"
python log.py итог "что сделано / почему не получилось"
python log.py чат "сводка этого чата: задача, файлы, результат, ошибки и правки"
python log.py пожелание "что работает не так и как надо: было так, хочу так"
python log.py тройка --вход ФАЙЛ [ФАЙЛ ...] --итог ФАЙЛ [ФАЙЛ ...] [--правки "что поправил пользователь"] [--текст "что за работа"]
Пишется в журнал пользователя в его подпапке папки команды. Наружу ничего не отправляется.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import load_config, log_event


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 2 and args[0] in ("запрос", "итог", "чат", "пожелание"):
        log_event(load_config(), args[0], текст=args[1][:20000 if args[0] == "чат" else 4000])
        return 0
    if args and args[0] == "тройка":
        ap = argparse.ArgumentParser(prog="log.py тройка")
        ap.add_argument("--вход", nargs="+", required=True)
        ap.add_argument("--итог", nargs="+", required=True)
        ap.add_argument("--правки", default="")
        ap.add_argument("--текст", default="")
        a = ap.parse_args(args[1:])

        def absp(xs):
            return [str(Path(x).expanduser().resolve()) for x in xs]

        log_event(load_config(), "тройка", вход=absp(a.вход), итог=absp(a.итог), правки=a.правки[:4000], текст=a.текст[:2000])
        print("записано")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
