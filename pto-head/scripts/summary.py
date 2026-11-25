"""Сводка начальника ПТО по папке команды.

python summary.py [--days 7] [--team ПАПКА]  → отчёт в консоль
python summary.py --members "Петров П.П.,Сидоров С.С."  → <команда>/команда.json (кого ждём: без данных видно)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

from common import is_open, load_acts, load_config, load_remarks, log_event, read_journal, safe_name

STALE_DAYS = 3                    # скан старше — данные инженера могли устареть (не синхронизирован Диск)


def collect(team: Path) -> dict:
    """{инженер: {acts: [...], journal: [...]}} по подпапкам команды."""
    out = {}
    for d in sorted(p for p in team.iterdir() if p.is_dir() and not p.name.startswith(".")):
        acts, journal = load_acts(d), read_journal(d / "журнал.jsonl")
        snap = {}
        if (d / "состояние.json").exists():
            try:
                snap = json.loads((d / "состояние.json").read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                snap = {}
        acts += scanned_acts(snap, {str(a.get("номер")) for a in acts})
        remarks = load_remarks(d)
        if acts or snap or remarks or any(e.get("действие") != "сводка" for e in journal):   # пустая папка (только настройка) не в счёт
            for a in acts:
                a["_инженер"] = d.name
            for r in remarks:
                r["_инженер"] = d.name
            out[d.name] = {"acts": acts, "journal": journal, "snap": snap, "remarks": remarks, "роль": _role(d)}
    return out


def _role(d: Path) -> str:
    try:
        return json.loads((d / "владелец.json").read_text(encoding="utf-8")).get("роль") or "инженер"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return "инженер"


def open_remarks(data: dict) -> list[dict]:
    return [r for v in data.values() for r in v.get("remarks", []) if is_open(r)]


def members(team: Path) -> list[str]:
    p = team / "команда.json"
    try:
        return [str(x) for x in json.loads(p.read_text(encoding="utf-8")).get("инженеры", [])] if p.exists() else []
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return []


def stale(snap: dict, days: int = STALE_DAYS) -> bool:
    t = str(snap.get("обновлено") or "")
    if not t:
        return False
    try:
        when = dt.datetime.fromisoformat(t)
    except ValueError:
        return False
    return (dt.datetime.now(when.tzinfo) - when).days >= days


ACT_KINDS = ("АОСР", "АООК", "акт испытаний", "акт готовности")
STATUS_MAP = {"подписан?": "подписан", "на подписи?": "на_подписи", "в работе": "черновик"}


def scanned_acts(snap: dict, known: set) -> list[dict]:
    """Акты из скана рабочих папок (Word/PDF как есть) — в общий формат доски. Акты из скилла важнее."""
    out, seen = [], set()
    for f in snap.get("файлы") or []:
        if f.get("тип") not in ACT_KINDS:
            continue
        num = f.get("номер") or Path(f["файл"]).stem
        if num in known or num in seen:      # один акт в DOCX и PDF — одна запись (статус берём «старший»)
            for a in out:
                if a["номер"] == num and f.get("статус") == "подписан?":
                    a["статус"] = "подписан"
            continue
        seen.add(num)
        out.append({"номер": num, "работы": f.get("работы") or f["файл"], "система": f.get("система") or "",
                    "захватка": f.get("захватка") or "", "дата_акта": f.get("дата") or "",
                    "статус": STATUS_MAP.get(f.get("статус"), "черновик"), "_скан": True, "_файл": f["файл"]})
    return out


def text_summary(data: dict, days: int, expected: list[str] | None = None) -> str:
    acts = [a for v in data.values() for a in v["acts"]]
    rem = open_remarks(data)
    lines = [f"Сводка. Инженеров: {len(data)}, актов: {len(acts)}, открытых замечаний технадзора/заказчика: {len(rem)}."]
    silent = [m for m in (expected or []) if safe_name(m) not in data]
    if silent:
        lines.append("Нет данных от инженеров (папка пуста или Диск не синхронизирован): " + ", ".join(silent))
    for name, v in data.items():
        # только готовность и замечания: журнал запросов и список файлов инженера в сводку не идут (решение 21.11)
        st = defaultdict(int)
        for a in v["acts"]:
            st[a.get("статус") or "черновик"] += 1
        mine = [r for r in rem if r.get("_инженер") == name]
        snap = v.get("snap") or {}
        when = str(snap.get("обновлено", ""))[:16].replace("T", " ") or "—"
        who = f"{name} (начальник, свои системы)" if v.get("роль") == "начальник" else name
        lines.append(f"- {who}: подписано {st['подписан']}, на подписи {st['на_подписи']}, в работе {st['черновик']}; "
                     f"открытых замечаний {len(mine)}; данные обновлены {when}"
                     + (f" — СТАРШЕ {STALE_DAYS} ДН., попросите инженера запустить скан" if stale(snap) else ""))
        for r in mine[:5]:
            lines.append(f"    замечание {r.get('от')}{' к ' + r['акт'] if r.get('акт') else ''}: {str(r.get('текст', ''))[:100]}"
                         + (f" (ответ дан: {str(r.get('ответ'))[:60]})" if r.get("статус") == "ответ_дан" else ""))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--team")
    ap.add_argument("--members")
    a = ap.parse_args(argv)
    cfg = load_config() if not a.team else None
    team = Path(a.team or cfg["team_folder"])
    if a.members:
        names = [s.strip() for s in a.members.split(",") if s.strip()]
        (team / "команда.json").write_text(json.dumps({"инженеры": names}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"команда сохранена: {len(names)} инженеров")
        return 0
    data = collect(team)
    print(text_summary(data, a.days, members(team)))
    if cfg:
        log_event(cfg, "сводка", инженеров=len(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
