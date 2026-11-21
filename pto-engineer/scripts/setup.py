"""Первая настройка скилла: имя, почта, папка команды на Google Диске, рабочие папки, объекты.

python setup.py --engineer "Петров П.П." [--email p@example.ru] [--team "G:/Мой диск/ПТО — ЖК Пример"] [--work "папка"]
python setup.py --role начальник --engineer "Имя" --create "ЖК Пример"   — начальник: создать папку «ПТО — ЖК Пример» на Диске
python setup.py --show           — текущие настройки
python setup.py --objects        — объекты (папки команды), с которыми уже работали
python setup.py --object "ПТО — ЖК Другой"   — переключиться на другой объект (найдёт папку на Диске)
python setup.py --check          — проверить окружение: Python, Google Диск для компьютера, папки «ПТО — …»
Без --team папка команды ищется сама на Google Диске для компьютера. Скилл ничего не передаёт разработчикам.
"""
from __future__ import annotations

import argparse
import json
import os
import string
import sys
from pathlib import Path

from common import config_path, engineer_dir, log_event, safe_name, save_config

SHORTCUT_HINT = ("Если папкой с вами поделились, на компьютере её не видно, пока она не добавлена в «Мой диск»: "
                 "drive.google.com → «Доступные мне» → правой кнопкой по папке → «Упорядочить» → «Добавить ярлык» → "
                 "«Мой диск». Через минуту папка появится в «Google Диске для компьютера».")
DRIVE_HINT = ("Не найден «Google Диск для компьютера». Установите: https://www.google.com/drive/download/ → войдите "
              "в аккаунт Google. На ПК появится диск «Google Drive» (обычно G:).")


def drive_roots() -> list[Path]:
    """Корни Google Диска для компьютера на этом ПК. PTO_DRIVE_ROOTS (через «;») — свои места (и для тестов)."""
    env = os.environ.get("PTO_DRIVE_ROOTS")
    if env:
        roots = [Path(p) for p in env.split(";") if p]
    else:
        home = Path.home()
        roots = [home / "Google Drive", home / "Мой диск", home / "My Drive", home / "Google Drive" / "My Drive"]
        roots += [Path(f"{d}:/") / sub for d in string.ascii_uppercase[6:]
                  for sub in ("Мой диск", "My Drive", "Общие диски", "Shared drives")]
    out = []
    for r in roots:
        try:
            if r.is_dir():
                out.append(r)
        except OSError:
            continue
    return out


def find_team_folders(name: str | None = None) -> list[Path]:
    """Папки «ПТО — …» на Google Диске для компьютера (или папка с таким именем)."""
    out = []
    for r in drive_roots():
        try:
            for d in r.iterdir():
                if d.is_dir() and (d.name == name if name else d.name.startswith("ПТО")):
                    out.append(d)
        except OSError:
            continue
    return sorted(set(out))


def load() -> dict:
    p = config_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def remember_object(cfg: dict, team: Path) -> None:
    cfg.setdefault("objects", {})[team.name] = str(team)
    cfg["team_folder"] = str(team)


def check_env() -> int:
    ok = sys.version_info >= (3, 9)
    print(f"Python {sys.version.split()[0]}: {'подходит' if ok else 'нужен 3.9 или новее'}")
    roots = drive_roots()
    if not roots:
        print(DRIVE_HINT)
        return 2
    print("Google Диск для компьютера: " + ", ".join(str(r) for r in roots))
    found = find_team_folders()
    print("Папки команды: " + (", ".join(f.name for f in found) if found else "нет. " + SHORTCUT_HINT))
    return 0 if ok else 2


