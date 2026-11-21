"""Сканер рабочих папок инженера: что уже сделано и в каком состоянии — для начальника ПТО.

python scan.py                 — папки из настроек (setup.py --work ...), снимок в <команда>/<инженер>/состояние.json
python scan.py --folder ПАПКА  — разово по указанной папке (снимок печатается, в команду не пишется)
python scan.py --text акт.docx — текст Word/Excel: собрать из него JSON акта и проверить (check.py --json)
Ничего не меняет в рабочих папках: только читает. Тип, номер, дата, система, захватка и статус
определяются по имени файла, пути и тексту (Word/Excel) — это догадка, спорное помечено «?».
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

EXT = {".docx", ".doc", ".pdf", ".xlsx", ".xls", ".dwg", ".dxf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".odt"}
SKIP_DIRS = {"$recycle.bin", ".git", "__pycache__", "node_modules", "~$"}
KINDS = [  # (тип, регулярка по имени/пути/началу текста) — порядок важен
    ("АООК", r"аоок|ответственных\s+конструкц"),
    ("АОСР", r"аоср|освидетельствовани\w*\s+скрыт|скрыт\w*\s+работ"),
    ("акт испытаний", r"испытани|опрессовк|промывк|протокол\s+измерен|\bпнр\b|пусконаладк"),
    ("акт готовности", r"готовност"),
    ("исполнительная схема", r"(^|[\s_\-/])(ис|игс)[\s_\-№.]|исполнительн\w*\s+(схем|съемк|чертеж)"),
    ("документ о качестве", r"паспорт|сертификат|декларац|документ\w*\s+о\s+качеств|отказн|пожарн\w*\s+сертиф"),
    ("ведомость", r"ведомост|спецификац"),
    ("реестр", r"реестр|опись"),
    ("журнал", r"журнал|ожр|кабельн\w*\s+журн"),
    ("приказ", r"приказ|распоряжени"),
    ("письмо", r"письм|исх\.?\s*№|сопроводит"),
    ("чертёж", r"\.(dwg|dxf)$|чертеж|лист\s*\d|\bрд\b|\bпд\b"),
]
SYSTEMS = ["СОУЭ", "СПС", "АПС", "АУПТ", "СКУД", "СОТ", "СКС", "ЭОМ", "ЭМ", "ЭО", "ЭС", "ОВ", "ВК", "ВН", "АР", "КЖ", "КМ",
           "КР", "ТМ", "ИТП", "АСУД", "АИИСКУЭ", "СС", "НВК", "НК", "ГП", "ДУ", "ПС"]
ZONE = re.compile(r"(\d+)\s*[-_]?\s*(?:й\s*)?этаж|этаж\s*[-_№]?\s*(\d+)|секци\w*\s*[-_№]?\s*(\d+)|захватк\w*\s*[-_№]?\s*(\d+)|(подвал|кровл\w*|паркинг|техэтаж)", re.I)
NUM = re.compile(r"№\s*([A-Za-zА-Яа-я0-9][\w\-./]*)|((?:АОСР|АООК|ИС|ИГС)[\s\-_]*[\w\-./]*\d)", re.I)
DATE = re.compile(r"(\d{2})[.\-_](\d{2})[.\-_](20\d{2})|(20\d{2})-(\d{2})-(\d{2})")
_W = r"(?<![а-яёa-z])"            # начало слова: «old» внутри «Holder» не черновик
SIGNED = re.compile(_W + r"(подписан|скан|сдан|сдача|утвержд|согласован)", re.I)
DRAFT = re.compile(_W + r"(черновик|draft|проект\s+акта|на\s+подпис|в\s+работе|правк|старое|old|копия)(?![a-z])", re.I)


def docx_text(p: Path, limit=4000) -> str:
    try:
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return ""
    xml = re.sub(r"</w:p>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))[:limit]


def xlsx_text(p: Path, limit=4000) -> str:
    try:
        with zipfile.ZipFile(p) as z:
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace") if "xl/sharedStrings.xml" in z.namelist() else ""
    except (zipfile.BadZipFile, OSError):
        return ""
    return html.unescape(" ".join(re.findall(r"<t[^>]*>([^<]*)</t>", xml)))[:limit]


def pdf_is_scan(p: Path) -> bool | None:
    """Скан (картинки без шрифтов) — обычно бумага с подписями; PDF со шрифтами — выгрузка из Word. None — не понять."""
    try:
        data = p.read_bytes()[:20_000_000]
    except OSError:
        return None
    fonts, images = b"/Font" in data, b"/Image" in data
    if images and not fonts:
        return True
    if fonts:
        return False
    return None


def pdf_pages(p: Path) -> int | None:
    try:
        data = p.read_bytes()[:20_000_000]
    except OSError:
        return None
    n = len(re.findall(rb"/Type\s*/Page[^s]", data))
    return n or None


def classify(rel: str, text: str) -> str:
    probe = (rel + "\n" + text[:600]).lower().replace("ё", "е")
    for kind, rx in KINDS:
        if re.search(rx, probe, re.I):
            return kind
    return "прочее"


def find_system(rel: str, text: str) -> str:
    probe = rel + " " + text[:600]
    for s in SYSTEMS:
        if re.search(rf"(?<![А-ЯA-Zа-я]){s}(?![А-ЯA-Zа-я])", probe):
            return s
    return ""


def find_zone(rel: str) -> str:
    m = ZONE.search(rel)
    if not m:
        return ""
    g = m.groups()
    if g[0] or g[1]:
        return f"{g[0] or g[1]} этаж"
    if g[2]:
        return f"секция {g[2]}"
    if g[3]:
        return f"захватка {g[3]}"
    return g[4].lower()


def find_date(rel: str, text: str) -> str:
    for src in (rel, text[:1500]):
        m = DATE.search(src)
        if m:
            g = m.groups()
            return f"{g[2]}-{g[1]}-{g[0]}" if g[0] else f"{g[3]}-{g[4]}-{g[5]}"
    return ""


def find_number(name: str, text: str) -> str:
    for src in (name, text[:800]):
        m = NUM.search(src)
        if m:
            return (m.group(1) or m.group(2)).strip(" ._-")
    return ""


def status(rel: str, ext: str, has_pdf_twin: bool, scan: bool | None = None) -> str:
    """Догадка по имени и виду файла. Подписанным считаем только явное слово в пути или скан PDF;
    PDF из Word без скана — «на подписи?» (выгрузили, но подписей не видно)."""
    if DRAFT.search(rel):
        return "в работе"
    if SIGNED.search(rel) or (ext == ".pdf" and scan is True):
        return "подписан?"
    if has_pdf_twin or ext == ".pdf":
        return "на подписи?"
    return "в работе"


def scan_folder(root: Path, max_files: int = 5000) -> list[dict]:
    files = []
    for p in root.rglob("*"):
        if len(files) >= max_files:
            break
        if not p.is_file() or p.suffix.lower() not in EXT or p.name.startswith("~$"):
            continue
        if any(part.lower() in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        files.append(p)
    stems = Counter(f.with_suffix("").as_posix().lower() for f in files)
    pdf_stems = {f.with_suffix("").as_posix().lower() for f in files if f.suffix.lower() == ".pdf"}
    out = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        ext = p.suffix.lower()
        text = docx_text(p) if ext == ".docx" else xlsx_text(p) if ext == ".xlsx" else ""
        kind = classify(rel, text)
        stem = p.with_suffix("").as_posix().lower()
        st = p.stat()
        rec = {"файл": rel, "тип": kind, "номер": find_number(p.stem, text), "дата": find_date(rel, text),
               "система": find_system(rel, text), "захватка": find_zone(rel),
               "статус": status(rel, ext, ext != ".pdf" and stem in pdf_stems, pdf_is_scan(p) if ext == ".pdf" else None) if kind in ("АОСР", "АООК", "акт испытаний", "акт готовности", "исполнительная схема") else "",
               "изменён": int(st.st_mtime), "размер": st.st_size,
               "хэш": hashlib.sha1(f"{rel}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:12]}
        if ext == ".pdf":
            rec["страниц"] = pdf_pages(p)
        if kind in ("АОСР", "АООК") and text:
            m = re.search(r"(?:работ[ыа]?|наименование работ)[^\n:]*[:\n]\s*([^\n]{10,200})", text, re.I)
            if m:
                rec["работы"] = m.group(1).strip()
        out.append(rec)
    return out


def summarize(files: list[dict]) -> dict:
    acts = [f for f in files if f["тип"] in ("АОСР", "АООК", "акт испытаний", "акт готовности")]
    return {"файлов": len(files), "по_типам": dict(Counter(f["тип"] for f in files).most_common()),
            "актов": len(acts), "по_статусам": dict(Counter(f["статус"] for f in acts if f["статус"])),
            "системы": sorted({f["система"] for f in files if f["система"]}),
            "захватки": sorted({f["захватка"] for f in files if f["захватка"]})}


def diff(old: list[dict], new: list[dict]) -> dict:
    o = {f["файл"]: f["хэш"] for f in old}
    n = {f["файл"]: f["хэш"] for f in new}
    return {"новые": sorted(set(n) - set(o)), "изменённые": sorted(k for k in n if k in o and n[k] != o[k]),
            "удалённые": sorted(set(o) - set(n))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder")
    ap.add_argument("--text", help="напечатать текст Word/Excel (чтобы собрать JSON акта и проверить его)")
    a = ap.parse_args(argv)
    if a.text:
        p = Path(a.text)
        ext = p.suffix.lower()
        txt = docx_text(p, 60000) if ext == ".docx" else xlsx_text(p, 60000) if ext == ".xlsx" else ""
        print(txt or "Текст не прочитан: нужен .docx или .xlsx (PDF и сканы — прочитай сам или попроси Word-версию).")
        return 0 if txt else 2
    if a.folder:
        files = scan_folder(Path(a.folder))
        print(json.dumps({"итоги": summarize(files), "файлы": files[:50]}, ensure_ascii=False, indent=1))
        return 0
    from common import engineer_dir, load_config, log_event, now
    cfg = load_config()
    roots = [Path(x) for x in cfg.get("work_folders") or []]
    if not roots:
        print("Рабочие папки не заданы: python setup.py ... --work \"путь\" [--work \"путь2\"]"); return 2
    files = []
    for r in roots:
        if not r.is_dir():
            print(f"нет папки: {r}"); continue
        for f in scan_folder(r):
            f["папка"] = r.name
            files.append(f)
    d = engineer_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    snap_path = d / "состояние.json"
    old = json.loads(snap_path.read_text(encoding="utf-8")).get("файлы", []) if snap_path.exists() else []
    ch = diff(old, files)
    snap = {"инженер": cfg["engineer"], "обновлено": now(), "итоги": summarize(files), "изменения": ch, "файлы": files}
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    log_event(cfg, "скан", итоги=snap["итоги"], изменения={k: len(v) for k, v in ch.items()},
              новые=ch["новые"][:200], изменённые=ch["изменённые"][:200])
    s = snap["итоги"]
    print(f"Файлов: {s['файлов']}, актов: {s['актов']} {s['по_статусам']}; системы: {', '.join(s['системы']) or '—'}; "
          f"захватки: {', '.join(s['захватки']) or '—'}")
    print(f"С прошлого раза: новых {len(ch['новые'])}, изменённых {len(ch['изменённые'])}, удалённых {len(ch['удалённые'])}")
    print(f"Снимок для начальника: {snap_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
