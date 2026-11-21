"""Заполнение своего шаблона Word метками {{поле}} — только стандартная библиотека.

python docx_fill.py шаблон.docx акт.json выход.docx   — заполнить
python docx_fill.py шаблон.docx                        — показать метки шаблона
Метки: {{номер}}, {{дата_акта}}, {{работы}}, {{представители.застройщик}} и т. д. (поля акта, см. SKILL.md).
Word часто режет метку на несколько кусков (run) — склеиваем текст абзаца, заменяем, результат кладём
в первый кусок метки. XML правим как текст (без пересборки дерева): пространства имён и всё прочее,
что Word ждёт, остаётся байт в байт.
"""
from __future__ import annotations

import html
import json
import re
import sys
import zipfile

LABEL_RE = re.compile(r"\{\{([\w.Ѐ-ӿ]+)\}\}")
PARTS = re.compile(r"^word/(document|header\d*|footer\d*)\.xml$")
PARA = re.compile(r"<w:p[ >].*?</w:p>|<w:p/>", re.S)
TEXT = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.S)


_MISSING = object()


def _resolve(key: str, data: dict):
    """Значение по пути «а.б»; нет такого поля — _MISSING (поле есть, но None — пустая строка)."""
    val = data
    for p in key.split("."):
        if not isinstance(val, dict) or p not in val:
            return _MISSING
        val = val[p]
    return val


def _format_value(val) -> str:
    if val is None:
        return ""
    if isinstance(val, (str, int, float, bool)):
        return str(val)
    if isinstance(val, dict):
        return "; ".join(f"{k}: {_format_value(v)}" for k, v in val.items() if v not in (None, "", []))
    if isinstance(val, list):
        out = []
        for item in val:
            if isinstance(item, dict) and "шифр" in item:
                out.append(", ".join(x for x in (str(item.get("шифр") or ""),
                                                  f"листы {item['листы']}" if item.get("листы") else "",
                                                  str(item.get("наименование") or "")) if x))
            elif isinstance(item, dict):
                name, doc = str(item.get("наименование") or ""), str(item.get("документ") or "")
                out.append(f"{name} ({doc})" if doc else name)
            else:
                out.append(str(item))
        return "; ".join(x for x in out if x)
    return str(val)


def _fill_paragraph(p: str, data: dict, missing: list) -> str:
    texts = list(TEXT.finditer(p))
    if not texts:
        return p
    raw = [html.unescape(m.group(2)) for m in texts]
    full = "".join(raw)
    if "{{" not in full:
        return p
    # смещение начала каждого куска в склеенном тексте
    starts, pos = [], 0
    for r in raw:
        starts.append(pos); pos += len(r)
    new = list(raw)
    # идём с конца, чтобы замены не сдвигали более ранние смещения
    for m in reversed(list(LABEL_RE.finditer(full))):
        val = _resolve(m.group(1), data)
        if val is _MISSING:
            if m.group(0) not in missing:
                missing.append(m.group(0))
            continue
        a, b = m.start(), m.end()
        first = max(i for i, s in enumerate(starts) if s <= a)
        last = max(i for i, s in enumerate(starts) if s < b)
        head = new[first][: a - starts[first]]
        tail = new[last][b - starts[last]:] if last != first else new[first][b - starts[first]:]
        if last == first:
            new[first] = head + _format_value(val) + tail
        else:
            new[first] = head + _format_value(val)
            for i in range(first + 1, last):
                new[i] = ""
            new[last] = tail
    out, prev = [], 0
    for m, t in zip(texts, new):
        open_tag = m.group(1)
        if t != t.strip() and "xml:space" not in open_tag:
            open_tag = open_tag[:-1] + ' xml:space="preserve">'
        out.append(p[prev:m.start()] + open_tag + html.escape(t, quote=False) + m.group(3))
        prev = m.end()
    out.append(p[prev:])
    return "".join(out)


def _fill_xml(xml: str, data: dict, missing: list) -> str:
    return PARA.sub(lambda m: _fill_paragraph(m.group(0), data, missing), xml)


def fields(template_path) -> list:
    """Все метки шаблона по порядку, без повторов."""
    seen: list[str] = []
    with zipfile.ZipFile(template_path) as z:
        for name in z.namelist():
            if PARTS.match(name):
                xml = z.read(name).decode("utf-8")
                for p in PARA.findall(xml):
                    for m in LABEL_RE.finditer("".join(html.unescape(t.group(2)) for t in TEXT.finditer(p))):
                        if m.group(0) not in seen:
                            seen.append(m.group(0))
    return seen


def fill(template_path, data: dict, out_path) -> list:
    """Заполнить шаблон и записать out_path. Возвращает метки, которых нет в data (остались в тексте)."""
    missing: list[str] = []
    with zipfile.ZipFile(template_path) as zin, zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if PARTS.match(item.filename):
                raw = _fill_xml(raw.decode("utf-8"), data, missing).encode("utf-8")
            zout.writestr(item, raw)
    return missing


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 1:
        print("\n".join(fields(args[0])) or "меток {{...}} в шаблоне нет")
        return 0
    if len(args) != 3:
        print(__doc__); return 2
    data = json.loads(open(args[1], encoding="utf-8-sig").read())
    miss = fill(args[0], data, args[2])
    print(f"готово: {args[2]}" + (f"; не заполнены: {', '.join(miss)}" if miss else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
