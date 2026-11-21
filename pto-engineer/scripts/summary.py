"""Сводка начальника ПТО по папке команды + интерактивная доска «захватки × системы».

python summary.py [--days 7] [--team ПАПКА]  → отчёт в консоль, доска <команда>/доска.html
python summary.py --plan "1 этаж,2 этаж,3 этаж" --systems "СПС,СОУЭ,ЭОМ"  → <команда>/план.json
   (что ожидается по объекту: тогда пустые клетки видны как «нет актов»)
python summary.py --members "Петров П.П.,Сидоров С.С."  → <команда>/команда.json (кого ждём: без данных видно)
Статус клетки (только готовность, результаты проверки актов — для инженера, не сюда): подписан — все акты
подписаны; на подписи; в работе — есть черновики; замечание — открытое замечание технадзора/заказчика;
нет актов — ожидается по плану, актов нет.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sys
from collections import defaultdict
from pathlib import Path

from common import is_open, load_acts, load_config, load_remarks, log_event, read_journal, safe_name

STATUSES = [("замечание", "Замечание технадзора"), ("нет", "Нет актов"), ("в_работе", "В работе"),
            ("на_подписи", "На подписи"), ("подписан", "Подписано")]
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
        if acts or snap or remarks or any(e.get("действие") != "сводка" for e in journal):   # папка начальника — только сводки
            for a in acts:
                a["_инженер"] = d.name
            for r in remarks:
                r["_инженер"] = d.name
            out[d.name] = {"acts": acts, "journal": journal, "snap": snap, "remarks": remarks}
    return out


def open_remarks(data: dict) -> list[dict]:
    return [r for v in data.values() for r in v.get("remarks", []) if is_open(r)]


def remark_cell(r: dict, acts: list[dict]) -> tuple[str, str] | None:
    """Клетка замечания: по акту (его захватка и система) или по полям самого замечания."""
    a = next((x for x in acts if r.get("акт") and str(x.get("номер", "")).lower() == str(r["акт"]).lower()), None)
    zone = str((a or {}).get("захватка") or r.get("захватка") or "").strip()
    system = str((a or {}).get("система") or r.get("система") or "").strip()
    if not zone and not system:
        return None
    return zone or "без захватки", system or "без системы"


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


def cell_status(acts: list[dict], remarks: list[dict] | None = None) -> str:
    if remarks:
        return "замечание"
    st = {str(a.get("статус") or "черновик") for a in acts}
    if st == {"подписан"}:
        return "подписан"
    if "черновик" in st:
        return "в_работе"
    return "на_подписи"


def board_data(team: Path, data: dict) -> dict:
    acts = [a for v in data.values() for a in v["acts"]]
    by_cell = defaultdict(list)
    for r in open_remarks(data):
        key = remark_cell(r, acts)
        if key:
            by_cell[key].append(r)
    plan = {}
    pp = team / "план.json"
    if pp.exists():
        try:
            plan = json.loads(pp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            plan = {}
    rows = list(dict.fromkeys([*(plan.get("захватки") or []), *(str(a.get("захватка") or "без захватки") for a in acts),
                               *(k[0] for k in by_cell)]))
    cols = list(dict.fromkeys([*(plan.get("системы") or []), *(str(a.get("система") or "без системы") for a in acts),
                               *(k[1] for k in by_cell)]))
    cells = {}
    grouped = defaultdict(list)
    for a in acts:
        grouped[(str(a.get("захватка") or "без захватки"), str(a.get("система") or "без системы"))].append(a)
    for r in rows:
        for c in cols:
            g = grouped.get((r, c), [])
            rm = by_cell.get((r, c), [])
            expected = r in (plan.get("захватки") or []) and c in (plan.get("системы") or [])
            if not g and not expected and not rm:
                continue
            cells[f"{r}|{c}"] = {
                "статус": cell_status(g, rm) if (g or rm) else "нет",
                "акты": [{"номер": a.get("номер"), "работы": str(a.get("работы", ""))[:160], "статус": a.get("статус") or "черновик",
                          "инженер": a["_инженер"], "дата": a.get("дата_акта")} for a in g],
                "замечания": [{"от": x.get("от"), "текст": str(x.get("текст", ""))[:300], "статус": x.get("статус"),
                               "акт": x.get("акт", ""), "ответ": str(x.get("ответ", ""))[:300], "инженер": x.get("_инженер")}
                              for x in rm]}
    return {"строки": rows, "столбцы": cols, "клетки": cells, "обновлено": dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
            "объект": team.name}


def text_summary(data: dict, days: int, expected: list[str] | None = None) -> str:
    since = (dt.datetime.now().astimezone() - dt.timedelta(days=days)).isoformat()
    acts = [a for v in data.values() for a in v["acts"]]
    rem = open_remarks(data)
    lines = [f"Сводка за {days} дн. Инженеров: {len(data)}, актов: {len(acts)}, открытых замечаний технадзора/заказчика: {len(rem)}."]
    silent = [m for m in (expected or []) if safe_name(m) not in data]
    if silent:
        lines.append("Нет данных от инженеров (папка пуста или Диск не синхронизирован): " + ", ".join(silent))
    for name, v in data.items():
        recent = [e for e in v["journal"] if str(e.get("время", "")) >= since]
        made = sum(e.get("действие") == "создан" for e in recent)
        fixed = sum(e.get("действие") == "исправлен" for e in recent)
        st = defaultdict(int)
        for a in v["acts"]:
            st[a.get("статус") or "черновик"] += 1
        mine = [r for r in rem if r.get("_инженер") == name]
        last = max((str(e.get("время", "")) for e in v["journal"]), default="—")[:16].replace("T", " ")
        snap = v.get("snap") or {}
        if snap:
            s, ch = snap.get("итоги", {}), snap.get("изменения", {})
            lines.append(f"- {name}: в рабочих папках {s.get('файлов', 0)} файлов, актов {s.get('актов', 0)} {s.get('по_статусам', {})}; "
                         f"системы {', '.join(s.get('системы', [])) or '—'}; с прошлого скана новых {len(ch.get('новые', []))}, "
                         f"изменённых {len(ch.get('изменённые', []))}; скан {str(snap.get('обновлено', ''))[:16].replace('T', ' ')}"
                         + (f" — ДАННЫЕ СТАРШЕ {STALE_DAYS} ДН., попросите инженера запустить скан" if stale(snap) else ""))
        asked = [e for e in recent if e.get("действие") == "запрос"]
        if asked:
            lines.append(f"  запросов к скиллу: {len(asked)}, последний: {str(asked[-1].get('текст', ''))[:100]}")
        lines.append(f"  {name}: новых {made}, правок {fixed}; черновиков {st['черновик']}, на подписи {st['на_подписи']}, "
                     f"подписано {st['подписан']}; открытых замечаний {len(mine)}; последняя активность {last}")
        for r in mine[:5]:
            lines.append(f"    замечание {r.get('от')}{' к ' + r['акт'] if r.get('акт') else ''}: {str(r.get('текст', ''))[:100]}"
                         + (f" (ответ дан: {str(r.get('ответ'))[:60]})" if r.get("статус") == "ответ_дан" else ""))
    missing = [e for v in data.values() for e in v["journal"] if e.get("действие") == "нет_формы" and str(e.get("время", "")) >= since]
    if missing:
        lines.append("Просили, а скилл не умеет: " + "; ".join(str(e.get("заметка", ""))[:80] for e in missing))
    return "\n".join(lines)


def render_html(b: dict) -> str:
    payload = json.dumps(b, ensure_ascii=False).replace("</", "<\\/")
    legend = "".join(f'<span class="lg s-{k}">{html.escape(t)}</span>' for k, t in STATUSES)
    return (TEMPLATE.replace("__TITLE__", html.escape(b["объект"])).replace("__LEGEND__", legend)
            .replace("__DATA__", payload))


TEMPLATE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Доска ИД — __TITLE__</title><style>
:root{--bg:#f6f7f9;--fg:#1d232b;--muted:#5d6876;--card:#fff;--line:#dde2e8;
--ok:#2f8f5b;--sign:#2f6fb3;--work:#c48a12;--err:#c53a3a;--none:#9aa4b0}
@media (prefers-color-scheme:dark){:root{--bg:#14181d;--fg:#e6eaef;--muted:#9aa6b4;--card:#1d232b;--line:#2f3741;
--ok:#4cb57c;--sign:#5a97d6;--work:#e0a93a;--err:#e06363;--none:#6b7684}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,Segoe UI,sans-serif;padding:16px}
h1{font-size:20px;margin:0 0 4px}.muted{color:var(--muted)}.wrap{overflow-x:auto;margin-top:12px}
table{border-collapse:separate;border-spacing:4px}th{font-weight:600;text-align:left;padding:4px 8px;white-space:nowrap}
td.c{min-width:92px;height:44px;border-radius:8px;cursor:pointer;color:#fff;text-align:center;font-weight:600}
td.empty{background:transparent;border:1px dashed var(--line);border-radius:8px}
.s-подписан{background:var(--ok)}.s-на_подписи{background:var(--sign)}.s-в_работе{background:var(--work)}
.s-замечание{background:var(--err)}.s-нет{background:var(--none)}
.lg{display:inline-block;color:#fff;border-radius:6px;padding:2px 8px;margin:4px 6px 0 0;font-size:12px}
#d{margin-top:16px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px}
#d li{margin:6px 0}.err{color:var(--err)}
</style></head><body><h1>__TITLE__</h1><div class="muted" id="u"></div><div>__LEGEND__</div>
<div class="wrap"><table id="t"></table></div><div id="d" hidden></div>
<script>const B=__DATA__;const N={"подписан":"✓","на_подписи":"подпись","в_работе":"в работе","замечание":"замечание","нет":"—"};
document.getElementById('u').textContent='Обновлено '+B["обновлено"]+'. Нажмите на клетку — список актов.';
const t=document.getElementById('t'),d=document.getElementById('d');
const hr=t.insertRow();hr.appendChild(document.createElement('th'));
for(const c of B["столбцы"]){const th=document.createElement('th');th.textContent=c;hr.appendChild(th)}
for(const r of B["строки"]){const tr=t.insertRow();const th=document.createElement('th');th.textContent=r;tr.appendChild(th);
 for(const c of B["столбцы"]){const td=tr.insertCell();const k=r+'|'+c,x=B["клетки"][k];
  if(!x){td.className='empty';continue}td.className='c s-'+x["статус"];
  const n=x["акты"].length,m=x["замечания"].length;td.textContent=(n||m)?(N[x["статус"]]+(n?' · '+n:'')):N["нет"];
  td.onclick=()=>{d.hidden=false;d.replaceChildren();const h=document.createElement('h3');h.textContent=r+' — '+c;d.appendChild(h);
   for(const e of x["замечания"]){const s=document.createElement('p');s.className='err';
    s.textContent='Замечание ('+e["от"]+(e["акт"]?', '+e["акт"]:'')+'): '+e["текст"]+(e["ответ"]?' — ответ: '+e["ответ"]:'')+' · '+e["статус"];d.appendChild(s)}
   if(!n){const p=document.createElement('p');p.textContent=m?'Актов в этой клетке нет.':'Актов нет, а по плану ожидаются.';d.appendChild(p);return}
   const ul=document.createElement('ul');for(const a of x["акты"]){const li=document.createElement('li');
    li.textContent=(a["номер"]||'?')+' · '+a["статус"]+' · '+a["инженер"]+(a["дата"]?' · '+a["дата"]:'')+' — '+a["работы"];
    ul.appendChild(li)}d.appendChild(ul)}}}
</script></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--team")
    ap.add_argument("--plan")
    ap.add_argument("--systems")
    ap.add_argument("--members")
    a = ap.parse_args(argv)
    cfg = load_config() if not a.team else None
    team = Path(a.team or cfg["team_folder"])
    if a.plan or a.systems:
        plan = {"захватки": [s.strip() for s in (a.plan or "").split(",") if s.strip()],
                "системы": [s.strip() for s in (a.systems or "").split(",") if s.strip()]}
        (team / "план.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"план сохранён: {len(plan['захватки'])} захваток × {len(plan['системы'])} систем")
        return 0
    if a.members:
        names = [s.strip() for s in a.members.split(",") if s.strip()]
        (team / "команда.json").write_text(json.dumps({"инженеры": names}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"команда сохранена: {len(names)} инженеров")
        return 0
    data = collect(team)
    print(text_summary(data, a.days, members(team)))
    b = board_data(team, data)
    out = team / "доска.html"
    out.write_text(render_html(b), encoding="utf-8")
    print(f"\nДоска: {out}")
    try:                                   # здание одной командой: тот же сбор + папка сдачи, открывается само
        from building import make
        plan = json.loads((team / "план.json").read_text(encoding="utf-8")) if (team / "план.json").exists() else {}
        house, _ = make(team, systems=plan.get("системы") or ("СПС", "СОУЭ"))
        print(f"Здание: {house}")
    except Exception as exc:               # доска важнее: здание не мешает сводке
        print(f"Здание не собрано: {exc}")
    if cfg:
        log_event(cfg, "сводка", инженеров=len(data), клеток=len(b["клетки"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
