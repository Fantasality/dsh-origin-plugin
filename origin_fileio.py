# -*- coding: utf-8 -*-
"""
origin_fileio —— 本地表格文件读取（纯 Python，不依赖 Origin，可离线单测）
==========================================================================

职责：把 CSV / TXT / XLSX / XLS 统一读成 {"列名": [值...]} 的列式结构，
供 origin_load_file 写入 Origin 工作表。设计要点：

- 中文安全：中文路径/中文列名按 UTF-8 处理；字节流按 utf-8-sig → gbk → utf-16
  依次探测解码，避免中文乱码；
- 分隔符嗅探：csv.Sniffer 失败时按"首行出现次数最多者"确定性回退
  （, ; \\t | 及空白分隔均支持，实验数据常见空白分隔表可直读）；
- 表头识别：首行非数值占优且次行含数值 → 视为表头；重名列自动加序号；
- 数值化：逐格尝试 float（容忍千分位逗号 / 百分号 / 全角数字），失败保留
  字符串；列类型标记 numeric / text / mixed / empty；
- XLSX / XLSM 走 openpyxl（未安装时返回结构化错误与安装建议，绝不崩溃）；
  旧版 .xls 需要 xlrd（未安装时建议先另存为 XLSX/CSV）。

所有失败返回统一错误结构：{"ok": False, "error_code": ..., "next_actions": [...]}。
"""
from __future__ import annotations

import csv
import io
import os
import re

import origin_errors as oerr

TEXT_EXTS = {".csv", ".txt", ".tsv", ".dat"}
MAX_PREVIEW_ROWS = 5
WARN_ROWS = 100_000

# 千分位逗号 / 货币符号 / 空白 / 百分号（数值清洗；% 号仅剥离，数值含义不变）
_NUM_CLEAN_RE = re.compile(r"[,\s%￥$€£]")
_FULLWIDTH = str.maketrans("０１２３４５６７８９．－＋Ｅｅ", "0123456789.-+Ee")


