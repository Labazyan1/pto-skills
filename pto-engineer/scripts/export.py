"""Один архив с историей работы: пользователь сам пересылает его, кому решит (скилл ничего не отправляет).

python export.py                    — архив выгрузка_ПТО_<имя>_<дата>.zip в папку «_выгрузка» внутри папки команды
                                      (нет папки команды — рабочий стол); хранятся последние 8 на человека
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
import re
import os
import subprocess
import tempfile
import datetime as _dt
import sys
import zipfile
from pathlib import Path

from common import claude_dir, engineer_dir, home, load_config, log_event, now, read_journal, safe_name, save_config

OWN = ("журнал.jsonl", "замечания.json", "состояние.json", "владелец.json")


EXPORT_FOLDER = "_выгрузка"
KEEP = 8                                                   # сколько последних архивов держать в папке выгрузки


def out_dir(cfg: dict | None = None) -> Path:
    """Куда класть архив: PTO_EXPORT_DIR > своя папка (--папка) > «_выгрузка» внутри папки команды (она на Диске
    начальника и видна ему у каждого инженера; начальник открывает её, кому решит) > рабочий стол."""
    if os.environ.get("PTO_EXPORT_DIR"):
        return Path(os.environ["PTO_EXPORT_DIR"])
    cfg = cfg or {}
    if cfg.get("export_dir"):
        return Path(cfg["export_dir"])
    team = Path(cfg["team_folder"]) if cfg.get("team_folder") else None
    if team and team.is_dir():
        return team / EXPORT_FOLDER
    for d in (Path.home() / "Desktop", Path.home() / "Рабочий стол", Path.home() / "OneDrive" / "Desktop"):
        if d.is_dir():
            return d
    return home() / "выгрузка"


def _readiness(cfg: dict) -> dict[str, bytes]:
    """Текст сводки по команде — свежий, тем же скриптом, что у начальника."""
    here = Path(__file__).resolve().parent
    env = {**os.environ, "PTO_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8"}
    out: dict[str, bytes] = {}
    try:
        r = subprocess.run([sys.executable, str(here / "summary.py")], cwd=here, env=env, capture_output=True, timeout=300)
        if r.returncode == 0 and r.stdout:
            out["сводка.txt"] = r.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return out


SKILL_MARKERS = ("pto-engineer", "pto-head", "scripts/log.py", "scripts\\log.py", "pto-skill")


def _claude_code(zf: zipfile.ZipFile, cfg: dict, budget: int, st: dict) -> tuple[int, int]:
    """Вкладка Code на этом ПК: сессии, где работал скилл (истории с картинками и вложения), изменённые после
    прошлой выгрузки. Посторонние проекты не берутся."""
    root = claude_dir()
    since = 0.0
    if cfg.get("last_export"):
        try:
            since = _dt.datetime.fromisoformat(cfg["last_export"]).timestamp()
        except ValueError:
            since = 0.0
    n = 0
    for f in sorted((root / "projects").glob("*/*.jsonl")) if (root / "projects").is_dir() else []:
        try:
            if f.stat().st_mtime < since:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not any(m in text for m in SKILL_MARKERS):
            continue
        size = f.stat().st_size
        if size > budget:
            st["не_влезло"].append(str(f))
            continue
        budget -= size
        zf.write(f, f"claude_code/{safe_name(f.parent.name)}/{f.name}")
        up = root / "uploads" / f.stem
        if up.is_dir():
            for u in sorted(up.iterdir()):
                if u.is_file() and u.stat().st_size <= budget:
                    budget -= u.stat().st_size
                    zf.write(u, f"claude_code/вложения/{f.stem}/{safe_name(u.name)}")
        n += 1
    return n, budget


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
    who = safe_name(cfg.get("engineer") or "пользователь")
    target = target or out_dir(cfg) / f"выгрузка_ПТО_{who}_{stamp}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    st = {"участников": 0, "записей": 0, "чатов": 0, "пожеланий": 0, "троек": 0, "файлов": 0, "заметок": 0,
          "не_влезло": [], "нет_файла": []}
    budget = limit_mb * 1024 * 1024
    seen: set[str] = set()
    wishes: list[str] = []
    closed: set[str] = set()
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
                if ev.get("действие") == "пожелание":
                    st["пожеланий"] += 1
                    wishes.append(f"{str(ev.get('время', ''))[:16]}  {ev.get('инженер', m.name)}: {ev.get('текст', '')}")
                if ev.get("действие") != "тройка":
                    continue
                st["троек"] += 1
                if ev.get("id"):
                    closed.add(str(ev["id"]))
                if ev.get("id") and (home() / "история" / safe_name(str(ev["id"]))).is_dir():
                    st["нет_файла"] += [p for p in [*(ev.get("вход") or []), *(ev.get("итог") or [])]
                                        if not Path(p).is_file() and p not in seen]
                    seen.update(ev.get("вход") or [])
                    continue                     # копии входа, версий и итога уже лежат в истории
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
        hroot = home() / "история"
        hist = sorted(d for d in hroot.iterdir() if d.is_dir()) if hroot.is_dir() and with_files else []
        packed: set[str] = set()                 # истории, вошедшие в архив целиком: только их можно чистить
        for d in hist:
            whole = True
            for f in sorted(d.iterdir()):
                if f.is_file() and f.stat().st_size <= budget:
                    budget -= f.stat().st_size
                    zf.write(f, f"история/{d.name}/{f.name}")
                elif f.is_file():
                    st["не_влезло"].append(str(f))
                    whole = False
            if whole:
                packed.add(d.name)
        st["историй"] = len(hist)
        if with_files:
            st["сессий_code"], budget = _claude_code(zf, cfg, budget, st)
        nroot = home() / "заметки"
        for f in sorted(nroot.rglob("*.md")) if nroot.is_dir() else []:
            zf.write(f, f"заметки/{safe_name(f.parent.name)}/{safe_name(f.name)}")
        notes = []
        for root in cfg.get("work_folders") or []:
            r = Path(root)
            if r.is_dir():
                notes += [p for p in r.rglob("разметка_*.md") if p.is_file()]
        for i, p in enumerate(sorted(notes)):
            zf.write(p, f"заметки_разметки/{i:03d}_{safe_name(p.parent.name)}_{safe_name(p.name)}")
        st["заметок"] = len(notes)
        ready = _readiness(cfg)
        for name, data in ready.items():
            zf.writestr(f"готовность/{name}", data)
        zf.writestr("ПОЖЕЛАНИЯ.txt", "\r\n".join(wishes) or "Пожеланий нет.")
        lines = [f"Выгрузка скилла ПТО, {now()}", f"Пользователь: {cfg.get('engineer', '')}",
                 f"Участников: {st['участников']}, записей журнала: {st['записей']}, сводок чатов: {st['чатов']}, "
                 f"троек: {st['троек']}, историй файлов: {st.get('историй', 0)}, сессий вкладки Code: {st.get('сессий_code', 0)}, пожеланий: {st['пожеланий']}, оригиналов: {st['файлов']}, заметок разметки: {st['заметок']}.",
                 "Пожелания (было так, надо так) - ПОЖЕЛАНИЯ.txt. Сводка по команде - папка готовность.",
                 "Журнал: журнал.jsonl, одна строка - одно событие. Тройка: вход, итог, правки пользователя.",
                 "Вкладка Code: папка claude_code - истории сессий, где работал скилл (с картинками), и вложения к ним.",
                 "История файла: папка история/<номер> - копии входа, каждой версии и итога; шаги (замечание, правка) - в журнале, события файл_начало, файл_шаг, тройка с тем же id.",
                 "Архив никуда не отправлен. Пересылает его только сам пользователь."]
        if st["не_влезло"]:
            lines += ["", "Не вошли по размеру (взять отдельно):", *st["не_влезло"]]
        if st["нет_файла"]:
            lines += ["", "Файл не найден на диске (перемещён или загружен прямо в чат):", *st["нет_файла"]]
        zf.writestr("ЧИТАТЬ.txt", "\r\n".join(lines))
    _rotate(target.parent, keep=KEEP)
    _prune_history(closed & packed if with_files else set())
    return target, st


def _prune_history(closed: set[str], days: int = 30) -> None:
    """Чтобы копии не копились: законченные истории (есть «тройка» с этим id), уже попавшие в архив и старше
    days дней, удаляются с ПК. Незаконченные не трогаются."""
    import shutil
    import time
    root = home() / "история"
    if not root.is_dir():
        return
    edge = time.time() - days * 86400
    for d in root.iterdir():
        if d.is_dir() and d.name in {safe_name(x) for x in closed} and d.stat().st_mtime < edge:
            shutil.rmtree(d, ignore_errors=True)


def _rotate(folder: Path, keep: int) -> None:
    """Удаляет только свои старые архивы выгрузки (выгрузка_ПТО_*.zip), последние keep оставляет."""
    groups: dict[str, list[Path]] = {}
    for p in folder.glob("выгрузка_ПТО_*.zip"):
        m = re.match(r"(.*)_\d{4}-\d{2}-\d{2}_\d{4}\.zip$", p.name)
        groups.setdefault(m.group(1) if m else p.name, []).append(p)
    old = [p for g in groups.values() for p in sorted(g, key=lambda x: x.name)[:-keep]]   # по 4 на человека
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
    print(f"Сводок чатов: {st['чатов']}, пожеланий: {st['пожеланий']}, троек: {st['троек']}, оригиналов: {st['файлов']}"
          + (f", не вошли по размеру: {len(st['не_влезло'])}" if st["не_влезло"] else "")
          + (f", не найдены на диске: {len(st['нет_файла'])}" if st["нет_файла"] else ""))
    print("Скилл архив никуда не отправляет. Он лежит в вашей папке: кому вы открыли к ней доступ, тот его увидит.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
