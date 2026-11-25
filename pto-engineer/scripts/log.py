"""История работы со скиллом: запросы, итоги, сводки чатов, пожелания, история файла «пришёл → замечания и правки → ушёл».

python log.py запрос "текст запроса"
python log.py итог "что сделано / почему не получилось"
python log.py чат "сводка этого чата: задача, файлы, результат, ошибки и правки"
python log.py пожелание "что работает не так и как надо: было так, хочу так"

История одного файла (работа «из файла сделать файл»: фото разметки → чертёж, журнал → акт):
python log.py файл-начало --вход ФАЙЛ [ФАЙЛ ...] [--текст "что за работа"]        → печатает номер истории
python log.py файл-шаг --id НОМЕР [--замечание "что не так"] [--правка "было → стало"] [--версия ФАЙЛ]
python log.py тройка [--id НОМЕР] [--вход ФАЙЛ ...] --итог ФАЙЛ [ФАЙЛ ...] [--правки "…"] [--текст "…"]   — каким ушёл
python log.py тройка --id НОМЕР --итог ФАЙЛ --сдано ФАЙЛ   — --сдано: что реально ушло заказчику (после AutoCAD и т. п.)

Пометки разметки по одной (для обучения чтению фото):
python log.py пометки --id НОМЕР --json ФАЙЛ_ИЛИ_ТЕКСТ   — список [{"номер": 1, "фото": "имя.jpg", "место": [x0, y0, x1, y1]
       (доли 0..1 от ширины и высоты фото), "тип": "трасса|стена|выноска|замер|снятие|прибор|перенос|номер|прочее",
       "прочтение": "что это по-твоему", "уверенность": "уверен|не уверен"}]
python log.py решение --id НОМЕР --номер N --решение "верно" | "иначе: …"   — ответ инженера по пометке
python log.py оценка --находка "текст находки проверки" --ответ согласен|ложно|неважно [--почему "…"] [--акт НОМЕР]

Операция правки (для отчёта «что делают руками чаще всего», ops_freq.py):
python log.py операция КОД [--раз N] [--id НОМЕР] [--файл ПУТЬ] [--текст "что именно"]
       КОД — перенос | копия | подпись | текст | трасса | линия | удаление | прибор | цвет | подложка | стена | слой |
       создание | длины_кж | спецификация | штамп | лист | прочее (своё слово — тоже можно, пиши коротко)

Копии входа, каждой промежуточной версии и итога кладутся на этот ПК: ~/.pto-skill/история/<номер>/
(файл потом меняется — копия остаётся какой была). Журнал — в подпапке пользователя в папке команды.
Наружу ничего не отправляется.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
from pathlib import Path

from common import OP_CODES, home, load_config, log_event, read_journal, engineer_dir, safe_name

MAX_COPY = 200 * 1024 * 1024          # файл крупнее — только путь, без копии


def hist_dir(hid: str) -> Path:
    return home() / "история" / safe_name(hid)


def _copy(hid: str, files: list[str], label: str) -> list[dict]:
    """Копии файлов в историю: NN_<метка>_<имя>. Возвращает [{путь, копия}] (копия пустая, если файла нет или он велик)."""
    d = hist_dir(hid)
    d.mkdir(parents=True, exist_ok=True)
    n = len(list(d.iterdir()))
    out = []
    for x in files:
        f = Path(x).expanduser().resolve()
        rec = {"путь": str(f), "копия": ""}
        if f.is_file() and f.stat().st_size <= MAX_COPY:
            dst = d / f"{n:02d}_{label}_{safe_name(f.name)}"
            shutil.copy2(f, dst)
            rec["копия"] = dst.name
            n += 1
        out.append(rec)
    return out


def _start_inputs(cfg: dict, hid: str) -> list[str]:
    for ev in read_journal(engineer_dir(cfg) / "журнал.jsonl"):
        if ev.get("действие") == "файл_начало" and ev.get("id") == hid:
            return [x["путь"] for x in ev.get("вход") or []]
    return []


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 2 and args[0] in ("запрос", "итог", "чат", "пожелание"):
        log_event(load_config(), args[0], текст=args[1][:20000 if args[0] == "чат" else 4000])
        return 0
    if args and args[0] in ("пометки", "решение", "оценка"):
        return marks(args)
    if args and args[0] == "операция":
        return operation(args[1:])
    if not args or args[0] not in ("файл-начало", "файл-шаг", "тройка"):
        print(__doc__)
        return 2
    cmd = args[0]
    ap = argparse.ArgumentParser(prog=f"log.py {cmd}")
    ap.add_argument("--id")
    ap.add_argument("--вход", dest="inp", nargs="+", default=[])
    ap.add_argument("--итог", dest="out", nargs="+", default=[])
    ap.add_argument("--версия", dest="ver")
    ap.add_argument("--замечание", dest="remark", default="")
    ap.add_argument("--правка", dest="fix", default="")
    ap.add_argument("--правки", dest="fixes", default="")
    ap.add_argument("--текст", dest="text", default="")
    ap.add_argument("--сдано", dest="done", nargs="+", default=[])
    a = ap.parse_args(args[1:])
    cfg = load_config()
    if cmd == "файл-начало":
        if not a.inp:
            print("нужен --вход"); return 2
        hid = a.id or _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        log_event(cfg, "файл_начало", id=hid, вход=_copy(hid, a.inp, "вход"), текст=a.text[:2000])
        print(f"история {hid}")
        return 0
    if cmd == "файл-шаг":
        if not a.id or not (a.remark or a.fix or a.ver):
            print("нужны --id и хотя бы одно из --замечание, --правка, --версия"); return 2
        ver = _copy(a.id, [a.ver], "версия") if a.ver else []
        log_event(cfg, "файл_шаг", id=a.id, замечание=a.remark[:4000], правка=a.fix[:4000], версия=ver)
        print("записано")
        return 0
    # тройка: каким файл ушёл (с историей — вход берётся из её начала)
    if not a.out:
        print("нужен --итог"); return 2
    inputs = a.inp or (_start_inputs(cfg, a.id) if a.id else [])
    if not inputs:
        print("нужен --вход (или --id истории)"); return 2
    hid = a.id or _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    if not a.id:
        _copy(hid, inputs, "вход")                       # без начала истории вход копируется здесь
    copies = _copy(hid, a.out, "итог")
    done = _copy(hid, a.done, "сдано") if a.done else []
    log_event(cfg, "тройка", id=hid, вход=[str(Path(x).expanduser().resolve()) for x in inputs],
              итог=[c["путь"] for c in copies], итог_копии=copies, сдано=[c["путь"] for c in done], сдано_копии=done,
              правки=a.fixes[:4000], текст=a.text[:2000])
    print("записано")
    return 0


def op_code(code: str) -> str:
    """Код операции: строчными, пробелы → «_», до 40 знаков; «длины КЖ» = «длины_кж»."""
    c = "_".join(str(code).strip().lower().split())[:40]
    return c or "прочее"


def operation(args: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="log.py операция")
    ap.add_argument("код")
    ap.add_argument("--раз", dest="times", type=int, default=1)
    ap.add_argument("--id", default="")
    ap.add_argument("--файл", dest="file", default="")
    ap.add_argument("--текст", dest="text", default="")
    a = ap.parse_args(args)
    if a.times < 1:
        print("--раз: целое ≥ 1"); return 2
    code = op_code(a.код)
    log_event(load_config(), "операция", код=code, раз=a.times, id=a.id, файл=Path(a.file).name if a.file else "",
              текст=a.text[:1000])
    print("записано" + ("" if code in OP_CODES else f" (новый код «{code}»)"))
    return 0


MARK_TYPES = {"трасса", "стена", "выноска", "замер", "снятие", "прибор", "перенос", "номер", "прочее"}


def marks(args: list[str]) -> int:
    import json
    cmd = args[0]
    ap = argparse.ArgumentParser(prog=f"log.py {cmd}")
    ap.add_argument("--id", default="")
    ap.add_argument("--json", dest="js", default="")
    ap.add_argument("--номер", dest="num", type=int)
    ap.add_argument("--решение", dest="decision", default="")
    ap.add_argument("--находка", dest="finding", default="")
    ap.add_argument("--ответ", dest="answer", choices=["согласен", "ложно", "неважно"])
    ap.add_argument("--почему", dest="why", default="")
    ap.add_argument("--акт", dest="act", default="")
    a = ap.parse_args(args[1:])
    cfg = load_config()
    if cmd == "пометки":
        if not a.id or not a.js:
            print("нужны --id и --json"); return 2
        src = Path(a.js)
        raw = src.read_text(encoding="utf-8") if len(a.js) < 260 and src.is_file() else a.js
        try:
            items = json.loads(raw)
        except ValueError as exc:
            print(f"не JSON: {exc}"); return 2
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
            print("нужен список объектов"); return 2
        clean = []
        for i, x in enumerate(items, 1):
            place = x.get("место")
            ok_place = isinstance(place, list) and len(place) == 4 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in place)
            clean.append({"номер": x.get("номер", i), "фото": str(x.get("фото", ""))[:200], "место": place if ok_place else None,
                          "тип": x.get("тип") if x.get("тип") in MARK_TYPES else "прочее",
                          "прочтение": str(x.get("прочтение", ""))[:500], "уверенность": str(x.get("уверенность", ""))[:20]})
        log_event(cfg, "пометки", id=a.id, пометки=clean)
        bad = sum(1 for c in clean if c["место"] is None)
        print(f"записано пометок: {len(clean)}" + (f", без места на фото: {bad}" if bad else ""))
        return 0
    if cmd == "решение":
        if not a.id or a.num is None or not a.decision:
            print("нужны --id, --номер, --решение"); return 2
        log_event(cfg, "решение", id=a.id, номер=a.num, решение=a.decision[:1000])
        print("записано")
        return 0
    if not a.finding or not a.answer:
        print("нужны --находка и --ответ"); return 2
    log_event(cfg, "оценка", находка=a.finding[:1000], ответ=a.answer, почему=a.why[:1000], акт=a.act)
    print("записано")
    return 0


if __name__ == "__main__":
    sys.exit(main())
