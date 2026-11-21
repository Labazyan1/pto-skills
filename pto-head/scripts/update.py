"""Установка и обновление скилла с GitHub (без git: архив репозитория по HTTPS).

python update.py --check          — есть ли новая версия (раз в день, иначе молчит; --force — проверить сейчас)
python update.py                  — обновить этот скилл до последней версии
python update.py --install pto-head [--to ПАПКА]   — поставить скилл (по умолчанию в ~/.claude/skills)
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


def archive(src: dict) -> bytes:
    url = os.environ.get("PTO_SKILL_ZIP") or f"https://codeload.github.com/{src['repo']}/zip/refs/heads/{src['branch']}"
    return _get(url)


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
    from common import home
    mark = home() / "update_check.json"
    today = dt.date.today().isoformat()
    try:
        last = json.loads(mark.read_text(encoding="utf-8")) if mark.exists() else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        last = {}
    if not force and last.get("день") == today:
        return 0
    try:
        remote = remote_version(source())
    except OSError as exc:
        print(f"Проверить обновление не удалось (нет сети?): {type(exc).__name__}")
        return 0
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(json.dumps({"день": today, "версия": remote}), encoding="utf-8")
    mine = local_version()
    if newer(remote, mine):
        print(f"Есть новая версия скилла: {remote} (у вас {mine}). Обновить: python scripts/update.py")
        news = SKILL_DIR / "CHANGELOG.md"
        if news.exists():
            print("Что нового — CHANGELOG.md после обновления.")
    else:
        print(f"Скилл актуален: {mine}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--install")
    ap.add_argument("--to")
    a = ap.parse_args(argv)
    if a.check:
        return check(a.force)
    src = source()
    if a.install:
        target = Path(a.to) if a.to else Path.home() / ".claude" / "skills" / a.install
        version = extract_skill(archive(src), a.install, target)
        print(f"Установлен {a.install} {version}: {target}. Откройте Claude и напишите, что нужно, например «покажи здание».")
        return 0
    mine = local_version()
    data = archive(src)
    version = extract_skill(data, SKILL_DIR.name, SKILL_DIR)
    print(f"Обновлено: {mine} → {version}" if version != mine else f"Уже последняя версия: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
