"""Замечания технадзора и заказчика по ИД: записать, ответить, снять; проверка перед сдачей.

python remarks.py add --от технадзор --текст "нет ИС к АОСР-12" [--акт АОСР-12] [--захватка "4 этаж"] [--система СПС] [--дата 2026-11-20]
python remarks.py answer 3 --текст "ИС приложена, акт пересобран"   — ответ дан (ждём, примет ли технадзор)
python remarks.py close 3 [--текст "принято на обходе"]              — замечание снято
python remarks.py list [--все]                                       — открытые (или все) замечания свои
python remarks.py before --acts "АОСР-1,АОСР-2"                      — перед сдачей: какие из этих актов с открытыми замечаниями
Хранится в <команда>/<инженер>/замечания.json; начальник видит открытые замечания на доске и в здании.
"""
from __future__ import annotations

import argparse
import sys

from common import engineer_dir, is_open, load_config, load_remarks, log_event, now, save_remarks

SOURCES = ("технадзор", "заказчик", "генподрядчик", "авторский надзор", "другое")


def find(remarks: list[dict], rid: int) -> dict | None:
    return next((r for r in remarks if r.get("id") == rid), None)


def line(r: dict) -> str:
    where = " · ".join(x for x in (r.get("акт"), r.get("захватка"), r.get("система")) if x)
    ans = f" → ответ: {r['ответ']}" if r.get("ответ") else ""
    return f"#{r['id']} [{r.get('статус')}] {r.get('от')}, {r.get('дата') or r.get('создано', '')[:10]}" \
           f"{' · ' + where if where else ''}: {r.get('текст')}{ans}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "answer", "close", "list", "before"])
    ap.add_argument("id", nargs="?", type=int)
    ap.add_argument("--от", dest="src", default="технадзор")
    ap.add_argument("--текст", dest="text", default="")
    ap.add_argument("--акт", dest="act", default="")
    ap.add_argument("--захватка", dest="zone", default="")
    ap.add_argument("--система", dest="system", default="")
    ap.add_argument("--дата", dest="date", default="")
    ap.add_argument("--acts", default="")
    ap.add_argument("--все", dest="all", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_config()
    folder = engineer_dir(cfg)
    remarks = load_remarks(folder)

    if a.cmd == "add":
        if not a.text.strip():
            print("нужен --текст замечания"); return 2
        src = a.src if a.src in SOURCES else "другое"
        r = {"id": max((x.get("id", 0) for x in remarks), default=0) + 1, "от": src, "текст": a.text.strip()[:2000],
             "акт": a.act.strip(), "захватка": a.zone.strip(), "система": a.system.strip(), "дата": a.date.strip(),
             "статус": "открыто", "создано": now(), "ответ": ""}
        remarks.append(r)
        save_remarks(folder, remarks)
        log_event(cfg, "замечание", шаг="добавлено", id=r["id"], от=src, акт=r["акт"])
        print("записано: " + line(r))
        return 0
    if a.cmd in ("answer", "close"):
        r = find(remarks, a.id) if a.id else None
        if not r:
            print("нет замечания с таким номером: python remarks.py list --все"); return 2
        if a.cmd == "answer":
            if not a.text.strip():
                print("нужен --текст ответа"); return 2
            r.update(статус="ответ_дан", ответ=a.text.strip()[:2000], ответ_дан=now())
        else:
            r.update(статус="снято", снято=now())
            if a.text.strip():
                r["ответ"] = (r.get("ответ") + " / " if r.get("ответ") else "") + a.text.strip()[:500]
        save_remarks(folder, remarks)
        log_event(cfg, "замечание", шаг=r["статус"], id=r["id"], акт=r.get("акт", ""))
        print(line(r))
        return 0
    if a.cmd == "list":
        shown = [r for r in remarks if a.all or is_open(r)]
        for r in shown:
            print(line(r))
        print(f"{'всего' if a.all else 'открытых'}: {len(shown)}")
        return 0
    wanted = {x.strip().lower() for x in a.acts.split(",") if x.strip()}
    hits = [r for r in remarks if is_open(r) and (not wanted or str(r.get("акт", "")).lower() in wanted)]
    if not hits:
        print("Открытых замечаний по этим актам нет — можно сдавать.")
        return 0
    print("Перед сдачей закройте или ответьте на замечания:")
    for r in hits:
        print("- " + line(r))
    return 1


if __name__ == "__main__":
    sys.exit(main())