def claim(cfg: dict) -> str | None:
    """Своя подпапка инженера: занята другим человеком с тем же именем — отказ."""
    d = engineer_dir(cfg)
    owner = d / "владелец.json"
    me = {"имя": cfg["engineer"], "почта": cfg.get("email", ""), "роль": cfg.get("role") or "инженер"}
    if owner.exists():
        try:
            other = json.loads(owner.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            other = {}
        if other.get("почта") and me["почта"] and other["почта"].lower() != me["почта"].lower():
            return (f"Папка «{d.name}» уже занята другим инженером ({other['почта']}). "
                    "Укажите имя иначе, например с участком: --engineer \"Петров П.П. (ОВ)\"")
    d.mkdir(parents=True, exist_ok=True)
    owner.write_text(json.dumps(me, ensure_ascii=False), encoding="utf-8")
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engineer")
    ap.add_argument("--email", default="")
    ap.add_argument("--team")
    ap.add_argument("--create", help="начальник: создать папку команды с этим именем на Google Диске")
    ap.add_argument("--work", action="append", default=[], help="рабочая папка инженера (можно несколько)")
    ap.add_argument("--role", choices=["инженер", "начальник"])
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--objects", action="store_true")
    ap.add_argument("--object")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)

    cfg = load()
    if a.check:
        return check_env()
    if a.show:
        print(json.dumps(cfg, ensure_ascii=False, indent=2) if cfg else "не настроено")
        return 0
    if a.objects:
        objs = cfg.get("objects") or {}
        for name, path in objs.items():
            print(("* " if path == cfg.get("team_folder") else "  ") + f"{name}: {path}")
        if not objs:
            print("объектов нет")
        return 0
    if a.object:
        if not cfg:
            print("не настроено: сначала setup.py --engineer ...")
            return 1
        known = (cfg.get("objects") or {}).get(a.object)
        team = Path(known) if known and Path(known).is_dir() else next(iter(find_team_folders(a.object)), None)
        if not team:
            print(f"Папка «{a.object}» не найдена на Google Диске. {SHORTCUT_HINT}")
            return 2
        remember_object(cfg, team)
        save_config(cfg)
        print(f"Текущий объект: {team.name} ({team})")
        return 0

    engineer = (a.engineer or cfg.get("engineer") or "").strip()
    if not engineer:
        ap.error("нужно --engineer (имя, как подписывать папку)")
    role = a.role or cfg.get("role") or "инженер"
    if a.create:
        roots = [r for r in drive_roots() if r.name in ("Мой диск", "My Drive")] or drive_roots()
        if not roots:
            print(DRIVE_HINT)
            return 2
        team = roots[0] / safe_name(a.create if a.create.startswith("ПТО") else f"ПТО — {a.create}")
        team.mkdir(exist_ok=True)
        print(f"Папка команды: {team}")
        print("Откройте её инженерам: drive.google.com → правой кнопкой по папке → «Поделиться» → почты инженеров, "
              "роль «Редактор». Инженерам: поставить «Google Диск для компьютера» и добавить папку ярлыком в «Мой диск».")
    elif a.team:
        team = Path(a.team)
    elif cfg.get("team_folder") and Path(cfg["team_folder"]).is_dir():
        team = Path(cfg["team_folder"])
    else:
        found = find_team_folders()
        if len(found) != 1:
            if not drive_roots():
                print(DRIVE_HINT)
            elif not found:
                print("Папку команды не нашёл сам. " + SHORTCUT_HINT)
            else:
                print("Нашёл несколько папок команды:")
                for f in found:
                    print(f"  {f}")
            print("Укажите нужную: --team \"путь\" или --object \"имя папки\".")
            return 2
        team = found[0]
        print(f"Папка команды: {team}")
    if not team.is_dir():
        print(f"Папка команды не найдена: {team}. Проверьте, что Google Диск для компьютера запущен. {SHORTCUT_HINT}")
        return 2
    cfg.update(engineer=engineer, email=(a.email or cfg.get("email") or "").strip(), role=role,
               work_folders=[str(Path(w)) for w in a.work] or cfg.get("work_folders") or [])
    for k in ("share", "share_name", "dev_folder", "consent_version", "consent_at", "consent_revoked"):
        cfg.pop(k, None)                      # старые настройки передачи разработчикам больше не используются
    remember_object(cfg, team)
    err = claim(cfg)                          # своя подпапка — и у начальника: у него тоже свои системы и запросы
    if err:
        print(err)
        return 3
    save_config(cfg)
    log_event(cfg, "сводка", заметка="скилл настроен", роль=role)
    print(f"Готово: {engineer}, объект {team.name}, роль {role}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
