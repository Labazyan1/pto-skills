"""Установка и обновление скилла с GitHub (без git: архив репозитория по HTTPS).

python update.py --check          — есть ли новая версия (раз в день, иначе молчит; --force — проверить сейчас)
python update.py                  — показать, какие файлы изменит обновление (ничего не меняет)
python update.py --yes            — применить обновление (после того как человек посмотрел список и сказал «да»)
python update.py --off | --on     — выключить / включить проверку обновлений (скилл тогда не ходит в сеть сам)
python update.py --install pto-head [--to ПАПКА] [--version 2026.11.21]   — поставить скилл (по умолчанию в ~/.claude/skills)
Ставится выпуск по тегу v<версия> (не текущая ветка): то, что посмотрели, то и ставится. Сетевые обращения —
только чтение с raw.githubusercontent.com и codeload.github.com; данные скилл никуда не отправляет.
Источник — файл source.json рядом со SKILL.md ({"repo": "владелец/репозиторий", "branch": "main"}).
Настройки и данные (~/.pto-skill, папка команды) обновление не трогает: меняется только папка скилла.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = {"repo": "Labazyan1/pto-skills", "branch": "main"}
TIMEOUT = 20


def source() -> dict:
    p = SKILL_DIR / "source.json"
    try:
        return {**DEFAULT_SOURCE, **json.loads(p.read_text(encoding="utf-8"))} if p.exists() else dict(DEFAULT_SOURCE)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return dict(DEFAULT_SOURCE)


def local_version(d: Path = SKILL_DIR) -> str:
    p = d / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "0"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "pto-skill-update"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:   # noqa: S310 — только https GitHub
        return r.read()


def remote_version(src: dict) -> str:
    base = os.environ.get("PTO_SKILL_RAW") or f"https://raw.githubusercontent.com/{src['repo']}/{src['branch']}"
    return _get(f"{base}/VERSION").decode("utf-8").strip()


def archive(src: dict, version: str) -> bytes:
    """Архив выпуска по тегу v<версия>: код выпуска не меняется вслед за веткой."""
    url = os.environ.get("PTO_SKILL_ZIP") or f"https://codeload.github.com/{src['repo']}/zip/refs/tags/v{version}"
    return _get(url)


def _state_path() -> Path:
    from common import home
    return home() / "update_check.json"


def _state() -> dict:
    try:
        p = _state_path()
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}


def _save_state(st: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _files(root: Path) -> dict:
    import hashlib
    return {f.relative_to(root).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in root.rglob("*") if f.is_file() and "__pycache__" not in f.parts}


def plan(data: bytes, name: str, target: Path) -> dict:
    """Что изменит обновление: новые, изменённые, удалённые файлы (в target ничего не пишется)."""
    with tempfile.TemporaryDirectory(prefix="pto-skill-diff-") as tmp:
        stage = Path(tmp) / name
        version = extract_skill(data, name, stage)
        new, old = _files(stage), _files(target) if target.exists() else {}
        return {"версия": version, "новые": sorted(set(new) - set(old)), "удалённые": sorted(set(old) - set(new)),
                "изменённые": sorted(k for k in new if k in old and new[k] != old[k])}


def newer(a: str, b: str) -> bool:
    """a новее b: версии вида 2026.11.21 или 1.4.2."""
    def parts(v):
        return [int(x) if x.isdigit() else 0 for x in v.replace("-", ".").split(".")]
    return parts(a) > parts(b)


def extract_skill(data: bytes, name: str, target: Path) -> str:
    """Папку скилла `name` из архива репозитория — в target (старое содержимое заменяется). Защита от путей наружу."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [i for i in zf.infolist() if not i.is_dir()]
        prefix = None
        for i in members:
            parts = i.filename.split("/")
            if len(parts) >= 3 and parts[1] == name and parts[2] == "SKILL.md":
                prefix = f"{parts[0]}/{name}/"
                break
        if prefix is None:
            raise SystemExit(f"В репозитории нет скилла {name}")
        tmp = Path(tempfile.mkdtemp(prefix="pto-skill-"))
        try:
            stage = tmp / name
            for i in members:
                if not i.filename.startswith(prefix):
                    continue
                rel = i.filename[len(prefix):]
                if not rel or rel.startswith("/") or ".." in rel.split("/") or ":" in rel or "\\" in rel:
                    raise SystemExit(f"подозрительный путь в архиве: {i.filename}")
                out = stage / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(zf.read(i))
            version = local_version(stage)
            # содержимое меняем внутри папки, а не саму папку: на Windows папку, где стоит текущий каталог, не переименовать
            target.mkdir(parents=True, exist_ok=True)
            for child in target.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            shutil.copytree(stage, target, dirs_exist_ok=True)
            return version
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check(force: bool) -> int:
    last = _state()
    today = dt.date.today().isoformat()
    if last.get("выключено") and not force:
        return 0                                   # человек выключил проверку: в сеть не ходим
    if not force and last.get("день") == today:
        return 0
    try:
        remote = remote_version(source())
    except OSError as exc:
        print(f"Проверить обновление не удалось (нет сети?): {type(exc).__name__}")
        return 0
    _save_state({**last, "день": today, "версия": remote})
    mine = local_version()
    if newer(remote, mine):
        print(f"Есть новая версия скилла: {remote} (у вас {mine}). Посмотреть, что изменится: python scripts/update.py")
    else:
        print(f"Скилл актуален: {mine}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--install")
    ap.add_argument("--to")
    ap.add_argument("--version")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--on", action="store_true")
    a = ap.parse_args(argv)
    if a.off or a.on:
        _save_state({**_state(), "выключено": bool(a.off)})
        print("Проверка обновлений выключена: скилл сам в сеть не ходит. Включить: --on." if a.off
              else "Проверка обновлений включена (раз в день).")
        return 0
    if a.check:
        return check(a.force)
    src = source()
    version = a.version or remote_version(src)
    if a.install:
        target = Path(a.to) if a.to else Path.home() / ".claude" / "skills" / a.install
        got = extract_skill(archive(src, version), a.install, target)
        print(f"Установлен {a.install} {got}: {target}. Откройте Claude и напишите, что нужно, например «покажи здание».")
        return 0
    mine = local_version()
    if not a.version and not newer(version, mine):
        print(f"Уже последняя версия: {mine}")
        return 0
    data = archive(src, version)
    ch = plan(data, SKILL_DIR.name, SKILL_DIR)
    if not a.yes:
        print(f"Обновление {mine} → {ch['версия']} изменит файлы скилла (настройки и данные не трогает):")
        for k, label in (("новые", "новые"), ("изменённые", "изменённые"), ("удалённые", "удалённые")):
            if ch[k]:
                print(f"  {label}: " + ", ".join(ch[k]))
        print("Что нового — CHANGELOG.md в репозитории. Применить: python scripts/update.py --yes"
              + (f" --version {a.version}" if a.version else ""))
        return 3
    got = extract_skill(data, SKILL_DIR.name, SKILL_DIR)
    print(f"Обновлено: {mine} → {got}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
