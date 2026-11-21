"""Журнал работ (Excel/CSV) → черновики АОСР по скрытым работам и сверка «журнал ↔ акты».

python journal.py import журнал.xlsx [--sheet ИМЯ]   — черновики в <папка инженера>/черновики/*.json
python journal.py compare журнал.xlsx                — скрытые работы из журнала, по которым нет акта
Список «какие работы скрытые» — базовый (ЧЕРНОВИК); спорное решает инженер.
Черновик — не акт: Claude дополняет его (материалы, документы, схемы, представители) и сохраняет act.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from check import iso, places

# основа слова в описании работ → это скрытая работа (нужен АОСР). ЧЕРНОВИК.
HIDDEN = ["армирован", "бетонирован", "гидроизоляц", "пароизоляц", "утеплен", "теплоизоляц", "обратн засыпк",
          "уплотнен грунт", "устройство основани", "подготовк", "стяжк", "закладн", "анкер", "огнезащит",
          "прокладк", "заделк", "проход", "заземлен", "монтаж трубопровод", "монтаж воздуховод", "грунтовани",
          "антикоррозийн", "сварк", "устройство кровл", "устройство опалубк"]
_NOT = re.compile(r"\b(демонтаж|вывоз|уборк|разгрузк|доставк)", re.I)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).lower().replace("ё", "е"))


def hidden_kind(text: str) -> str | None:
    t = norm(text)
    if _NOT.search(t):
        return None
    for k in HIDDEN:
        if all(part in t for part in k.split()):
            return k
    return None


def find_columns(rows: list[list[str]]) -> tuple[int, dict]:
    """Строка шапки и номера колонок: дата, работы, место (необязательно)."""
    for i, r in enumerate(rows[:20]):
        low = [norm(c) for c in r]
        works = next((j for j, c in enumerate(low) if "работ" in c and "дат" not in c), None)
        date = next((j for j, c in enumerate(low) if "дат" in c), None)
        if works is not None and date is not None:
            where = next((j for j, c in enumerate(low) if any(w in c for w in ("мест", "захватк", "оси", "этаж", "участ"))), None)
            return i, {"дата": date, "работы": works, "место": where}
    raise SystemExit("Не нашёл шапку журнала: нужны колонки с «дата» и «работ» в первых 20 строках")


def entries(path: str, sheet: str | None = None) -> list[dict]:
    from xlsx_read import read_csv_or_xlsx, read_rows
    rows = read_rows(path, sheet) if sheet else read_csv_or_xlsx(path)
    h, cols = find_columns(rows)
    out = []
    for n, r in enumerate(rows[h + 1:], h + 2):
        get = lambda k: (r[cols[k]].strip() if cols[k] is not None and cols[k] < len(r) else "")
        works = get("работы")
        if not works:
            continue
        where = get("место")
        out.append({"строка": n, "дата": iso(get("дата")) or get("дата"), "работы": works + (f", {where}" if where and where.lower() not in works.lower() else ""),
                    "вид": hidden_kind(works)})
    return out


def group(rows: list[dict]) -> list[dict]:
    """Одна работа на несколько дней (одинаковый текст) → одна запись с периодом."""
    g: dict[str, dict] = {}
    for e in rows:
        if not e["вид"]:
            continue
        k = norm(e["работы"])
        x = g.setdefault(k, {**e, "начало": e["дата"], "окончание": e["дата"], "строки": []})
        x["строки"].append(e["строка"])
        if e["дата"] and (not x["начало"] or e["дата"] < x["начало"]):
            x["начало"] = e["дата"]
        if e["дата"] and (not x["окончание"] or e["дата"] > x["окончание"]):
            x["окончание"] = e["дата"]
    return list(g.values())


def covered(work: dict, acts: list[dict]) -> str | None:
    """Номер акта, который закрывает работу: тот же вид и (то же место или дата внутри периода акта)."""
    wp = places({"работы": work["работы"]})
    for a in acts:
        if hidden_kind(str(a.get("работы", ""))) != work["вид"]:
            continue
        ap_ = places(a)
        a0, a1 = iso(a.get("дата_начала")), iso(a.get("дата_окончания"))
        same_place = bool(wp and ap_ and wp <= ap_)   # место из журнала входит в места акта
        in_dates = work["начало"] and a0 and a1 and a0 <= work["начало"] <= a1
        if same_place or (in_dates and not (wp and ap_)):
            return str(a.get("номер"))
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["import", "compare"])
    ap.add_argument("file")
    ap.add_argument("--sheet")
    ap.add_argument("--folder")
    a = ap.parse_args(argv)
    from common import engineer_dir, load_acts, load_config, log_event, safe_name
    cfg = None if a.folder else load_config()
    folder = Path(a.folder) if a.folder else engineer_dir(cfg)
    works = group(entries(a.file, a.sheet))
    if a.cmd == "import":
        d = folder / "черновики"
        d.mkdir(parents=True, exist_ok=True)
        for i, w in enumerate(works, 1):
            draft = {"номер": "", "работы": w["работы"], "дата_начала": w["начало"], "дата_окончания": w["окончание"],
                     "дата_акта": w["окончание"], "материалы": [], "исполнительные_схемы": [], "нормативы": [],
                     "представители": {}, "статус": "черновик", "_источник": f"журнал работ, строки {w['строки']}"}
            (d / f"{i:03d} {safe_name(w['работы'][:50])}.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Скрытых работ: {len(works)} → черновики в {d}")
        if cfg:
            log_event(cfg, "сводка", заметка="импорт журнала работ", черновиков=len(works))
        return 0
    acts = load_acts(folder)
    gaps = [w for w in works if not covered(w, acts)]
    print(f"Скрытых работ в журнале: {len(works)}, без акта: {len(gaps)}")
    for w in gaps:
        print(f"- {w['начало']}…{w['окончание']}: {w['работы']} (строки {w['строки']})")
    if cfg:
        log_event(cfg, "проверка", заметка="сверка с журналом работ", работ=len(works), без_акта=len(gaps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
