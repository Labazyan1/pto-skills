"""xlsx_read.py — read XLSX / CSV without third-party libraries."""
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Optional

# Namespaces
NS_SS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

SS = f"{{{NS_SS}}}"
R_NS = f"{{{NS_R}}}"


def _col_index(col_str: str) -> int:
    """Convert column letters (A, B, ..., AA, ...) to 0-based index."""
    idx = 0
    for ch in col_str.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _cell_ref(ref: str):
    """Split 'C5' into ('C', 5)."""
    m = re.match(r"([A-Za-z]+)(\d+)", ref)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def _excel_date(serial: float) -> str:
    """Convert Excel serial date to YYYY-MM-DD string."""
    # Excel epoch: 1899-12-30 (accounting for the Lotus 1-2-3 bug)
    epoch = date(1899, 12, 30)
    d = epoch + timedelta(days=int(serial))
    return d.strftime("%Y-%m-%d")


def _is_date_format(num_fmt_id: int, fmt_str: str) -> bool:
    """Return True if the format represents a date."""
    if 14 <= num_fmt_id <= 22:
        return True
    if fmt_str:
        # Check for date-like patterns
        clean = re.sub(r'"[^"]*"', "", fmt_str)
        clean = re.sub(r'\[.*?\]', "", clean)
        if re.search(r'[dDmMyY]', clean):
            return True
    return False


def _parse_shared_strings(xml_bytes: bytes) -> list:
    """Parse xl/sharedStrings.xml, return list of strings."""
    root = ET.fromstring(xml_bytes)
    strings = []
    for si in root.iter(f"{SS}si"):
        # Collect all <t> text (handles rich text)
        parts = []
        for t in si.iter(f"{SS}t"):
            parts.append(t.text or "")
        strings.append("".join(parts))
    return strings


def _parse_styles(xml_bytes: bytes) -> dict:
    """
    Parse xl/styles.xml.
    Returns dict: xf_index -> (numFmtId, format_string)
    """
    root = ET.fromstring(xml_bytes)

    # Custom number formats
    num_fmts = {}
    num_fmts_el = root.find(f"{SS}numFmts")
    if num_fmts_el is not None:
        for nf in num_fmts_el.findall(f"{SS}numFmt"):
            nid = int(nf.get("numFmtId", 0))
            fmt_code = nf.get("formatCode", "")
            num_fmts[nid] = fmt_code

    # cellXfs
    xf_map = {}
    cell_xfs = root.find(f"{SS}cellXfs")
    if cell_xfs is not None:
        for idx, xf in enumerate(cell_xfs.findall(f"{SS}xf")):
            nid = int(xf.get("numFmtId", 0))
            fmt_str = num_fmts.get(nid, "")
            xf_map[idx] = (nid, fmt_str)
    return xf_map


def _parse_workbook(xml_bytes: bytes):
    """
    Parse xl/workbook.xml.
    Returns list of (sheet_name, r:id).
    """
    root = ET.fromstring(xml_bytes)
    sheets = []
    for sheet in root.iter(f"{SS}sheet"):
        name = sheet.get("name", "")
        rid = sheet.get(f"{R_NS}id", "")
        sheets.append((name, rid))
    return sheets


def _parse_rels(xml_bytes: bytes) -> dict:
    """
    Parse xl/_rels/workbook.xml.rels.
    Returns dict: r:id -> target path (relative to xl/).
    """
    NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
    root = ET.fromstring(xml_bytes)
    rels = {}
    for rel in root.findall(f"{{{NS_REL}}}Relationship"):
        rid = rel.get("Id", "")
        target = rel.get("Target", "")
        rels[rid] = target
    return rels