def _try_float(cell):
    """尽力把单元格转成 float；失败返回 None。None/空串不算失败。"""
    if cell is None:
        return None
    if isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    s = str(cell).strip().translate(_FULLWIDTH)
    if not s:
        return None
    cleaned = _NUM_CLEAN_RE.sub("", s)
    if cleaned in ("", "-", "+", ".", "-.", "+."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _decode_bytes(raw: bytes):
    """多编码探测解码：utf-8-sig → gbk → utf-16 → latin-1(兜底替换)。"""
    for enc in ("utf-8-sig", "gbk", "utf-16"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1(replace)"


def _sniff_delimiter(sample: str):
    """返回分隔符字符；None 表示按空白切分（实验数据常见）。"""
    cands = [",", ";", "\t", "|"]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(cands))
        return dialect.delimiter
    except csv.Error:
        pass
    lines = sample.splitlines()
    first = lines[0] if lines else ""
    best, best_n = ",", -1
    for d in cands:
        n = first.count(d)
        if n > best_n:
            best, best_n = d, n
    if best_n <= 0:
        if re.search(r"\S\s+\S", first):
            return None
        return ","
    return best


def _rows_from_text(text: str):
    delim = _sniff_delimiter(text[:4096])
    if delim is None:
        rows = [re.split(r"\s+", ln.strip()) for ln in text.splitlines() if ln.strip()]
        return rows, "whitespace"
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    return rows, ("tab" if delim == "\t" else delim)


def _numeric_ratio(row):
    cells = [c for c in row if str(c).strip() != ""]
    if not cells:
        return 0.0
    return sum(1 for c in cells if _try_float(c) is not None) / len(cells)


def _looks_like_header(first, second):
    """首行非数值占优，且次行以数值为主 → 表头。"""
    if not first:
        return False
    if _numeric_ratio(first) >= 0.5:
        return False
    if second is not None and _numeric_ratio(second) > 0.5:
        return True
    return any(str(c).strip() and _try_float(c) is None for c in first)


def _trim_empty(rows):
    """去掉全空的尾部行与尾部列（xlsx read_only 常见尾部空壳）。"""
    def row_empty(r):
        return all((c is None or str(c).strip() == "") for c in r)
    while rows and row_empty(rows[-1]):
        rows.pop()
    if not rows:
        return rows
    ncol = max(len(r) for r in rows)
    rows = [list(r) + [""] * (ncol - len(r)) for r in rows]
    keep = list(range(ncol))
    while keep:
        j = keep[-1]
        if all((r[j] is None or str(r[j]).strip() == "") for r in rows):
            keep.pop()
        else:
            break
    return [[r[j] for j in keep] for r in rows]


def _coerce_rows(rows):
    """字符串行 → 列式 dict + 逐列类型。返回 (columns, n_rows, dtypes, header_mode)。"""
    rows = [list(r) for r in rows if r is not None]
    if not rows:
        return {}, 0, [], "empty"
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    header = _looks_like_header(rows[0], rows[1] if len(rows) > 1 else None)
    data_rows = rows[1:] if header else rows
    if header:
        names, seen = [], {}
        for j, c in enumerate(rows[0]):
            nm = str(c).strip() or f"C{j + 1}"
            if nm in seen:
                seen[nm] += 1
                nm = f"{nm}_{seen[nm]}"
            else:
                seen[nm] = 0
            names.append(nm)
    else:
        names = [f"C{j + 1}" for j in range(ncol)]
    columns, dtypes = {}, []
    for j, nm in enumerate(names):
        nums = texts = 0
        out = []
        for r in data_rows:
            v = _try_float(r[j])
            if v is None:
                s = str(r[j]).strip()
                if s == "":
                    out.append(None)
                else:
                    out.append(s)
                    texts += 1
            else:
                out.append(v)
                nums += 1
        if nums and texts:
            dt = "mixed"
        elif nums:
            dt = "numeric"
        elif texts:
            dt = "text"
        else:
            dt = "empty"
        columns[nm] = out
        dtypes.append((nm, dt))
    return columns, len(data_rows), dtypes, ("header" if header else "no-header")


def _read_xlsx(path, sheet):
    try:
        from openpyxl import load_workbook
    except ImportError:
        return oerr.fail(
            "file_read_error", "读取 XLSX 需要 openpyxl（当前 Python 环境未安装）",
            next_actions=["在插件 venv 中执行 pip install openpyxl 后重试",
                          "或将文件另存为 CSV/TXT 后重试"])
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        names = list(wb.sheetnames)
        if sheet:
            key = str(sheet)
            if key.isdigit() and 1 <= int(key) <= len(names):
                ws = wb[names[int(key) - 1]]
            elif key in names:
                ws = wb[key]
            else:
                return oerr.fail("file_read_error", f"找不到工作表: {sheet}",
                                 sheets=names)
        else:
            ws = wb[names[0]]
        sheet_title = str(ws.title)
        rows = []
        for row in ws.iter_rows(values_only=True):
            rows.append(["" if c is None else c for c in row])
    finally:
        wb.close()
    rows = _trim_empty(rows)
    columns, n_rows, dtypes, header_mode = _coerce_rows(rows)
    meta = {"file_type": "xlsx", "sheet": sheet_title, "sheets": names,
            "header": header_mode}
    return oerr.ok(columns=columns, n_rows=n_rows, dtypes=dtypes, meta=meta)


def _read_xls(path, sheet):
    try:
        import xlrd
    except ImportError:
        return oerr.fail(
            "file_read_error", "读取旧版 .xls 需要 xlrd（未安装）",
            next_actions=["推荐：用 Excel/Origin 另存为 .xlsx 或 CSV 后重试",
                          "或安装旧版 xlrd（pip install xlrd==1.2.0，新版已不支持 xls）"])
    book = xlrd.open_workbook(path)
    names = book.sheet_names()
    if sheet:
        key = str(sheet)
        if key.isdigit() and 1 <= int(key) <= len(names):
            ws = book.sheet_by_index(int(key) - 1)
        elif key in names:
            ws = book.sheet_by_name(key)
        else:
            return oerr.fail("file_read_error", f"找不到工作表: {sheet}", sheets=names)
    else:
        ws = book.sheet_by_index(0)
    rows = []
    for r in range(ws.nrows):
        rows.append(["" if v is None else v for v in ws.row_values(r)])
    rows = _trim_empty(rows)
    columns, n_rows, dtypes, header_mode = _coerce_rows(rows)
    meta = {"file_type": "xls", "sheet": ws.name, "sheets": names,
            "header": header_mode}
    return oerr.ok(columns=columns, n_rows=n_rows, dtypes=dtypes, meta=meta)


def read_table(path, sheet=None):
    """读表格文件 → 统一列式结构。

    Returns:
        {"ok": true, "path", "file_type", "n_rows", "n_columns", "columns",
         "column_types", "preview_rows", ...文件元信息}
    """
    if not path:
        return oerr.fail("invalid_request", "path 不能为空")
    path = str(path)
    if not os.path.exists(path):
        return oerr.fail("file_error", f"文件不存在: {path}", path=path)
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in TEXT_EXTS:
            with open(path, "rb") as f:
                raw = f.read()
            text, encoding = _decode_bytes(raw)
            rows, delim = _rows_from_text(text)
            columns, n_rows, dtypes, header_mode = _coerce_rows(rows)
            meta = {"file_type": ext.lstrip("."), "encoding": encoding,
                    "delimiter": delim, "header": header_mode}
        elif ext in (".xlsx", ".xlsm"):
            r = _read_xlsx(path, sheet)
            if not r.get("ok"):
                return r
            columns, n_rows, dtypes, meta = (r["columns"], r["n_rows"],
                                             r["dtypes"], r["meta"])
        elif ext == ".xls":
            r = _read_xls(path, sheet)
            if not r.get("ok"):
                return r
            columns, n_rows, dtypes, meta = (r["columns"], r["n_rows"],
                                             r["dtypes"], r["meta"])
        else:
            return oerr.fail(
                "file_unsupported_format", f"不支持的文件类型: {ext}",
                supported=sorted(["csv", "txt", "tsv", "dat", "xlsx", "xlsm", "xls"]))
    except Exception as e:
        return oerr.fail("file_read_error", f"读取失败: {e}", path=path)
    if not columns:
        return oerr.fail("file_read_error", "文件中没有可用数据列", path=path)
    names = list(columns)
    preview = [[columns[nm][i] for nm in names]
               for i in range(min(MAX_PREVIEW_ROWS, n_rows))]
    result = oerr.ok(
        path=os.path.abspath(path), n_rows=int(n_rows), n_columns=len(names),
        columns=columns, column_types={nm: dt for nm, dt in dtypes},
        preview_rows=preview, **meta)
    if n_rows > WARN_ROWS:
        result["warning"] = f"数据量较大（{n_rows} 行），写入与画图耗时可能增加"
    return result
