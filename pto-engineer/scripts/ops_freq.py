"""Что команда делает руками чаще всего: частоты операций по журналам всех инженеров (запись — log.py операция).

python ops_freq.py [--топ 10] [--дней 30] [--команда ПАПКА]

Порядок — по числу дней, когда операция понадобилась (ежедневная правка выше разовой массовой), затем по числу раз.
Отметка «скрипт есть» — операция уже делается программой без модели (подписи, трассы, длины КЖ…); остальное —
кандидаты на новый скрипт. Плюс пожелания и правки по истории файла — их тоже видно в отчёте. Наружу ничего не уходит.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from collections import Counter, defaultdict
from pathlib import Path

from common import load_config, read_journal

SCRIPTED = {"подпись", "трасса", "длины_кж", "перенос", "копия", "удаление", "текст", "линия"}   # есть программа платформы


def collect(team: Path, days: int | None = None) -> dict:
    since = (_dt.datetime.now().astimezone() - _dt.timedelta(days=days)).isoformat() if days else ""
    times: Counter = Counter()
    day_set: dict[str, set] = defaultdict(set)
    who: dict[str, set] = defaultdict(set)
    other = Counter()
    for d in sorted(p for p in team.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))):
        for ev in read_journal(d / "журнал.jsonl"):
            t = str(ev.get("время", ""))
            if since and t < since:
                continue
            act = ev.get("действие")
            if act == "операция":
                code = str(ev.get("код") or "прочее")
                n = ev.get("раз") if isinstance(ev.get("раз"), int) and ev.get("раз") > 0 else 1
                times[code] += n
                day_set[code].add(t[:10])
                who[code].add(str(ev.get("инженер") or d.name))
            elif act in ("пожелание", "файл_шаг", "оценка"):
                other[act] += 1
    rows = [(c, times[c], len(day_set[c]), len(who[c]), c in SCRIPTED) for c in times]
    rows.sort(key=lambda r: (-r[2], -r[1], r[0]))
    return {"rows": rows, "other": dict(other)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ops_freq.py")
    ap.add_argument("--топ", dest="top", type=int, default=10)
    ap.add_argument("--дней", dest="days", type=int)
    ap.add_argument("--команда", dest="team")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    team = Path(a.team) if a.team else Path(load_config()["team_folder"])
    res = collect(team, a.days)
    if not res["rows"]:
        print("операций в журналах нет (запись: log.py операция КОД)")
    else:
        print(f"{'операция':<16}{'раз':>6}{'дней':>6}{'людей':>7}  скрипт")
        for code, n, dd, ww, has in res["rows"][: a.top]:
            print(f"{code:<16}{n:>6}{dd:>6}{ww:>7}  {'есть' if has else 'нет — кандидат'}")
    o = res["other"]
    if o:
        print(f"пожеланий {o.get('пожелание', 0)}, правок по истории файла {o.get('файл_шаг', 0)}, "
              f"оценок находок {o.get('оценка', 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
