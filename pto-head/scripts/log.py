"""История работы со скиллом: запросы, итоги, сводки чатов, пожелания, история файла «пришёл → замечания и правки → ушёл».

python log.py запрос "текст запроса"
python log.py итог "что сделано / почему не получилось"
python log.py чат "сводка этого чата: задача, файлы, результат, ошибки и правки"
python log.py пожелание "что работает не так и как надо: было так, хочу так"

История одного файла (работа «из файла сделать файл»: фото разметки → чертёж, журнал → акт):
python log.py файл-начало --вход ФАЙЛ [ФАЙЛ ...] [--текст "что за работа"]        → печатает номер истории
python log.py файл-шаг --id НОМЕР [--замечание "что не так"] [--правка "было → стало"] [--версия ФАЙЛ]
python log.py тройка [--id НОМЕР] [--вход ФАЙЛ ...] --итог ФАЙЛ [ФАЙЛ ...] [--правки "…"] [--текст "…"]   — каким ушёл
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

from common import home, load_config, log_event, read_journal, engineer_dir, safe_name

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
    log_event(cfg, "тройка", id=hid, вход=[str(Path(x).expanduser().resolve()) for x in inputs],
              итог=[c["путь"] for c in copies], итог_копии=copies, правки=a.fixes[:4000], текст=a.text[:2000])
    print("записано")
    return 0


if __name__ == "__main__":
    sys.exit(main())
