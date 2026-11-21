r"""История объекта: исходники и замечания по датам, версии сдачи и что в них менялось, вопросы.

python history.py ПАПКА_ОБЪЕКТА [--year 2026] [--out история.html]

Ждёт в папке объекта: «Исходники\<что> <дд.мм>\…» (папки с датой в имени), «Сдача <дд.мм>.zip» (прошлые
версии сдачи), «Сдача\» (текущая), «вопросы\» (наши вопросы с картинками). Ничего не читает внутри файлов:
только имена, даты и контрольные суммы (crc в zip, размер+время для папки). Этаж и система — по имени файла.
Рядом пишет история.json (данные) и история.html (шкала времени и таблица «этаж × система × версия»).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import zipfile
from pathlib import Path

DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{2})(?:\.(\d{2,4}))?(?!\d)")
KINDS = [(re.compile(r"заказчик", re.I), "заказчик"), (re.compile(r"пометк|замечан", re.I), "замечания"),
         (re.compile(r"фото|тг\b|телефон", re.I), "фото"), (re.compile(r"\bкж\b|кабельн", re.I), "кабельный журнал"),
         (re.compile(r"\bид\b|паспорт", re.I), "документы"), (re.compile(r"пример", re.I), "образец")]
FLOOR = [(re.compile(r"абк\s*-?\s*(\d)", re.I), "АБК {} этаж"), (re.compile(r"(?<![\d.])(\d{1,2})\s*-?\s*(?:й\s*)?этаж", re.I), "{} этаж"),
         (re.compile(r"(?:^|[\\/_ ])f(\d{1,2})[_ ]", re.I), "{} этаж"), (re.compile(r"(?:^|[\\/_ ])abk(\d)", re.I), "АБК {} этаж"),
         (re.compile(r"стояк", re.I), "Стояки"), (re.compile(r"охран", re.I), "Помещение охраны")]
SYSTEMS = [(re.compile(r"сп[сc]|_sps", re.I), "СПС"), (re.compile(r"соуэ|_soue", re.I), "СОУЭ")]


def _date(text: str, year: int, fallback: float | None = None) -> str | None:
    m = DATE.search(text)
    if m:
        d, mo, y = int(m[1]), int(m[2]), m[3]
        y = int(y) + (2000 if y and len(y) == 2 else 0) if y else year
        try:
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            pass
    return dt.date.fromtimestamp(fallback).isoformat() if fallback else None


def _kind(name: str) -> str:
    return next((k for rx, k in KINDS if rx.search(name)), "исходники")


def tags(path: str) -> tuple[list[str], list[str]]:
    floors, systems = [], []
    for rx, fmt in FLOOR:
        for m in rx.finditer(path):
            f = fmt.format(*m.groups()) if m.groups() else fmt
            if f not in floors:
                floors.append(f)
        if floors and rx.pattern.startswith("абк"):
            break                                   # «АБК 2 этаж» не дублировать как «2 этаж»
    for rx, s in SYSTEMS:
        if rx.search(path):
            systems.append(s)
    return floors, systems


def _deliveries(root: Path, year: int) -> list[dict]:
    """Версии сдачи: zip с датой в имени + текущая папка «Сдача». files: путь → отпечаток."""
    out = []
    for z in sorted(root.glob("Сдача*.zip")):
        date = _date(z.stem, year)
        if not date:
            continue                                 # «Сдача.zip» без даты — копия текущей
        with zipfile.ZipFile(z) as zf:
            files = {i.filename.split("/", 1)[-1] if i.filename.startswith("Сдача/") else i.filename: f"{i.CRC:08x}"
                     for i in zf.infolist() if not i.is_dir()}
        out.append({"версия": date, "источник": z.name, "файлы": files})
    cur = root / "Сдача"
    if cur.is_dir():
        files = {}
        for f in cur.rglob("*"):
            if f.is_file():
                st = f.stat()
                files[f.relative_to(cur).as_posix()] = f"{st.st_size}:{int(st.st_mtime)}"
        last = max((f.stat().st_mtime for f in cur.rglob("*") if f.is_file()), default=None)
        out.append({"версия": dt.date.fromtimestamp(last).isoformat() if last else "текущая",
                    "источник": "Сдача (текущая)", "файлы": files})
    out.sort(key=lambda v: v["версия"])
    return out


def _diff(prev: dict | None, cur: dict) -> dict:
    """Изменения версии сдачи: new/changed/removed. В zip отпечаток — crc, в папке — размер+время:
    сравнение zip↔папка по размеру невозможно, поэтому для текущей папки «изменено» = другое имя или нет в zip."""
    if prev is None:
        return {"новые": sorted(cur), "изменены": [], "удалены": []}
    comparable = all(":" not in v for v in list(prev.values())[:1] + list(cur.values())[:1])
    new = sorted(set(cur) - set(prev))
    changed = sorted(p for p in set(cur) & set(prev) if comparable and cur[p] != prev[p])
    return {"новые": new, "изменены": changed, "удалены": sorted(set(prev) - set(cur))}


def collect(root: Path, year: int) -> dict:
    events = []
    src = root / "Исходники"
    if src.is_dir():
        for d in sorted(p for p in src.iterdir()):
            files = [f for f in (d.rglob("*") if d.is_dir() else [d]) if f.is_file()]
            if not files:
                continue
            first = min(f.stat().st_mtime for f in files)
            date = _date(d.name, year, first)
            fl, sy = set(), set()
            for f in files:
                a, b = tags(str(f.relative_to(src)))
                fl.update(a)
                sy.update(b)
            events.append({"дата": date, "вид": _kind(d.name), "что": d.name, "файлов": len(files),
                           "этажи": sorted(fl), "системы": sorted(sy),
                           "файлы": [f.relative_to(root).as_posix() for f in files][:200]})
    versions = _deliveries(root, year)
    prev = None
    for v in versions:
        diff = _diff(prev, v["файлы"])
        touched = diff["новые"] + diff["изменены"]
        fl, sy = set(), set()
        for p in touched:
            a, b = tags(p)
            fl.update(a)
            sy.update(b)
        zipped = v["источник"].lower().endswith(".zip")
        events.append({"дата": v["версия"], "вид": "сдача", "что": v["источник"], "файлов": len(v["файлы"]),
                       "этажи": sorted(fl), "системы": sorted(sy), "изменения": {k: len(x) for k, x in diff.items()},
                       # промежуточная версия — одной ссылкой на её архив, файлы внутри только списком;
                       # текущая — ссылки на файлы папки «Сдача». В визуализацию ничего не подгружается.
                       "архив": v["источник"] if zipped else None,
                       "файлы": touched[:300] if zipped else [f"Сдача/{t}" for t in touched[:300]]})
        v["изменения"] = diff
        prev = v["файлы"]
    q = root / "вопросы"
    if q.is_dir():
        for f in sorted(q.iterdir()):
            if f.is_file():
                a, b = tags(f.name)
                events.append({"дата": dt.date.fromtimestamp(f.stat().st_mtime).isoformat(), "вид": "вопрос",
                               "что": f.name, "файлов": 1, "этажи": a, "системы": b,
                               "файлы": [f.relative_to(root).as_posix()]})
    events.sort(key=lambda e: (e["дата"] or "", e["вид"]))
    # таблица «этаж × система × версия»: что было в каждой сдаче
    matrix = {}
    for v in versions:
        for p in v["файлы"]:
            fl, sy = tags(p)
            for f in fl or ["без этажа"]:
                for s in sy or ["общее"]:
                    cell = matrix.setdefault(f, {}).setdefault(s, {})
                    state = ("новое" if p in v["изменения"]["новые"] else
                             "изменено" if p in v["изменения"]["изменены"] else "без изменений")
                    rank = {"новое": 2, "изменено": 1, "без изменений": 0}
                    if rank[state] >= rank.get(cell.get(v["версия"]), -1):
                        cell[v["версия"]] = state
    return {"объект": root.name, "обновлено": dt.datetime.now().strftime("%d.%m.%Y %H:%M"), "события": events,
            "версии": [v["версия"] for v in versions], "таблица": matrix}


def render(h: dict, base: str = "") -> str:
    """base — путь от страницы до папки объекта: ссылки «скачать» относительные, файлы не встраиваются."""
    data = json.dumps({**h, "база": base}, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__TITLE__", html.escape(h["объект"])).replace("__DATA__", data)


TEMPLATE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>История ИД — __TITLE__</title><style>
:root{--bg:#f4f5f7;--fg:#1d232b;--muted:#5d6876;--card:#fff;--line:#d7dce3;--new:#2f8f5b;--chg:#c48a12;--same:#9aa4b0}
@media (prefers-color-scheme:dark){:root{--bg:#13171c;--fg:#e6eaef;--muted:#9aa6b4;--card:#1c222a;--line:#2f3741;--new:#4cb57c;--chg:#e0a93a;--same:#6b7684}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif;padding:16px}
h1{font-size:20px;margin:0}h2{font-size:16px;margin:20px 0 8px}.muted{color:var(--muted)}
.wrap{overflow-x:auto}table{border-collapse:separate;border-spacing:3px}th{text-align:left;font-weight:600;padding:3px 8px;white-space:nowrap}
td.c{min-width:78px;height:30px;border-radius:6px;text-align:center;color:#fff;font-size:12px;cursor:pointer}
.новое{background:var(--new)}.изменено{background:var(--chg)}.без{background:var(--same)}td.e{border:1px dashed var(--line);border-radius:6px}
.ev{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:6px 0;cursor:pointer}
.tag{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:0 8px;margin:2px 4px 0 0;font-size:12px}
.k-заказчик{border-left:4px solid #b9472f}.k-замечания{border-left:4px solid #c48a12}.k-сдача{border-left:4px solid #2f6fb3}.k-вопрос{border-left:4px solid #7a5ab3}
.files{display:none;font-size:12px;color:var(--muted);margin-top:6px;white-space:pre-wrap}.ev.open .files{display:block}
select{margin-left:8px}
</style></head><body><h1>История ИД — __TITLE__</h1><div class="muted" id="u"></div>
<h2>Что было в каждой сдаче</h2><div class="muted">зелёный — впервые, жёлтый — переделано, серый — без изменений</div>
<div class="wrap"><table id="t"></table></div>
<h2>Хронология <select id="f"><option value="">все этажи</option></select></h2><div id="ev"></div>
<script>
const H=__DATA__;document.getElementById('u').textContent='Обновлено '+H["обновлено"]+' · по именам файлов и датам, содержимое не читалось';
const t=document.getElementById('t'),V=H["версии"],T=H["таблица"];
const hr=t.insertRow();for(const h of ['Этаж','Система',...V]){const th=document.createElement('th');th.textContent=h;hr.appendChild(th)}
const order=k=>{const m=k.match(/(\\d+)/);return (k.startsWith('АБК')?100:0)+(m?+m[1]:200)};
for(const f of Object.keys(T).sort((a,b)=>order(a)-order(b)))for(const s of Object.keys(T[f]).sort()){
 const tr=t.insertRow();[f,s].forEach(x=>{const th=document.createElement('th');th.textContent=x;tr.appendChild(th)});
 for(const v of V){const td=tr.insertCell();const st=T[f][s][v];if(!st){td.className='e';continue}
  td.className='c '+(st==='без изменений'?'без':st);td.textContent=st==='без изменений'?'=':st;
  td.onclick=()=>{document.getElementById('f').value=f;draw()}}}
const sel=document.getElementById('f');for(const f of Object.keys(T).sort((a,b)=>order(a)-order(b))){const o=document.createElement('option');o.value=f;o.textContent=f;sel.appendChild(o)}
sel.onchange=draw;
function draw(){const box=document.getElementById('ev');box.replaceChildren();const f=sel.value;
 for(const e of H["события"]){if(f&&!e["этажи"].includes(f))continue;
  const d=document.createElement('div');d.className='ev k-'+e["вид"];
  const head=document.createElement('div');const b=document.createElement('b');b.textContent=(e["дата"]||'?')+' · '+e["вид"];
  head.append(b,' — '+e["что"]+' ('+e["файлов"]+' файл.)');
  if(e["изменения"])head.append(' · новых '+e["изменения"]["новые"]+', изменено '+e["изменения"]["изменены"]+', удалено '+e["изменения"]["удалены"]);
  d.appendChild(head);
  for(const x of e["этажи"].concat(e["системы"])){const s=document.createElement('span');s.className='tag';s.textContent=x;d.appendChild(s)}
  const fl=document.createElement('div');fl.className='files';
  const url=p=>H["база"]+p.split('/').map(encodeURIComponent).join('/');
  const link=(p,text)=>{const a=document.createElement('a');a.href=url(p);a.download='';a.textContent=text;a.onclick=ev=>ev.stopPropagation();return a};
  if(e["архив"]){fl.append(link(e["архив"],'скачать эту версию ('+e["архив"]+')'),'\\n'+e["файлы"].join('\\n'))}
  else for(const p of e["файлы"]){fl.append(link(p,'скачать'),' '+p+'\\n')}
  d.appendChild(fl);
  d.onclick=()=>d.classList.toggle('open');box.appendChild(d)}}
draw();
</script></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    root = Path(a.root)
    if not root.is_dir():
        print(f"нет папки {root}", file=sys.stderr)
        return 2
    h = collect(root, a.year)
    out = Path(a.out) if a.out else root / "история.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    import os
    base = os.path.relpath(root.resolve(), out.parent.resolve()).replace(os.sep, "/")
    out.write_text(render(h, "" if base == "." else base + "/"), encoding="utf-8")
    kinds = {}
    for e in h["события"]:
        kinds[e["вид"]] = kinds.get(e["вид"], 0) + 1
    print(f"событий {len(h['события'])}: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    print(f"версии сдачи: {', '.join(h['версии'])}")
    print(f"история: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
