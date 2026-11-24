"""Выгрузка чата одним архивом: журнал чата, оригиналы загруженных файлов, результаты.

python chat_export.py [--из ПАПКА_ЗАГРУЗОК] [--в ПАПКА_РЕЗУЛЬТАТОВ]
По умолчанию ищет /mnt/user-data/uploads и /mnt/user-data/outputs (так в чате claude.ai), иначе ./uploads и ./outputs.
Архив выгрузка_чата_<дата>.zip кладётся в папку результатов — пользователь скачивает его сам. Ничего не отправляет.
Только стандартная библиотека Python.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import zipfile
from pathlib import Path

UPLOAD_CANDIDATES = ("/mnt/user-data/uploads", "/mnt/data", "uploads")
OUTPUT_CANDIDATES = ("/mnt/user-data/outputs", "outputs", ".")
LIMIT = 400 * 1024 * 1024                    # больше — в список «не вошло»


def first_dir(cands) -> Path | None:
    for c in cands:
        p = Path(c)
        if p.is_dir():
            return p
    return None


def build(uploads: Path | None, outputs: Path) -> tuple[Path, dict]:
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    target = outputs / f"выгрузка_чата_{stamp}.zip"
    budget, st = LIMIT, {"загружено": 0, "результатов": 0, "не_вошло": []}
    files = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for label, root in (("загружено", uploads), ("результаты", outputs)):
            if root is None:
                continue
            for f in sorted(root.rglob("*")):
                if not f.is_file() or f == target or f.name.startswith("выгрузка_чата_"):
                    continue
                size = f.stat().st_size
                if size > budget:
                    st["не_вошло"].append(f"{label}/{f.name}")
                    continue
                budget -= size
                arc = f"{label}/{f.relative_to(root).as_posix()}"
                zf.write(f, arc)
                st[label if label == "загружено" else "результатов"] += 1
                files.append({"файл": arc, "байт": size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()})
        zf.writestr("опись.json", json.dumps({"собрано": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                                              "файлы": files, "не_вошло": st["не_вошло"]}, ensure_ascii=False, indent=1))
    return target, st


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--из", dest="src")
    ap.add_argument("--в", dest="dst")
    a = ap.parse_args(argv)
    uploads = Path(a.src) if a.src else first_dir(UPLOAD_CANDIDATES)
    outputs = Path(a.dst) if a.dst else first_dir(OUTPUT_CANDIDATES)
    outputs.mkdir(parents=True, exist_ok=True)
    if not (outputs / "журнал_чата.md").exists():
        print("ВНИМАНИЕ: нет журнал_чата.md в папке результатов — допиши его перед выгрузкой.")
    target, st = build(uploads, outputs)
    print(f"Архив: {target}")
    print(f"Загруженных файлов: {st['загружено']}, результатов: {st['результатов']}"
          + (f", не вошло по размеру: {len(st['не_вошло'])}" if st["не_вошло"] else ""))
    print("Дайте пользователю ссылку на скачивание архива. Скилл ничего никуда не отправляет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
