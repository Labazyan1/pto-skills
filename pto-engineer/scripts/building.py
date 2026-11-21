"""Здание одной командой: корпуса, этажи стопкой, цвет по готовности ИД, чего ждём. Открывается само.

python building.py                         — всё из настроек скилла (папка команды), пишет здание.html и открывает
python building.py ПАПКА_СДАЧИ [--team ПАПКА_КОМАНДЫ] [--out здание.html] [--floors "Основной:1-8;АБК:1-5"]
                   [--systems "СПС,СОУЭ"] [--no-open]

Откуда данные (что найдётся, то и берём, ничего настраивать не надо):
- акты инженеров в папке команды (`<папка>/<инженер>/акты/*.json` и скан рабочих папок): захватка → этаж,
  система → система; подписан → сдано, на подписи → готово, черновик → не готово, открытое замечание технадзора/заказчика → не готово;
- папка сдачи (`<папка команды>/Сдача` или указанная): подпапки этажей «4 этаж», «АБК 2 этаж», «Подвал»,
  в них «ИД» и подпапки систем. Все документы этажа в PDF — готово, есть DOCX без PDF — не готово.
  Подписи по файлам не видны: «сдано» даёт только подписанный акт инженера или файл со словом «подписан».
Четыре цвета: сдано, готово (не сдано), не готово, не начато. Только стандартная библиотека Python.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import webbrowser
from pathlib import Path

DONE, READY, NOT_READY, NOT_STARTED = "сдано", "готово", "не_готово", "не_начато"
STATUSES = [
    {"id": DONE, "название": "Сдано", "цвет": "#2e7d32", "пояснение": "всё готово, подписано"},
    {"id": READY, "название": "Готово, не сдано", "цвет": "#9ccc65", "пояснение": "оформлено, ждёт подписей"},
    {"id": NOT_READY, "название": "Не готово", "цвет": "#f2b134", "пояснение": "чего-то не хватает или есть замечание технадзора"},
    {"id": NOT_STARTED, "название": "Не начато", "цвет": "#b0b8c1", "пояснение": "документов ещё нет"},
]
_RANK = {NOT_STARTED: 0, DONE: 1, READY: 2, NOT_READY: 3}

# что ждём на этаж в папке сдачи (общее для систем); ключ — основа слова в имени файла
COMMON = [("акт готовности", "Акт готовности"), ("устройство проход", "АОСР устройство проходок"),
          ("прокладка", "АОСР прокладка кабеля"), ("заделка", "АОСР заделка проходок"),
          ("примененного", "Ведомость применённого материала"), ("смонтированных", "Ведомость смонтированных средств")]
FLOOR_RX = re.compile(r"^(?:(?P<corp>[А-ЯA-Z][\wА-Яа-я.-]*?)\s+)?(?P<n>-?\d+)\s*(?:-?й\s*)?этаж$", re.I)
SPECIAL = {"подвал": 0, "чердак": 99, "кровля": 99}
MAIN = "Основной"


def floor_key(name: str):
    """«4 этаж» → (Основной, 4), «АБК 2 этаж» → (АБК, 2), «Подвал» → (Основной, 0); иначе None."""
    low = name.strip().lower()
    for word, n in SPECIAL.items():
        if low.endswith(word):
            return (name[: -len(word)].strip() or MAIN), n
    m = FLOOR_RX.match(name.strip())
    return ((m["corp"] or MAIN), int(m["n"])) if m else None


def floor_title(n) -> str:
    return "Подвал" if n == 0 else "Кровля" if n == 99 else f"{n} этаж"


def summary(statuses) -> str:
    s = set(statuses)
    if not s or s == {NOT_STARTED}:
        return NOT_STARTED
    if s == {DONE}:
        return DONE
    if s <= {DONE, READY}:
        return READY
    return NOT_READY


def merge(a: str, b: str) -> str:
    """Два источника об одной клетке: «не начато» уступает любому, иначе берём худшее."""
    if a == NOT_STARTED:
        return b
    if b == NOT_STARTED:
        return a
    return a if _RANK[a] >= _RANK[b] else b


# --- папка сдачи: по файлам -------------------------------------------------------------------------
def _files_state(files: list[Path]) -> str:
    if not files:
        return NOT_STARTED
    if any("подписан" in f.name.lower() for f in files):
        return DONE
    return READY if any(f.suffix.lower() == ".pdf" for f in files) else NOT_READY


def scan_delivery(root: Path, systems: list[str]) -> dict:
    """{(корпус, этаж): {система: (статус, [чего ждём])}} по папкам этажей."""
    out = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        key = floor_key(d.name)
        if key is None:
            continue
        files = [f for f in d.rglob("*") if f.is_file()]
        id_files = [f for f in files if "ИД" in f.relative_to(d).parts[:1]]
        common = []
        for stem, title in COMMON:
            common.append((title, _files_state([f for f in id_files if stem in f.name.lower()])))
        kj = [f for f in files if f.parent == d and f.name.upper().startswith("КЖ")]
        common.append(("Кабельный журнал", READY if kj else NOT_STARTED))
        found = {s for s in systems if (d / s).is_dir()} or set(systems)
        per = {}
        for s in found:
            sd = d / s
            scheme = [f for f in sd.iterdir() if f.is_file()] if sd.is_dir() else []
            items = common + [(f"Исполнительная схема {s}", _files_state(scheme))]
            states = [st for _, st in items]
            wait = [f"нет: {t}" for t, st in items if st == NOT_STARTED] + \
                   [f"нет PDF: {t}" for t, st in items if st == NOT_READY]
            st = NOT_STARTED if set(states) == {NOT_STARTED} else \
                DONE if set(states) == {DONE} else READY if set(states) <= {DONE, READY} else NOT_READY
            if st not in (NOT_STARTED, DONE) and wait == []:
                wait = ["ждём подписей"]
            per[s] = (st, wait)
        out[key] = per
    return out


# --- акты инженеров ---------------------------------------------------------------------------------
def scan_acts(team: Path) -> dict:
    """{(корпус, этаж или захватка): {система: (статус, [чего ждём])}} по актам инженеров."""
    try:
        from summary import collect, open_remarks, remark_cell
    except ImportError:                                   # скрипты не рядом — только папка сдачи
        return {}
    data = collect(team)
    acts = [a for v in data.values() for a in v["acts"]]
    cells: dict = {}
    for r in open_remarks(data):                          # замечание технадзора: не готово, пока не снято
        k = remark_cell(r, acts)
        if not k:
            continue
        key = floor_key(k[0]) or (k[0], None)
        why = f"замечание {r.get('от')}{' к ' + r['акт'] if r.get('акт') else ''}: {str(r.get('текст', ''))[:80]}" +               (" (ответ дан, ждём)" if r.get("статус") == "ответ_дан" else "")
        old = cells.setdefault(key, {}).get(k[1], (NOT_STARTED, []))
        cells[key][k[1]] = (NOT_READY, old[1] + [why])
    for a in acts:
        place = str(a.get("захватка") or "").strip()
        key = floor_key(place) or (place or "без захватки", None)
        system = str(a.get("система") or "").strip() or "без системы"
        num, eng = str(a.get("номер") or "?"), a.get("_инженер", "")
        status = str(a.get("статус") or "черновик")
        if status == "подписан":
            st, why = DONE, ""
        elif status == "на_подписи":
            st, why = READY, f"{num}: ждём подписи ({eng})"
        else:
            st, why = NOT_READY, f"{num}: в работе у {eng}" if eng else f"{num}: в работе"
        old = cells.setdefault(key, {}).get(system, (NOT_STARTED, []))
        cells[key][system] = (merge(old[0], st) if old[0] != NOT_STARTED else st, old[1] + ([why] if why else []))
    return cells


# --- модель и страница ------------------------------------------------------------------------------
def parse_floors(spec: str | None) -> dict[str, list[int]]:
    out = {}
    for part in (spec or "").split(";"):
        if ":" not in part:
            continue
        corp, rng = part.split(":", 1)
        nums = []
        for r in rng.split(","):
            r = r.strip().lower()
            if r in SPECIAL:
                nums.append(SPECIAL[r])
            elif "-" in r:
                a, b = r.split("-", 1)
                nums += list(range(int(a), int(b) + 1))
            elif r.isdigit():
                nums.append(int(r))
        out[corp.strip()] = sorted(set(nums))
    return out


def model(team: Path | None, delivery: Path | None, systems: list[str], floors_spec: str | None, title: str) -> dict:
    sources = [s for s in (scan_delivery(delivery, systems) if delivery else {},
                           scan_acts(team) if team else {}) if s]
    cells: dict = {}
    for src in sources:
        for key, per in src.items():
            for s, (st, wait) in per.items():
                old = cells.setdefault(key, {}).get(s, (NOT_STARTED, []))
                cells[key][s] = (merge(old[0], st), old[1] + wait)
    plan = parse_floors(floors_spec)
    corps = sorted({c for c, _ in cells} | set(plan), key=lambda c: (c != MAIN, c))
    all_systems = list(dict.fromkeys([*systems, *(s for per in cells.values() for s in per)]))
    out = []
    for c in corps:
        nums = {n for cc, n in cells if cc == c}
        numbered = sorted(n for n in nums if n is not None)
        want = set(plan.get(c, []))
        if numbered and not want:                    # дыры между найденными этажами — тоже этажи (серые)
            want = set(range(min(n for n in numbered if n != 99), max(n for n in numbered if n != 99) + 1)) \
                if any(n != 99 for n in numbered) else set()
            want.discard(0)
        floors = []
        for n in sorted(want | set(numbered)):
            per = cells.get((c, n), {})
            floors.append(_floor(f"{c}|{n}", floor_title(n), n, per))
        if None in nums:
            floors.insert(0, _floor(f"{c}|", c, None, cells[(c, None)]))
        out.append({"id": c, "название": c if c != MAIN or len(corps) > 1 else "Здание",
                    "этажи": floors, "сводно": summary(f["сводно"] for f in floors)})
    return {"объект": title, "статусы": STATUSES, "системы": [s for s in all_systems],
            "корпуса": out, "обновлено": dt.datetime.now().strftime("%d.%m.%Y %H:%M")}


def _floor(fid, title, n, per) -> dict:
    return {"id": fid, "название": title, "этаж": n, "по_системам": {s: st for s, (st, _) in per.items()},
            "ждём": {s: list(dict.fromkeys(w)) for s, (_, w) in per.items() if w},
            "сводно": summary(st for st, _ in per.values())}


def render(m: dict) -> str:
    data = json.dumps(m, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__TITLE__", html.escape(m["объект"])).replace("__DATA__", data)


TEMPLATE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Здание — __TITLE__</title><style>
:root{--bg:#f4f5f7;--fg:#1d232b;--muted:#5d6876;--card:#fff;--line:#d7dce3}
@media (prefers-color-scheme:dark){:root{--bg:#13171c;--fg:#e6eaef;--muted:#9aa6b4;--card:#1c222a;--line:#2f3741}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif;padding:16px}
h1{font-size:20px;margin:0 0 2px}.muted{color:var(--muted)}
.bar{display:flex;flex-wrap:wrap;gap:12px;margin:10px 0}select{font:inherit;padding:2px 4px;margin-left:4px}
.lg{display:flex;flex-wrap:wrap;gap:14px;font-size:13px;color:var(--muted);margin-bottom:8px}
.dot{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:5px;border:1px solid rgba(0,0,0,.2)}
.wrap{display:flex;gap:16px;align-items:flex-start}.cv{flex:1 1 auto;min-width:0;overflow-x:auto}
svg{width:100%;max-width:900px;height:auto;display:block}.slab{cursor:pointer}.slab:hover{filter:brightness(1.08)}
.fl{font-size:12px;fill:var(--fg)}.bl{font-size:13px;font-weight:600;fill:var(--fg)}
aside{flex:0 0 320px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;max-height:85vh;overflow-y:auto}
aside h3{margin:0 0 6px}aside ul{margin:4px 0;padding-left:1.2em;font-size:13px}aside li{margin:2px 0}.sys{margin:8px 0}
@media (max-width:760px){.wrap{flex-direction:column}aside{flex:1 1 auto;width:100%;box-sizing:border-box;max-height:none}svg{min-width:460px}}
</style></head><body><h1>__TITLE__</h1><div class="muted" id="u"></div>
<div class="bar" id="bar"></div><div class="lg" id="lg"></div><div class="wrap"><div class="cv" id="cv"></div><aside id="side"></aside></div>
<script>
const M=__DATA__;const NS='http://www.w3.org/2000/svg';const W=170,D=46,DX=34,H=12,GAP=24,COL=250,PAD=30;
const C={},N={};M['статусы'].forEach(s=>{C[s.id]=s['цвет'];N[s.id]=s['название']});
const sel={b:'',f:'',s:''};
document.getElementById('u').textContent='Обновлено '+M['обновлено']+' · по актам инженеров и папке сдачи';
document.getElementById('lg').replaceChildren(...M['статусы'].map(s=>{const e=document.createElement('span');e.title=s['пояснение'];
 e.append(dot(s.id),s['название']);return e}));
function E(t,a,k){const e=document.createElement(t);for(const x in a||{})e[x]=a[x];(k||[]).forEach(c=>e.append(c));return e}
function S(t,a,k){const e=document.createElementNS(NS,t);for(const x in a||{})e.setAttribute(x,a[x]);(k||[]).forEach(c=>e.append(c));return e}
function dot(st){const e=document.createElement('i');e.className='dot';e.style.background=C[st]||'#b0b8c1';return e}
function shade(h,f){const n=parseInt(h.slice(1),16);return 'rgb('+[(n>>16)&255,(n>>8)&255,n&255].map(v=>Math.max(0,Math.min(255,Math.round(v*f)))).join(',')+')'}
const st=f=>sel.s?(f['по_системам'][sel.s]||'не_начато'):f['сводно'];
function pick(label,val,opts,on){const s=E('select',{},[E('option',{value:'',textContent:'все'}),...opts.map(([v,t])=>E('option',{value:v,textContent:t}))]);
 s.value=val;s.onchange=e=>on(e.target.value);return E('label',{},[label+': ',s])}
function drawBar(){const B=M['корпуса'];const fl=B.filter(b=>!sel.b||b.id===sel.b).flatMap(b=>b['этажи'].map(f=>[f.id,(B.length>1?b['название']+' · ':'')+f['название']])).reverse();
 document.getElementById('bar').replaceChildren(...(B.length>1?[pick('Корпус',sel.b,B.map(b=>[b.id,b['название']]),v=>{sel.b=v;sel.f='';draw()})]:[]),
  pick('Этаж',sel.f,fl,v=>{sel.f=v;draw()}),pick('Система',sel.s,M['системы'].map(s=>[s,s]),v=>{sel.s=v;draw()}))}
function drawSvg(){const B=M['корпуса'];const tall=Math.max(1,...B.map(b=>b['этажи'].length));const w=PAD*2+60+B.length*COL,h=PAD*2+tall*(H+GAP)+D+20;
 const svg=S('svg',{viewBox:'0 0 '+w+' '+h,role:'img'});
 B.forEach((b,bi)=>{const g=S('g',{opacity:sel.b&&sel.b!==b.id?.25:1});const x=PAD+60+bi*COL;
  g.append(S('text',{x:x+W/2,y:h-6,'text-anchor':'middle',class:'bl'},[b['название']]));
  b['этажи'].forEach((f,i)=>{const y=h-PAD-10-i*(H+GAP)-H,c=C[st(f)]||'#b0b8c1',on=sel.f===f.id,sk=on?'#111':'rgba(0,0,0,.35)',sw=on?2.5:.8,t=D*.55;
   const s=S('g',{class:'slab',tabindex:0},[
    S('polygon',{points:`${x},${y} ${x+W},${y} ${x+W+DX},${y-t} ${x+DX},${y-t}`,fill:shade(c,1.12),stroke:sk,'stroke-width':sw}),
    S('polygon',{points:`${x+W},${y} ${x+W+DX},${y-t} ${x+W+DX},${y-t+H} ${x+W},${y+H}`,fill:shade(c,.72),stroke:sk,'stroke-width':sw}),
    S('rect',{x,y,width:W,height:H,fill:c,stroke:sk,'stroke-width':sw}),
    S('text',{x:x-8,y:y+H-2,'text-anchor':'end',class:'fl'},[f['название']]),S('title',{},[f['название']+': '+N[st(f)]])]);
   const go=()=>{sel.b=b.id;sel.f=on?'':f.id;draw()};s.onclick=go;s.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go()}};g.append(s)});
  svg.append(g)});document.getElementById('cv').replaceChildren(svg)}
function drawSide(){const side=document.getElementById('side');const B=M['корпуса'];
 const fl=B.flatMap(b=>b['этажи'].map(f=>Object.assign({},f,{корпус:b['название']}))).find(f=>f.id===sel.f);
 if(fl){const sys=M['системы'].filter(s=>(!sel.s||s===sel.s)&&fl['по_системам'][s]);
  side.replaceChildren(E('h3',{textContent:(B.length>1?fl['корпус']+' · ':'')+fl['название']}),E('p',{},[dot(st(fl)),N[st(fl)]]),
   ...(sys.length?sys.map(s=>{const x=fl['по_системам'][s],w=fl['ждём'][s]||[];return E('div',{className:'sys'},[dot(x),E('b',{textContent:s}),' — '+N[x].toLowerCase(),
    ...(w.length?[E('ul',{},w.map(t=>E('li',{textContent:t})))]:[])])}):[E('p',{className:'muted',textContent:'Документов по этажу нет.'})]));return}
 const L=[],cnt={};for(const b of B){if(sel.b&&b.id!==sel.b)continue;for(const f of b['этажи']){cnt[st(f)]=(cnt[st(f)]||0)+1}
  for(const f of [...b['этажи']].reverse())for(const s in f['ждём']){if(sel.s&&s!==sel.s)continue;
   L.push(E('li',{},[dot(f['по_системам'][s]),(B.length>1?b['название']+' · ':'')+f['название']+' · '+s+': '+f['ждём'][s].join('; ')]))}}
 side.replaceChildren(E('h3',{textContent:sel.b?(B.find(b=>b.id===sel.b)||{})['название']:'Всё здание'}),
  E('p',{className:'muted',textContent:M['статусы'].filter(s=>cnt[s.id]).map(s=>s['название']+': '+cnt[s.id]).join(' · ')||'Этажей нет'}),
  E('b',{textContent:'Чего не хватает, чего ждём'}),L.length?E('ul',{},L.slice(0,60)):E('p',{className:'muted',textContent:'Ничего не ждём.'}),
  E('p',{className:'muted',textContent:'Нажмите на этаж, чтобы увидеть его системы.'}))}
function draw(){drawBar();drawSvg();drawSide()}draw();
</script></body></html>"""


