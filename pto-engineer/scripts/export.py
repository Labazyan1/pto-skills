"""Один архив с историей работы: пользователь сам пересылает его, кому решит (скилл ничего не отправляет).

python export.py                    — архив выгрузка_ПТО_<дата>.zip в папку «ПТО — выгрузка» рядом с папкой команды
                                      на Google Диске (нет Диска — рабочий стол); хранятся последние 4 архива
python export.py --папка ПУТЬ       — своя папка выгрузки (запоминается)
python export.py --все              — начальнику: подпапки всех участников папки команды
python export.py --без-файлов       — без оригиналов, только журнал и пути
python export.py --лимит 500        — потолок оригиналов, МБ (по умолчанию 500; не влезло — в список)
python export.py --если-старше 7    — собрать, только если прошлая выгрузка старше N дней

Внутри: журнал (запросы, итоги, сводки чатов, тройки), замечания технадзора, снимок рабочих папок,
заметки разметки (разметка_*.md из рабочих папок), оригиналы файлов из троек, ЧИТАТЬ.txt.
"""
from __future__ import annotations

import argparse
import os
import datetime as _dt
import sys
import zipfile
from pathlib import Path

from common import engineer_dir, home, load_config, log_event, now, read_journal, safe_name, save_config

OWN = ("журнал.jsonl", "замечания.json", "состояние.json", "владелец.json")


EXPORT_FOLDER = "ПТО — выгрузка"
KEEP = 4                                                   # сколько последних архивов держать в папке выгрузки


def out_dir(cfg: dict | None = None) -> Path:
    """Куда класть архив: PTO_EXPORT_DIR > своя папка (--папка) > «ПТО — выгрузка» рядом с папкой команды
    на Google Диске (её владелец открывает доступ, кому решит) > рабочий стол."""
    if os.environ.get("PTO_EXPORT_DIR"):
        return Path(os.environ["PTO_EXPORT_DIR"])
    cfg = cfg or {}
    if cfg.get("export_dir"):
        return Path(cfg["export_dir"])
    team = Path(cfg["team_folder"]) if cfg.get("team_folder") else None
    if team and team.parent.is_dir():
        return team.parent / EXPORT_FOLDER
    for d in (Path.home() / "Desktop", Path.home() / "Рабочий стол", Path.home() / "OneDrive" / "Desktop"):
        if d.is_dir():
            return d
    return home() / "выгрузка"


def _members(cfg: dict, all_: bool) -> list[Path]:
    if not all_:
        return [engineer_dir(cfg)]
    team = Path(cfg["team_folder"])
    return sorted(p for p in team.iterdir() if p.is_dir() and (p / "журнал.jsonl").exists()) if team.is_dir() else []


def _arc_path(f: Path) -> str:
    parts = [safe_name(x) for x in f.parts[1:]]          # без диска: C:\a\b.jpg → a/b.jpg
    return "/".join(parts)


