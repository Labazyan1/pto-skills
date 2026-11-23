"""Общий код скиллов ПТО: настройка (объекты), журнал, замечания технадзора.

Только стандартная библиотека Python 3.9+. Один и тот же файл лежит в обоих скиллах
(pto-engineer/scripts и pto-head/scripts) — скилл ставится отдельно и должен работать сам.

Папки (Google Диск для компьютера синхронизирует их как обычные):
  <команда>/<инженер>/акты/<номер>.json   — акты инженера (настоящие ФИО, это рабочие документы)
  <команда>/<инженер>/журнал.jsonl         — история действий, пишет только этот инженер
  <команда>/<инженер>/замечания.json       — замечания технадзора/заказчика и ответы на них
Наружу (разработчикам) скилл ничего не передаёт: работу делает Claude команды по её подписке.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("PTO_SKILL_HOME") or Path.home() / ".pto-skill")


def config_path() -> Path:
    return home() / "config.json"


def load_config() -> dict:
    p = config_path()
    if not p.exists():
        raise SystemExit("Скилл не настроен: запустите scripts/setup.py (имя, папка команды).")
    return json.loads(p.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    home().mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(s: str, default: str = "без_имени") -> str:
    """Имя файла/папки из данных: без разделителей пути и запрещённых в Windows символов."""
    s = _BAD.sub("_", str(s)).strip(" .")[:80]
    if not s or s in {".", ".."} or s.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        return default
    return s


def engineer_dir(cfg: dict) -> Path:
    return Path(cfg["team_folder"]) / safe_name(cfg["engineer"])


# ---------- журнал ----------
ACTIONS = {"создан", "исправлен", "проверка", "нет_формы", "сбой", "сводка", "скан", "запрос", "итог", "замечание", "уборка", "чат", "тройка", "выгрузка", "пожелание", "файл_начало", "файл_шаг"}


def log_event(cfg: dict, action: str, **data) -> dict:
    """Строка в журнал инженера (только в папке команды)."""
    if action not in ACTIONS:
        raise ValueError(f"неизвестное действие: {action}")
    ev = {"время": now(), "инженер": cfg["engineer"], "действие": action, **data}
    d = engineer_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "журнал.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def read_journal(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue                    # битая строка (обрыв синхронизации) не валит сводку
    return out


def load_acts(folder: Path) -> list[dict]:
    acts = []
    for p in sorted((folder / "акты").glob("*.json")) if (folder / "акты").exists() else []:
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(a, dict):
            a.setdefault("_файл", p.name)
            acts.append(a)
    return acts


# ---------- замечания технадзора / заказчика ----------
REMARK_OPEN = ("открыто", "ответ_дан")         # «снято» — закрыто; «ответ_дан» — ждём, примет ли технадзор


def load_remarks(folder: Path) -> list[dict]:
    p = folder / "замечания.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def save_remarks(folder: Path, remarks: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "замечания.json.tmp"
    tmp.write_text(json.dumps(remarks, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(folder / "замечания.json")


def is_open(r: dict) -> bool:
    return str(r.get("статус") or "открыто") in REMARK_OPEN
