# -*- coding: utf-8 -*-
"""origin_matrix —— 矩阵页读写与矩阵绘图（P1-3，2026-09-16）。

探针实证（本机 Origin 2026 + originpro 1.1.15）：
- 写矩阵必须用 MSheet.from_np（2D 分支会自动设 shape）；from_np2d 不 resize，
  写进 32x32 默认矩阵后读回形状错误；
- 读回用 to_np2d()（首个 MatrixObject）；
- heatmap 走 LabTalk plotm 全变体（im:=/ogl:=/mat:=）实测均静默失败
  （err=False 且 0 plots）——如实拒绝，见 COMPATIBILITY.md #14；
- 矩阵绘图复用 _plot3d_impl / _plot_contour_impl 的已验证路径（读矩阵 ->
  numpy -> 现有实现），不新开 COM 通道。
"""
import numpy as np

import origin_errors as oerr


def _find_matrix(op, name):
    ms = op.find_sheet("m", name)
    return ms


def matrix_write_impl(op, data, matrix_name=None):
    """写矩阵页。data: {'z': [[..],..]} 或直接 [[..],..]（2D 数值网格）。"""
    try:
        z = data.get("z") if isinstance(data, dict) else data
        z2d = np.asarray(z, dtype=float)
        if z2d.ndim != 2 or z2d.size == 0:
            return oerr.fail(
                "invalid_request",
                f"data 需要二维数值网格，收到 shape={getattr(z2d, 'shape', None)}")
        if z2d.shape[0] * z2d.shape[1] > 4_000_000:
            return oerr.fail(
                "invalid_request",
                f"矩阵过大（{z2d.shape[0]}x{z2d.shape[1]} > 400 万点）")
        if matrix_name:
            ms = op.find_sheet("m", matrix_name)
        else:
            ms = op.new_sheet("m", _new_name(op))
        if ms is None:
            ms = op.new_sheet("m", _new_name(op))
        ms.from_np(z2d)                    # 探针实证：自动 resize shape
        arr = ms.to_np2d()
        consistent = (arr.shape == z2d.shape
                      and bool(np.allclose(arr[np.isfinite(arr)],
                                           z2d[np.isfinite(arr)],
                                           equal_nan=True)))
        # MBook.lname 为空（实证），真名在 obj.GetName()
        ref = f"[{ms.get_book().obj.GetName()}]{ms.name}"
        return oerr.ok(
            matrix=ref, rows=int(z2d.shape[0]), cols=int(z2d.shape[1]),
            shape=list(z2d.shape), writeback_consistent=consistent,
            detail=f"已写入 {z2d.shape[0]}x{z2d.shape[1]} 矩阵 -> {ref}"
                   + ("" if consistent else "（警告：读回不一致）"))
    except Exception as e:
        return oerr.from_exception(e)


def _new_name(op):
    import itertools
    for i in itertools.count(1):
        cand = f"M{i:03d}"
        if op.find_sheet("m", cand) is None:
            return cand
    return "M001"


def matrix_read_impl(op, matrix):
    """读矩阵数据为二维列表（含形状）。"""
    try:
        ms = _find_matrix(op, matrix)
        if ms is None:
            return oerr.fail("worksheet_not_found",
                             f"矩阵页不存在: {matrix}",
                             hint="origin_list_pages 可列出矩阵页")
        arr = ms.to_np2d()
        vals = [[(None if not np.isfinite(v) else round(float(v), 10))
                 for v in row] for row in arr]
        return oerr.ok(
            matrix=matrix, shape=[int(arr.shape[0]), int(arr.shape[1])],
            data=vals, detail=f"读取矩阵 {matrix} "
                              f"({arr.shape[0]}x{arr.shape[1]})")
    except Exception as e:
        return oerr.from_exception(e)


def matrix_plot_impl(op, matrix, plot_type="surface", fmt=None, file_path=None,
                     width=1200, title=None):
    """用已有矩阵绘图：surface/scatter/contour/contour_fill/3d_wire。

    读矩阵 -> numpy -> 复用 _plot3d_impl/_plot_contour_impl 已验证路径。
    heatmap 不支持（plotm 探针实证不可用，见 COMPATIBILITY #14）。
    """
    try:
        pt = (plot_type or "surface").lower()
        if pt == "heatmap":
            return oerr.fail(
                "invalid_request",
                "heatmap 暂不支持：LabTalk plotm 全变体实测静默失败"
                "（COMPATIBILITY #14）；可用 contour_fill 近似或 Origin 内手动绘制")
        ms = _find_matrix(op, matrix)
        if ms is None:
            return oerr.fail("worksheet_not_found",
                             f"矩阵页不存在: {matrix}")
        arr = ms.to_np2d()
        if pt != "heatmap" and arr.size == 0:
            return oerr.fail("no_data_to_fit", f"矩阵 {matrix} 为空")
        data = {"z": arr.tolist()}
        # 注意：matrix_plot 自身已在 COM 线程内执行（@_synchronized），必须调
        # **裸 impl**；调 @_synchronized 的公开函数会二次投递队列 => 死锁
        # （2026-09-16 实证：90s 看门狗超时 com_blocked_by_dialog）。
        if pt in ("surface", "scatter"):
            import origin_engine as _eng
            return _eng._plot3d_impl(data, plot_type=pt, fmt=fmt or "png",
                                     file_path=file_path, width=width,
                                     title=title)
        if pt in ("contour", "contour_fill", "3d_wire"):
            import origin_engine as _eng
            return _eng._plot_contour_impl(data, plot_type=pt, fmt=fmt or "png",
                                           file_path=file_path, width=width,
                                           title=title)
        return oerr.fail("invalid_request",
                         f"plot_type 只支持 surface/scatter/contour/"
                         f"contour_fill/3d_wire，收到 {pt!r}")
    except Exception as e:
        return oerr.from_exception(e)
