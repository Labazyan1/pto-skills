"""Уборка: дубли и копии в папках — только список «можно удалить», сам скрипт ничего не удаляет.

python cleanup.py                  — рабочие папки инженера (из настроек) и папка команды
python cleanup.py ПАПКА [ПАПКА…]   — указанные папки
Находит: побайтно одинаковые файлы (оставить один), архив .zip, который целиком повторяет папку рядом,
временные файлы Office (~$…), пустые папки. Что из дублей оставить, решает человек.
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

SKIP = {"$recycle.bin", ".git", "__pycache__", "node_modules", ".tmp.drivedownload", ".tmp.driveupload"}
MIN_SIZE = 1024                      # мелочь (пустые, ярлыки) не считаем


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(roots: list[Path], max_files: int = 50000) -> list[Path]:
    out = []
    for r in roots:
        for p in r.rglob("*"):
            if len(out) >= max_files:
                return out
            try:
                if p.is_file() and not any(part.lower() in SKIP for part in p.parts):
                    out.append(p)
            except OSError:
                continue
    return out


def duplicates(files: list[Path]) -> list[list[Path]]:
    by_size = defaultdict(list)
    for f in files:
        try:
            s = f.stat().st_size
        except OSError:
            continue
        if s >= MIN_SIZE and not f.name.startswith("~$"):
            by_size[s].append(f)
    groups = []
    for same in by_size.values():
        if len(same) < 2:
            continue
        by_hash = defaultdict(list)
        for f in same:
            try:
                by_hash[_sha(f)].append(f)
            except OSError:
                continue
        groups += [sorted(g, key=lambda p: (len(p.parts), str(p))) for g in by_hash.values() if len(g) > 1]
    return sorted(groups, key=lambda g: -g[0].stat().st_size)


def zip_copies(files: list[Path]) -> list[tuple[Path, Path]]:
    """Архив, все файлы которого лежат рядом в папке с тем же содержимым (как «Сдача.zip» и папка «Сдача»)."""
    out = []
    for z in (f for f in files if f.suffix.lower() == ".zip"):
        folder = z.with_suffix("")
        if not folder.is_dir():
            continue
        try:
            with zipfile.ZipFile(z) as zf:
                items = [i for i in zf.infolist() if not i.is_dir()]
                same = bool(items)
                for i in items:
                    name = i.filename.split("/", 1)[1] if i.filename.startswith(folder.name + "/") else i.filename
                    target = folder / name
                    if not target.is_file() or target.stat().st_size != i.file_size \
                            or hashlib.sha256(zf.read(i)).hexdigest() != _sha(target):
                        same = False
                        break
        except (zipfile.BadZipFile, OSError, RuntimeError):
            continue
        if same:
            out.append((z, folder))
    return out


def mb(n: int) -> str:
    return f"{n / 1e6:.1f} МБ"


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cfg = None
    if args:
        roots = [Path(a) for a in args]
    else:
        from common import load_config
        cfg = load_config()
        roots = [Path(x) for x in cfg.get("work_folders") or []] + [Path(cfg["team_folder"])]
    roots = [r for r in dict.fromkeys(roots) if r.is_dir()]
    if not roots:
        print("Нет папок для проверки"); return 2
    files = walk(roots)
    groups = duplicates(files)
    zips = zip_copies(files)
    temp = [f for f in files if f.name.startswith("~$")]
    saved = sum(g[0].stat().st_size * (len(g) - 1) for g in groups) + sum(z.stat().st_size for z, _ in zips)
    print(f"Проверено файлов: {len(files)}. Можно освободить около {mb(saved)}. Сам ничего не удаляю.")
    for z, folder in zips:
        print(f"- архив повторяет папку целиком, можно удалить архив: {z} ({mb(z.stat().st_size)}) = {folder}")
    for g in groups[:100]:
        print(f"- одинаковые файлы ({mb(g[0].stat().st_size)} каждый), оставить один:")
        for f in g:
            print(f"    {f}")
    if len(groups) > 100:
        print(f"…и ещё групп дублей: {len(groups) - 100}")
    if temp:
        print(f"- временные файлы Office (~$…), можно удалить, если документ закрыт: {len(temp)}")
    if cfg and cfg.get("role") != "начальник":
        from common import log_event
        log_event(cfg, "уборка", групп_дублей=len(groups), архивов=len(zips), мб=round(saved / 1e6, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
