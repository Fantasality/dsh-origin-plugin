# -*- coding: utf-8 -*-
"""
ltcol_probe —— LabTalk 列公式赋值语法穷举探针
==============================================

背景：`range __rc = [BookN]Sheet1!col(3); __rc = log10(col(2));` 返回 nan，
说明 range 作用域 + 公式右值 的组合未生效。本探针穷举候选语法，逐条读回，
找出真正能写入列值的写法。

候选族：
  A. range-range 双向（range src = col(2); range dst = col(3); dst = log(src);）
  B. 激活工作表后相对列名 col(C) = col(B)*2
  C. 全引用赋值 [Book]Sheet!col(3) = [Book]Sheet!col(2)
  D. wcol() 函数式：wcol(3) = log10(wcol(2));
  E. 数据集名赋值：col(3) = log10(col(2))（激活后）
  F. Set Column Values 全命令 col(A) = ... / SetData
  G. range + LT 内建函数极简：range r2=2; r3=3; col(r3)=col(r2)^2
  H. 目标列先设 Numeric 类型 + format
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import origin_engine as engine  # noqa: E402
import origin_edit as oedit     # noqa: E402

R = {}


def main():
    ok, _ = engine._connect_impl()
    if not ok:
        sys.exit(2)
    op = engine._origin_app
    po = op.po

    w = op.new_sheet("w")
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    w.from_list(0, x, lname="x")
    R["x"] = x
    ncol0 = int(w.obj.Cols)
    R["ncol_before"] = ncol0
    # 预建 8 个目标列（col2..col9），确保列存在
    while int(w.obj.Cols) < 9:
        w.obj.Cols = int(w.obj.Cols) + 1
    R["ncol_after"] = int(w.obj.Cols)

    short = str(w)                      # [BookN]Sheet1
    book = short.split("]")[0][1:]      # BookN
    sheet = short.split("]")[1]         # Sheet1
    R["ref"] = {"short": short, "book": book, "sheet": sheet}

    # 列类型信息
    try:
        cols_info = []
        for j in range(int(w.obj.Cols)):
            c = w.obj[j]
            cols_info.append({"i": j, "lname": c.GetLongName(),
                              "type": c.GetType(), "name": c.GetName()})
        R["cols"] = cols_info
    except Exception as e:  # noqa: BLE001
        R["cols_err"] = str(e)[:120]

    def read_col(idx):
        try:
            return w.to_list(idx)[:3]
        except Exception as e:  # noqa: BLE001
            return f"ERR {e}"

    def run(tag, script, col_idx):
        err = oedit.lt_exec(po, script)
        R[tag] = {"script": script, "err": (str(err)[:140] if err else None),
                  "col%d" % col_idx: read_col(col_idx)}

    # ---- A. range-range ----
    run("A_range_range", (
        f"range __o = {short}!col(2);"
        f"range __t = {short}!col(3);"
        f"__t = ln(__o);"), 2)

    # ---- B. 先激活，相对列名 ----
    oedit.lt_exec(po, f"win -a {book};")
    run("B_active_colname", "col(3) = log10(col(2))*100;", 2)

    # ---- C. 全引用 ----
    run("C_fullref", f"{short}!col(4) = {short}!col(2)*1000;", 3)

    # ---- D. wcol() 函数式 ----
    run("D_wcol", "wcol(5) = wcol(2)*2 + 1;", 4)

    # ---- D2. wcol 全引用 ----
    run("D2_wcol_ref", f"wcol(6) = wcol(2) + 7;", 5)

    # ---- E. 数据集名形式 Book_B ----
    run("E_dataset", f"{book}_{chr(66)} = {book}_{chr(65)} * 3;", 1)

    # ---- F. col() 简写赋值（激活后）----
    run("F_col_assign", "col(7) = col(2)^2;", 6)

    # ---- G. range 数字引用 ----
    run("G_range_num", "range __r2 = 2; range __r7 = 7; __r7 = exp(__r2);", 6)

    # ---- H. Set Column Values 命令 ----
    run("H_setcolval", f"SetColumnValues irng:=[{book}]{sheet}!col(8) "
                       f"formula:=col(2)*5;", 7)

    # ---- I. 分析：跑完后再读全部列 ----
    R["final_all"] = {("col%d" % (j + 1)): read_col(j)
                      for j in range(int(w.obj.Cols))}

    print(json.dumps(R, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
