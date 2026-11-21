"""История запросов к скиллу: что просил инженер и чем закончилось.

python log.py запрос "текст запроса инженера"
python log.py итог "что сделано / почему не получилось"
Пишется в журнал инженера в папке команды: начальник видит, что просили.
"""
from __future__ import annotations

import sys

from common import load_config, log_event


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in ("запрос", "итог"):
        print(__doc__); return 2
    log_event(load_config(), args[0], текст=args[1][:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