def find_delivery(team: Path | None) -> Path | None:
    """Папка сдачи внутри папки команды: «Сдача*» или сама папка, если в ней лежат папки этажей."""
    if team is None or not team.is_dir():
        return None
    for d in sorted(team.iterdir()):
        if d.is_dir() and d.name.lower().startswith("сдача"):
            return d
    return team if any(floor_key(d.name) for d in team.iterdir() if d.is_dir()) else None


def make(team: Path | None, delivery: Path | None = None, *, systems=("СПС", "СОУЭ"), floors: str | None = None,
         title: str | None = None, out: Path | None = None, open_page: bool = True) -> tuple[Path, dict]:
    """Собрать здание.html (рядом с папкой команды) и открыть в браузере. PTO_NO_OPEN=1 — не открывать."""
    import os
    delivery = delivery or find_delivery(team)
    title = title or (team.name if team else delivery.parent.name)
    saved = team / "здание.json" if team else None          # этажи задают один раз, дальше берутся сами
    if saved is not None:
        if floors:
            saved.write_text(json.dumps({"этажи": floors}, ensure_ascii=False), encoding="utf-8")
        elif saved.is_file():
            try:
                floors = json.loads(saved.read_text(encoding="utf-8")).get("этажи")
            except (ValueError, OSError):
                floors = None
    m = model(team, delivery, list(systems), floors, title)
    out = out or (team or delivery.parent) / "здание.html"
    out.write_text(render(m), encoding="utf-8")
    if open_page and os.environ.get("PTO_NO_OPEN") != "1":
        webbrowser.open(out.resolve().as_uri())
    return out, m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", help="папка сдачи (по умолчанию — «Сдача» в папке команды)")
    ap.add_argument("--team", help="папка команды (по умолчанию — из настроек скилла)")
    ap.add_argument("--out")
    ap.add_argument("--floors")
    ap.add_argument("--systems", default="СПС,СОУЭ")
    ap.add_argument("--title")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args(argv)
    team = Path(a.team) if a.team else None
    if team is None and not a.root:
        try:
            from common import load_config
            team = Path(load_config()["team_folder"])
        except (ImportError, SystemExit, KeyError) as exc:
            print(f"Не знаю, где папка команды: {exc}. Укажите папку сдачи или --team.", file=sys.stderr)
            return 2
    delivery = Path(a.root) if a.root else None
    for p in (team, delivery):
        if p is not None and not p.is_dir():
            print(f"нет папки {p}", file=sys.stderr)
            return 2
    out, m = make(team, delivery, systems=[s.strip() for s in a.systems.split(",") if s.strip()], floors=a.floors,
                  title=a.title, out=Path(a.out) if a.out else None, open_page=not a.no_open)
    names = {s["id"]: s["название"] for s in STATUSES}
    for c in m["корпуса"]:
        print(f"{c['название']}: " + ", ".join(f"{f['название']} — {names[f['сводно']].lower()}" for f in c["этажи"]))
    waits = sum(len(w) for c in m["корпуса"] for f in c["этажи"] for w in f["ждём"].values())
    print(f"чего ждём: {waits} пунктов")
    print(f"здание: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