def _parse_sheet(xml_bytes: bytes, shared_strings: list, xf_map: dict) -> list:
    """Parse a worksheet XML, return list of rows (list of strings)."""
    root = ET.fromstring(xml_bytes)

    sheet_data = root.find(f"{SS}sheetData")
    if sheet_data is None:
        return []

    rows_dict = {}  # row_num -> {col_idx -> str}

    for row_el in sheet_data.findall(f"{SS}row"):
        r_attr = row_el.get("r")
        if r_attr is None:
            continue
        row_num = int(r_attr)
        row_dict = {}

        for c_el in row_el.findall(f"{SS}c"):
            ref = c_el.get("r", "")
            col_str, _ = _cell_ref(ref)
            if col_str is None:
                continue
            col_idx = _col_index(col_str)

            t = c_el.get("t", "")  # type
            s = c_el.get("s")     # style index
            v_el = c_el.find(f"{SS}v")
            v_text = v_el.text if v_el is not None else None

            if t == "s":
                # shared string
                try:
                    idx = int(v_text)
                    cell_val = shared_strings[idx] if idx < len(shared_strings) else ""
                except (TypeError, ValueError):
                    cell_val = ""
            elif t == "inlineStr":
                is_el = c_el.find(f"{SS}is")
                if is_el is not None:
                    parts = []
                    for t_el in is_el.iter(f"{SS}t"):
                        parts.append(t_el.text or "")
                    cell_val = "".join(parts)
                else:
                    cell_val = ""
            elif t == "b":
                cell_val = "ИСТИНА" if v_text == "1" else "ЛОЖЬ"
            elif t == "str":
                cell_val = v_text or ""
            else:
                # numeric or date
                if v_text is None:
                    cell_val = ""
                else:
                    is_date = False
                    if s is not None:
                        s_idx = int(s)
                        if s_idx in xf_map:
                            nid, fmt_str = xf_map[s_idx]
                            is_date = _is_date_format(nid, fmt_str)

                    if is_date:
                        try:
                            cell_val = _excel_date(float(v_text))
                        except ValueError:
                            cell_val = v_text
                    else:
                        # Number: strip trailing .0 for integers
                        try:
                            fval = float(v_text)
                            if fval == int(fval) and "." not in v_text:
                                cell_val = str(int(fval))
                            elif fval == int(fval):
                                # e.g. "2.0" in file -> "2"
                                cell_val = str(int(fval))
                            else:
                                cell_val = v_text
                        except ValueError:
                            cell_val = v_text

            row_dict[col_idx] = cell_val

        rows_dict[row_num] = row_dict

    if not rows_dict:
        return []

    min_row = min(rows_dict.keys())
    max_row = max(rows_dict.keys())

    # Find max column across all rows
    max_col = 0
    for rd in rows_dict.values():
        if rd:
            max_col = max(max_col, max(rd.keys()))

    result = []
    for rn in range(min_row, max_row + 1):
        rd = rows_dict.get(rn, {})
        row = [rd.get(ci, "") for ci in range(max_col + 1)]
        result.append(row)

    return result


def read_rows(path: str, sheet: Optional[str] = None) -> list:
    """
    Read an XLSX file, return list of rows (each row is list of strings).
    sheet: sheet name or None for first sheet.
    """
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()

        # Shared strings (optional)
        shared_strings = []
        if "xl/sharedStrings.xml" in names:
            shared_strings = _parse_shared_strings(zf.read("xl/sharedStrings.xml"))

        # Styles (optional)
        xf_map = {}
        if "xl/styles.xml" in names:
            xf_map = _parse_styles(zf.read("xl/styles.xml"))

        # Workbook
        wb_bytes = zf.read("xl/workbook.xml")
        sheets = _parse_workbook(wb_bytes)

        # Relationships
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            rels = _parse_rels(zf.read("xl/_rels/workbook.xml.rels"))

        # Choose sheet
        if not sheets:
            return []

        if sheet is None:
            target_rid = sheets[0][1]
        else:
            target_rid = None
            for sname, rid in sheets:
                if sname == sheet:
                    target_rid = rid
                    break
            if target_rid is None:
                raise ValueError(f"Sheet '{sheet}' not found")

        # Resolve path
        rel_target = rels.get(target_rid, "")
        # target may be like "worksheets/sheet1.xml"
        if not rel_target.startswith("/"):
            sheet_path = "xl/" + rel_target
        else:
            sheet_path = rel_target.lstrip("/")

        sheet_bytes = zf.read(sheet_path)
        return _parse_sheet(sheet_bytes, shared_strings, xf_map)


def _detect_encoding(raw: bytes) -> str:
    """Detect encoding: utf-8-sig first, then cp1251."""
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "cp1251"


def _detect_delimiter(sample: str) -> str:
    """Detect delimiter from sample text."""
    counts = {";": sample.count(";"), ",": sample.count(","), "\t": sample.count("\t")}
    return max(counts, key=counts.get)


def read_csv_or_xlsx(path: str) -> list:
    """
    Read CSV/TXT or XLSX file.
    For CSV: auto-detect delimiter and encoding.
    For XLSX: use read_rows.
    """
    pl = path.lower()
    if pl.endswith(".xlsx"):
        return read_rows(path)

    # CSV / TXT
    with open(path, "rb") as f:
        raw = f.read()

    encoding = _detect_encoding(raw)
    text = raw.decode(encoding)
    delimiter = _detect_delimiter(text[:4096])

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [row for row in reader]
