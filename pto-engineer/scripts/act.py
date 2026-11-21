"""Сохранить акт и записать в журнал.

python act.py save акт.json          — новый акт (JSON по полям 344/пр, см. SKILL.md) → акты/<номер>.json
python act.py save акт.json --fix    — исправление: сохраняет и пишет в журнал «было → стало» по полям
python act.py missing "АООК на ..."  — формы/работы, которые скилл не умеет (нам в бэклог)
python act.py error "что сломалось"  — сбой
Номер акта берётся из поля «номер».
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import engineer_dir, load_config, log_event, safe_name


def diff(old: dict, new: dict) -> dict:
    keys = [k for k in dict.fromkeys([*old, *new]) if not k.startswith("_")]
    return {k: {"было": old.get(k), "стало": new.get(k)} for k in keys if old.get(k) != new.get(k)}


def save(path: str, fix: bool) -> int:
    cfg = load_config()
    act = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(act, dict) or not str(act.get("номер", "")).strip():
        print("В акте нет поля «номер»"); return 2
    d = engineer_dir(cfg) / "акты"
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{safe_name(act['номер'])}.json"
    old = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
    if old is not None and not fix:
        print(f"Акт {act['номер']} уже есть: для правки добавьте --fix"); return 3
    target.write_text(json.dumps(act, ensure_ascii=False, indent=2), encoding="utf-8")
    if old is None:
        log_event(cfg, "создан", акт=act["номер"], данные=act)
    else:
        ch = diff(old, act)
        log_event(cfg, "исправлен", акт=act["номер"], правки=ch)
        print(f"изменено полей: {len(ch)}")
    print(f"сохранено: {target}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["save", "missing", "error"])
    ap.add_argument("arg")
    ap.add_argument("--fix", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "save":
        return save(a.arg, a.fix)
    cfg = load_config()
    log_event(cfg, "нет_формы" if a.cmd == "missing" else "сбой", заметка=a.arg[:2000])
    print("записано")
    return 0


if __name__ == "__main__":
    sys.exit(main())
