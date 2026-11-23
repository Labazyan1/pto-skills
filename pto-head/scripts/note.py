"""Заметки разметки: ответы инженера, практика объекта, привычки мастера. Читать перед вопросом, дописывать после.

python note.py list                         — какие заметки есть
python note.py show ИМЯ                     — прочитать (например, «разметка_объект» или «разметка_7 этаж СПС»)
python note.py add ИМЯ "текст"              — дописать строку с датой (раздел: --раздел «Ответы»)
Лежат на этом ПК: ~/.pto-skill/заметки/<объект>/<ИМЯ>.md (попадают в выгрузку). Наружу ничего не отправляется.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import home, load_config, log_event, now, safe_name


def notes_dir(cfg: dict) -> Path:
    obj = Path(cfg.get("team_folder") or "без_объекта").name
    return home() / "заметки" / safe_name(obj)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "show", "add"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--раздел", dest="section", default="")
    a = ap.parse_args(argv)
    cfg = load_config()
    d = notes_dir(cfg)
    if a.cmd == "list":
        files = sorted(d.glob("*.md")) if d.is_dir() else []
        print("\n".join(f.stem for f in files) or "Заметок пока нет.")
        return 0
    if not a.name:
        print("нужно имя заметки"); return 2
    f = d / (safe_name(a.name.removesuffix(".md")) + ".md")
    if a.cmd == "show":
        print(f.read_text(encoding="utf-8") if f.exists() else f"Заметки «{a.name}» нет.")
        return 0
    if not a.text.strip():
        print("нужен текст"); return 2
    d.mkdir(parents=True, exist_ok=True)
    new = not f.exists()
    with open(f, "a", encoding="utf-8") as fh:
        if new:
            fh.write(f"# {a.name}\n\n")
        if a.section:
            fh.write(f"\n## {a.section}\n")
        fh.write(f"- {now()[:16]} {a.text.strip()}\n")
    log_event(cfg, "заметка", файл=f.name, текст=a.text.strip()[:2000])
    print(f"записано: {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
