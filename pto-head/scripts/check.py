"""Проверка актов кодом (не моделью): даты, обязательные поля, документы о качестве, дубли номеров,
порядок работ, устаревшие нормы. Базовый набор; полная проверка — на платформе.

python check.py [папка_инженера]   — по умолчанию своя папка из настроек; печатает отчёт и пишет в журнал
python check.py --json файл.json   — проверить один акт или список актов (без журнала)
Нормы в списке устаревших — ЧЕРНОВИК, сверяйте с действующей редакцией.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CRIT, MAJOR, MINOR = "критично", "важно", "мелочь"
REQUIRED = ["номер", "дата_акта", "работы", "дата_начала", "дата_окончания"]
# московская форма ИГАСН № 11/04: номер и дата акта ставятся от руки при подписании — пусто не ошибка
IGASN_HAND = {"номер", "дата_акта"}
ROLES_REQUIRED = ["застройщик", "лицо_осуществляющее_строительство", "строительный_контроль"]
OUTDATED = {   # ЧЕРНОВИК: замены по актуализированным редакциям
    "СНиП 3.03.01-87": "СП 70.13330.2012",
    "СНиП 3.05.01-85": "СП 73.13330.2016",
    "СНиП 3.05.06-85": "СП 76.13330.2016",
    "СНиП 3.04.01-87": "СП 71.13330.2017",
    "СНиП 2.03.01-84": "СП 63.13330.2018",
    "СНиП 3.02.01-87": "СП 45.13330.2017",
    "СНиП II-26-76": "СП 17.13330.2017",
    "СП 5.13130.2009": "СП 484.1311500.2020 и СП 485.1311500.2020",
}
# (раньше, позже): ключевые слова; сравнение в пределах одного места (оси/этаж/захватка)
ORDER = [("армирован", "бетонирован"), ("гидроизоляц", "обратн"), ("прокладк", "заделк"),
         ("устройство проход", "прокладк"), ("утеплен", "гидроизоляционного ковра")]
DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
DOC_DATE = re.compile(r"от\s+(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})")
PLACE = re.compile(r"(в осях\s+[\w\-/., ]+?)(?=,|$| на | захват)|(\d+\s*этаж)|(захватк\w*\s*\d+)|(отм\.\s*[+\-−]?\d[\d.,]*)", re.I)


def iso(s) -> str | None:
    s = str(s or "").strip()
    if DATE.fullmatch(s):
        return s
    m = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def is_igasn(a: dict) -> bool:
    form = str(a.get("форма") or "").lower()
    return "игасн" in form or "11/04" in form


def places(a: dict) -> set:
    """Места работ: оси, этаж, захватка, отметка — нормализованные строки."""
    txt = ", ".join(str(a.get(k, "")) for k in ("работы", "захватка"))   # запятая — граница «в осях …»
    return {re.sub(r"\s+", " ", "".join(m.groups(default="")).lower().strip()) for m in PLACE.finditer(txt)}


def place(a: dict) -> str:
    return " | ".join(sorted(places(a)))


def check(acts: list[dict]) -> list[dict]:
    out = []

    def add(level, act, text, fix=""):
        out.append({"уровень": level, "акт": act.get("номер", act.get("_файл", "?")), "текст": text, "как_исправить": fix})

    seen = defaultdict(int)
    for a in acts:
        seen[str(a.get("номер", "")).strip().lower()] += 1
        draft = str(a.get("статус") or "черновик") == "черновик"   # черновик: пустое — «заполнить до подписи», не критично
        igasn = is_igasn(a)
        for f in REQUIRED:
            if igasn and f in IGASN_HAND:
                continue
            if not str(a.get(f) or "").strip():
                add(MINOR if draft else CRIT, a, f"пусто: {f}", "заполнить до подписи" if draft else "")
        reps = a.get("представители") or {}
        for r in ROLES_REQUIRED:
            if not str(reps.get(r) or "").strip():
                add(MINOR if draft else CRIT, a, f"нет представителя: {r}", "заполнить до подписи" if draft else "")
        d0, d1, da = iso(a.get("дата_начала")), iso(a.get("дата_окончания")), iso(a.get("дата_акта"))
        for f, v in (("дата_начала", d0), ("дата_окончания", d1), ("дата_акта", da)):
            if a.get(f) and not v:
                add(MAJOR, a, f"{f}: не дата (ГГГГ-ММ-ДД или ДД.ММ.ГГГГ)")
        if d0 and d1 and d0 > d1:
            add(CRIT, a, "дата начала позже даты окончания")
        if d1 and da and d1 > da:
            typo = int(d1[:4]) - int(da[:4]) == 1 and da[4:] >= d1[4:]
            add(CRIT, a, "акт датирован раньше окончания работ",
                f"похоже на опечатку в годе: {da[:4]} вместо {d1[:4]}?" if typo else "дата акта — не раньше окончания работ")
        for m in a.get("материалы") or []:
            if not isinstance(m, dict):
                continue
            doc = str(m.get("документ") or "")
            if not doc.strip():
                add(MAJOR, a, f"нет документа о качестве: {m.get('наименование', '?')}")
                continue
            dm = DOC_DATE.search(doc)
            dd = iso(dm.group(1)) if dm else None
            if dd and d1 and dd > d1:
                add(CRIT, a, f"документ о качестве позже окончания работ: {doc}", "проверить дату или партию материала")
            elif dd and d0 and dd > d0:
                add(MINOR, a, f"документ о качестве выдан в ходе работ: {doc}")
        for n in a.get("нормативы") or []:
            for old, new in OUTDATED.items():
                if old.replace(" ", "").lower() in str(n).replace(" ", "").lower():
                    add(MAJOR, a, f"устаревшая норма {old}", f"заменить на {new} (ЧЕРНОВИК, сверить)")
        if not a.get("исполнительные_схемы"):
            add(MINOR, a, "нет исполнительных схем — проверьте, нужны ли для этих работ")
    for num, n in seen.items():
        if num and n > 1:
            out.append({"уровень": CRIT, "акт": num, "текст": f"номер акта повторяется {n} раза", "как_исправить": "уникальные номера"})
    by_place = defaultdict(list)
    for a in acts:
        by_place[place(a)].append(a)
    for p, group in by_place.items():
        if not p:
            continue
        for before, after in ORDER:
            first = [a for a in group if before in str(a.get("работы", "")).lower()]
            second = [a for a in group if after in str(a.get("работы", "")).lower()]
            for b in first:
                for s in second:
                    eb, ss = iso(b.get("дата_окончания")), iso(s.get("дата_начала"))
                    if eb and ss and ss < eb:
                        add(CRIT, s, f"начато раньше, чем закончено предыдущее ({b.get('номер')}) в том же месте ({p})")
    order = {CRIT: 0, MAJOR: 1, MINOR: 2}
    return sorted(out, key=lambda f: (order[f["уровень"]], str(f["акт"])))


def report(findings: list[dict], n_acts: int) -> str:
    c = defaultdict(int)
    for f in findings:
        c[f["уровень"]] += 1
    lines = [f"Проверено актов: {n_acts}. Критично: {c[CRIT]}, важно: {c[MAJOR]}, мелочь: {c[MINOR]}."]
    for f in findings:
        lines.append(f"- [{f['уровень']}] {f['акт']}: {f['текст']}" + (f" → {f['как_исправить']}" if f["как_исправить"] else ""))
    lines.append("\nПолная проверка (сверка с журналом работ, кабельными журналами, чертежами) и готовый комплект DOCX/PDF — на платформе.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    if a.json:
        data = json.loads(Path(a.json).read_text(encoding="utf-8-sig"))
        acts = data if isinstance(data, list) else [data]
        print(report(check(acts), len(acts)))
        return 0
    from common import engineer_dir, load_acts, load_config, log_event
    cfg = load_config()
    folder = Path(a.folder) if a.folder else engineer_dir(cfg)
    acts = load_acts(folder)
    fs = check(acts)
    print(report(fs, len(acts)))
    if not a.folder:
        log_event(cfg, "проверка", актов=len(acts), замечания=fs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
