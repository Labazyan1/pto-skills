"""Реестры по актам инженера — CSV для Excel (разделитель «;», UTF-8 с BOM).

python registry.py ид [--out реестр_ИД.csv] [--folder ПАПКА]        — реестр исполнительной документации
python registry.py документы [--out реестр_документов.csv]         — документы о качестве: где используются, чего нет
python registry.py опись --acts "АОСР-1,АОСР-2" [--out опись.csv]  — опись передачи: акты + их приложения без дублей
python registry.py паспорта [--out реестр_паспортов.csv]           — файлы паспортов/сертификатов из скана рабочих папок:
    номер, дата, где лежит, в каких актах упомянут; и документы из актов, файла которых не нашлось
Без --folder — своя папка из настроек. Файлы пишутся в папку инженера (или по --out).
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import OrderedDict
from pathlib import Path

from check import DOC_DATE, iso

SCHEME = re.compile(r"^(ИС|ИГС|исполнительн)", re.I)


def write_csv(path: Path, header: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerows([["" if v is None else v for v in r] for r in rows])
    return path


def _key(a: dict):
    return (iso(a.get("дата_акта")) or "9999", str(a.get("номер", "")))


def id_registry(acts: list[dict]) -> tuple[list[str], list[list]]:
    head = ["№", "Номер акта", "Дата акта", "Наименование работ", "Система", "Захватка", "Период работ", "Статус", "Приложения"]
    rows = []
    for i, a in enumerate(sorted(acts, key=_key), 1):
        period = f"{a.get('дата_начала', '')} — {a.get('дата_окончания', '')}".strip(" —")
        rows.append([i, a.get("номер"), a.get("дата_акта"), a.get("работы"), a.get("система"), a.get("захватка"),
                     period, a.get("статус") or "черновик", "; ".join(map(str, a.get("приложения") or []))])
    return head, rows


def docs_registry(acts: list[dict]) -> tuple[list[str], list[list]]:
    head = ["№", "Документ о качестве", "Дата документа", "Материал", "Акты", "Замечание"]
    docs: "OrderedDict[str, dict]" = OrderedDict()
    for a in sorted(acts, key=_key):
        end = iso(a.get("дата_окончания"))
        for m in a.get("материалы") or []:
            if not isinstance(m, dict):
                continue
            doc = str(m.get("документ") or "").strip()
            key = doc.lower() or f"(нет документа) {m.get('наименование', '')}".lower()
            e = docs.setdefault(key, {"doc": doc, "date": None, "mats": [], "acts": [], "notes": set()})
            dm = DOC_DATE.search(doc)
            e["date"] = iso(dm.group(1)) if dm else e["date"]
            if m.get("наименование") and m["наименование"] not in e["mats"]:
                e["mats"].append(m["наименование"])
            if a.get("номер") not in e["acts"]:
                e["acts"].append(a.get("номер"))
            if not doc:
                e["notes"].add("нет документа о качестве")
            elif e["date"] and end and e["date"] > end:
                e["notes"].add(f"позже окончания работ по {a.get('номер')}")
            elif not dm:
                e["notes"].add("нет даты документа")
    rows = [[i, e["doc"] or "—", e["date"], "; ".join(e["mats"]), "; ".join(map(str, e["acts"])), "; ".join(sorted(e["notes"]))]
            for i, e in enumerate(docs.values(), 1)]
    return head, rows


def inventory(acts: list[dict], nums: list[str]) -> tuple[list[str], list[list]]:
    head = ["№", "Наименование документа", "Номер", "Дата", "Примечание"]
    by_num = {str(a.get("номер")): a for a in acts}
    missing = [n for n in nums if n not in by_num]
    if missing:
        raise SystemExit(f"Нет актов: {', '.join(missing)}")
    rows, seen = [], set()

    def add(name, num, date, note=""):
        k = (name, str(num))
        if k not in seen:
            seen.add(k)
            rows.append([len(rows) + 1, name, num, date, note])

    for n in nums:
        a = by_num[n]
        add("Акт освидетельствования скрытых работ", n, a.get("дата_акта"), a.get("работы", "")[:120])
        for s in a.get("исполнительные_схемы") or []:
            add("Исполнительная схема", str(s).split(" ")[0], "", f"к акту {n}")
        for m in a.get("материалы") or []:
            doc = str((m or {}).get("документ") or "")
            if doc:
                dm = DOC_DATE.search(doc)
                add("Документ о качестве", doc.split(" от ")[0], iso(dm.group(1)) if dm else "", (m.get("наименование") or "")[:80])
    return head, rows


def _norm(s) -> str:
    return re.sub(r"[\s№#]+", "", str(s or "")).lower()


def passports_registry(acts: list[dict], snap: dict) -> tuple[list[str], list[list]]:
    """Паспорта и сертификаты как файлы (скан) ↔ документы о качестве в актах."""
    head = ["№", "Файл", "Номер", "Дата", "Папка", "Акты", "Замечание"]
    files = [f for f in snap.get("файлы") or [] if f.get("тип") == "документ о качестве"]
    used = []                                            # (номер документа из акта, акт)
    for a in acts:
        for m in a.get("материалы") or []:
            doc = str((m or {}).get("документ") or "")
            num = re.search(r"№\s*([\w\-./]+)", doc)
            if num:
                used.append((_norm(num.group(1)), doc, a.get("номер")))
    rows, matched = [], set()
    for f in files:
        n = _norm(f.get("номер"))
        hit = [act for k, _, act in used if n and (k == n or k.endswith(n) or n.endswith(k))]
        matched.update(k for k, _, _ in used if n and (k == n or k.endswith(n) or n.endswith(k)))
        note = "" if hit else ("номер не распознан по имени файла" if not n else "не упомянут ни в одном акте")
        rows.append([len(rows) + 1, f.get("файл"), f.get("номер"), f.get("дата"), f.get("папка", ""),
                     "; ".join(dict.fromkeys(map(str, hit))), note])
    for k, doc, act in used:
        if k not in matched:
            rows.append([len(rows) + 1, "—", doc, "", "", str(act), "файла документа в рабочих папках не нашлось"])
            matched.add(k)
    return head, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["ид", "документы", "опись", "паспорта"])
    ap.add_argument("--folder")
    ap.add_argument("--out")
    ap.add_argument("--acts", default="")
    a = ap.parse_args(argv)
    from common import engineer_dir, load_acts, load_config, log_event
    cfg = None if a.folder else load_config()
    folder = Path(a.folder) if a.folder else engineer_dir(cfg)
    acts = load_acts(folder)
    if a.kind == "ид":
        head, rows = id_registry(acts); name = "реестр_ИД.csv"
    elif a.kind == "документы":
        head, rows = docs_registry(acts); name = "реестр_документов.csv"
    elif a.kind == "паспорта":
        import json
        sp = folder / "состояние.json"
        snap = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
        if not snap:
            print("Сначала скан рабочих папок: python scan.py"); return 2
        head, rows = passports_registry(acts, snap); name = "реестр_паспортов.csv"
    else:
        nums = [n.strip() for n in a.acts.split(",") if n.strip()]
        if not nums:
            ap.error("--acts: номера актов через запятую")
        head, rows = inventory(acts, nums); name = "опись_передачи.csv"
    out = write_csv(Path(a.out) if a.out else folder / name, head, rows)
    print(f"{out} — строк: {len(rows)}")
    if a.kind in ("документы", "паспорта"):
        bad = [r for r in rows if r[-1]]
        if bad:
            print("Замечания:"); [print(f"- {r[1] if r[1] != '—' else r[2]}: {r[-1]}") for r in bad]
    if cfg:
        log_event(cfg, "сводка", заметка=f"реестр {a.kind}", строк=len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