def build(cfg: dict, all_: bool = False, with_files: bool = True, limit_mb: int = 500,
          target: Path | None = None) -> tuple[Path, dict]:
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    target = target or out_dir(cfg) / f"выгрузка_ПТО_{stamp}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    st = {"участников": 0, "записей": 0, "чатов": 0, "троек": 0, "файлов": 0, "заметок": 0,
          "не_влезло": [], "нет_файла": []}
    budget = limit_mb * 1024 * 1024
    seen: set[str] = set()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for m in _members(cfg, all_):
            st["участников"] += 1
            base = safe_name(m.name)
            for name in OWN:
                if (m / name).exists():
                    zf.write(m / name, f"{base}/{name}")
            for ev in read_journal(m / "журнал.jsonl"):
                st["записей"] += 1
                st["чатов"] += ev.get("действие") == "чат"
                if ev.get("действие") != "тройка":
                    continue
                st["троек"] += 1
                for p in [*(ev.get("вход") or []), *(ev.get("итог") or [])]:
                    if not with_files or p in seen:
                        continue
                    seen.add(p)
                    f = Path(p)
                    if not f.is_file():
                        st["нет_файла"].append(p)
                        continue
                    size = f.stat().st_size
                    if size > budget:
                        st["не_влезло"].append(p)
                        continue
                    budget -= size
                    zf.write(f, f"{base}/файлы/{_arc_path(f)}")
                    st["файлов"] += 1
        notes = []
        for root in cfg.get("work_folders") or []:
            r = Path(root)
            if r.is_dir():
                notes += [p for p in r.rglob("разметка_*.md") if p.is_file()]
        for i, p in enumerate(sorted(notes)):
            zf.write(p, f"заметки_разметки/{i:03d}_{safe_name(p.parent.name)}_{safe_name(p.name)}")
        st["заметок"] = len(notes)
        lines = [f"Выгрузка скилла ПТО, {now()}", f"Пользователь: {cfg.get('engineer', '')}",
                 f"Участников: {st['участников']}, записей журнала: {st['записей']}, сводок чатов: {st['чатов']}, "
                 f"троек: {st['троек']}, оригиналов: {st['файлов']}, заметок разметки: {st['заметок']}.",
                 "Журнал: журнал.jsonl, одна строка - одно событие. Тройка: вход, итог, правки пользователя.",
                 "Архив никуда не отправлен. Пересылает его только сам пользователь."]
        if st["не_влезло"]:
            lines += ["", "Не вошли по размеру (взять отдельно):", *st["не_влезло"]]
        if st["нет_файла"]:
            lines += ["", "Файл не найден на диске (перемещён или загружен прямо в чат):", *st["нет_файла"]]
        zf.writestr("ЧИТАТЬ.txt", "\r\n".join(lines))
    _rotate(target.parent, keep=KEEP)
    return target, st


def _rotate(folder: Path, keep: int) -> None:
    """Удаляет только свои старые архивы выгрузки (выгрузка_ПТО_*.zip), последние keep оставляет."""
    old = sorted(folder.glob("выгрузка_ПТО_*.zip"), key=lambda p: p.name)[:-keep]
    for p in old:
        try:
            p.unlink()
        except OSError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--все", dest="all_", action="store_true")
    ap.add_argument("--без-файлов", dest="nofiles", action="store_true")
    ap.add_argument("--лимит", dest="limit", type=int, default=500)
    ap.add_argument("--если-старше", dest="older", type=float)
    ap.add_argument("--в", dest="target")
    ap.add_argument("--папка", dest="folder", help="запомнить свою папку выгрузки")
    a = ap.parse_args(argv)
    cfg = load_config()
    if a.folder:
        cfg["export_dir"] = str(Path(a.folder).expanduser())
        save_config(cfg)
    last = cfg.get("last_export")
    if a.older is not None and last:
        age = _dt.datetime.now().astimezone() - _dt.datetime.fromisoformat(last)
        if age.total_seconds() < a.older * 86400:
            print(f"выгрузка свежая ({last[:10]}), не собираю")
            return 0
    target, st = build(cfg, all_=a.all_, with_files=not a.nofiles, limit_mb=a.limit,
                       target=Path(a.target) if a.target else None)
    cfg["last_export"] = now()
    save_config(cfg)
    log_event(cfg, "выгрузка", файл=str(target), оригиналов=st["файлов"])
    print(f"Архив: {target}")
    print(f"Сводок чатов: {st['чатов']}, троек: {st['троек']}, оригиналов: {st['файлов']}"
          + (f", не вошли по размеру: {len(st['не_влезло'])}" if st["не_влезло"] else "")
          + (f", не найдены на диске: {len(st['нет_файла'])}" if st["нет_файла"] else ""))
    print("Скилл архив никуда не отправляет. Он лежит в вашей папке: кому вы открыли к ней доступ, тот его увидит.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
