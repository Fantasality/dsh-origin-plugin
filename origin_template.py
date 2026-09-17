# -*- coding: utf-8 -*-
"""origin_template —— 用户模板资产（save / list / apply）
===========================================================

动机（为什么做这个模块）
-----------------------
竞品 Ge-Shun/origin-mcp 有「把图存成可复用用户模板 → 按图型搜索匹配 → 应用到同类图」
的能力，这是课题组统一出图风格的刚需；本项目此前缺这一环。

通道实测结论（真机探针，本机 Origin 2026 + originpro 1.1.15）
----------------------------------------------------------
- LabTalk `save -i <path>.otpu` / `save -t` / `save -ib` / `save -it`：
  `LT_execute` 返回 True，但**磁盘上完全不生成文件**（静默失败，无任何异常）。
- COM 通道：`GraphPage` 对象（`OriginExt.GraphPage`）上**不存在** `SaveTemplate`/
  `SaveAsTemplate` 等任何模板方法（`dir(gp.obj)` 过滤 templ/save 为空）。
- 反向确认：全盘搜索 *.otpu 无新增文件 → 两条「真·.otpu 模板」通道在本环境均不可用。

因此按任务约定的降级方案落地：把图页的样式（颜色/线宽/线型/符号/透明度/可见性、
轴标题/量程/刻度/字体、图例、页面尺寸/背景、图层几何）以 **JSON 侧车文件**完整
记录（100% 可用），并最佳努力另存一份 `.opju` 项目快照作备份。apply 时读回 JSON
逐项套用到目标图，复用 origin_edit 已验证的「写入必读回、逐项 status」编辑函数，
保证与项目现有纪律一致。

设计约束（沿用项目铁律）
-----------------------
- 本模块 `*_impl(op, ...)` 只在引擎专用 COM 线程内被调用，op 即 originpro 模块对象；
  严禁在 COM 线程内调用 @_synchronized 公开函数（会二次投递队列 → 死锁）。
- 公开入口用 engine 的 @_synchronized 装饰，内部先 _connect_impl() 再调裸 impl。
- 错误用 origin_errors 的 oerr.ok/fail/from_exception；新错误码待宿主补（见末尾说明）。
- 所有改 Origin 动作都委托 origin_edit（COM 优先、激活复核、逐项读回）。
"""
from __future__ import annotations

import json
import os
import re

import origin_errors as oerr
import origin_edit as oedit
# origin_engine 仅用于：@_synchronized 装饰器、_connect_impl、_origin_app（全局）
import origin_engine as _eng
from origin_engine import _synchronized


# ---------------------------------------------------------------------------
# 模板目录
# ---------------------------------------------------------------------------
def _snapshot_dir():
    """插件的样式快照根目录（可被 DSH_ORIGIN_TEMPLATE_DIR 覆盖）。"""
    d = os.environ.get("DSH_ORIGIN_TEMPLATE_DIR")
    if not d:
        d = os.path.join(os.path.expanduser("~"), ".dsh", "origin_templates")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _user_template_dirs(op):
    """Origin 用户模板目录候选（User Files\\Templates）。

    优先用 op.path() 拿到的用户文件根（本机实测在 Documents\\OriginLab\\User Files）；
    再补 %APPDATA%\\OriginLab\\<版本>\\User Files\\Templates（任务建议的优先路径）。
    """
    dirs = []
    if op is not None:
        try:
            uf = op.path()                       # ...\Documents\OriginLab\User Files\
            cand = os.path.join(uf, "Templates")
            if os.path.isdir(cand):
                dirs.append(cand)
        except Exception:
            pass
    appdata = os.environ.get("APPDATA")
    if appdata:
        root = os.path.join(appdata, "OriginLab")
        if os.path.isdir(root):
            for name in os.listdir(root):
                cand = os.path.join(root, name, "User Files", "Templates")
                if os.path.isdir(cand):
                    dirs.append(cand)
    return dirs


def _safe_cat(cat):
    """把 category 规整成安全子目录名。"""
    cat = str(cat).strip().replace("\\", "/").strip("/")
    cat = re.sub(r"[^\w\-]+", "_", cat)
    return cat[:40] or "default"


def _read_meta(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else None
    except Exception:
        return None


def _find_template(name):
    """按模板名找样式快照 JSON（含 category 子目录），返回 (meta, path) 或 (None, None)。"""
    root = _snapshot_dir()
    # 先按文件名精确匹配
    direct = os.path.join(root, f"{name}.json")
    if os.path.exists(direct):
        m = _read_meta(direct)
        if m and m.get("_dsh_template"):
            return m, direct
    # 再递归任意子目录
    for base, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            p = os.path.join(base, fn)
            m = _read_meta(p)
            if m and m.get("_dsh_template") and m.get("template_name") == name:
                return m, p
    return None, None


# ---------------------------------------------------------------------------
# 样式快照采集（COM 线程内）
# ---------------------------------------------------------------------------
def _capture_style(op, po, graph):
    """巡检目标图页并完整记录可套用样式，返回 (snap, err)。

    字段命名与 origin_edit.edit_* 入参一一对应，套用时直接映射。
    """
    short, err = oedit.ensure_active_graph(op, po, graph)
    if err:
        return None, err
    gp = op.find_graph(short)
    if gp is None:
        return None, oerr.fail("graph_not_found", f"图不存在: {graph}", graph=str(graph))

    snap = {"graph": short, "page": {}, "layers": []}

    # 页面尺寸（cm）与背景
    w_dot, _ = oedit._safe(gp.get_int, "width")
    h_dot, _ = oedit._safe(gp.get_int, "height")
    rx = oedit.lt_float(po, "page.resx") or 600.0
    ry = oedit.lt_float(po, "page.resy") or 600.0
    if isinstance(w_dot, (int, float)) and rx:
        snap["page"]["width_cm"] = round(float(w_dot) / rx * 2.54, 3)
    if isinstance(h_dot, (int, float)) and ry:
        snap["page"]["height_cm"] = round(float(h_dot) / ry * 2.54, 3)
    bc = oedit.lt_float(po, "page.baseColor")
    if bc is not None:
        snap["page"]["background"] = int(bc)

    n_layers, _ = oedit._safe(lambda: gp.obj.Layers.Count)
    for li in range(int(n_layers or 0)):
        gl, _ = oedit._safe(gp.__getitem__, li)
        if gl is None:
            continue
        layer = {"index": li, "geometry": {}, "axes": {}, "plots": []}

        # 图层几何（%page）：临时切 unit=1 读回后还原，避免污染源图
        unit0, _ = oedit._safe(gl.get_int, "unit")
        oedit._safe(gl.set_int, "unit", 1)
        for k in ("left", "top", "width", "height"):
            v, _ = oedit._safe(gl.get_float, k)
            if v is None:
                v, _ = oedit._safe(gl.get_int, k)
            if v is not None:
                layer["geometry"][k] = round(float(v), 4)
        if unit0 is not None:
            oedit._safe(gl.set_int, "unit", int(unit0))

        # 轴
        for ax in ("x", "y"):
            info = {}
            t, _ = oedit._safe(lambda a=ax: gl.axis(a).title)
            if t is not None:
                info["title"] = str(t)
            lim, _ = oedit._safe(lambda a=ax: gl.axis(a).limits)
            if lim is not None:
                lims = [float(x) if isinstance(x, (int, float)) else x
                        for x in (lim if isinstance(lim, (tuple, list)) else [lim])]
                if len(lims) >= 2:
                    info["from"] = lims[0]
                    info["to"] = lims[1]
            sc, _ = oedit._safe(lambda a=ax: gl.axis(a).scale)
            if sc is not None:
                info["scale"] = int(sc)
            for prop, key in (("showgrids", "show_grids"), ("showAxes", "show_axes"),
                              ("opposite", "opposite"), ("ticklength", "tick_length"),
                              ("reverse", "reverse"), ("label.bold", "label_bold"),
                              ("label.fsize", "label_font_pt"),
                              ("label.type", "label_type"),
                              ("label.decPlaces", "label_decimals"),
                              ("grid.majorType", "grid_major_type"),
                              ("minorticks", "minor_ticks")):
                v, _ = oedit._safe(gl.get_int, f"{ax}.{prop}")
                if v is not None:
                    info[key] = int(v)
            inc, _ = oedit._safe(gl.get_float, f"{ax}.inc")
            if inc is not None:
                info["major_increment"] = float(inc)
            if info:
                layer["axes"][ax] = info

        # 曲线（逐项：颜色/线宽/线型/符号/透明度/可见性）
        plots, _ = oedit._safe(gl.plot_list)
        for i, p in enumerate(plots or []):
            item = {"index": i}
            nm, _ = oedit._safe(getattr, p, "name")
            if nm:
                item["dataset"] = str(nm)
            c, _ = oedit._safe(getattr, p, "color")
            if c is not None:
                try:
                    item["color"] = oedit._rgb_str(c)
                except Exception:
                    pass
            lw, _ = oedit._safe(p.get_float, "linewidth")
            if lw is not None:
                item["line_width_pt"] = float(lw)
            ls, _ = oedit._safe(p.get_int, "linestyle")
            if ls is not None:
                item["line_style"] = int(ls)
            sk, _ = oedit._safe(getattr, p, "symbol_kind")
            if sk is not None:
                item["symbol_kind"] = int(sk)
            ss, _ = oedit._safe(getattr, p, "symbol_size")
            if ss is not None:
                item["symbol_size"] = float(ss)
            si, _ = oedit._safe(getattr, p, "symbol_interior")
            if si is not None:
                item["symbol_interior"] = int(si)
            tr, _ = oedit._safe(getattr, p, "transparency")
            if tr is not None:
                item["transparency"] = float(tr)
            sh, _ = oedit._safe(p.get_int, "show")
            if sh is not None:
                item["visible"] = bool(sh)
            layer["plots"].append(item)
        snap["layers"].append(layer)

    # 图例
    legend = {}
    lgnd, _ = oedit._safe(lambda: gp[0].label("Legend"))
    if lgnd is not None:
        sh = oedit.lt_float(po, "legend.show")
        if sh is not None:
            legend["show"] = int(sh)
        fs, _ = oedit._safe(lgnd.get_int, "fsize")
        if fs is not None:
            legend["font_size_pt"] = int(fs)
        bg, _ = oedit._safe(lgnd.get_int, "background")
        if bg is not None:
            legend["background"] = int(bg)
        txt, _ = oedit._safe(getattr, lgnd, "text")
        if txt is not None:
            legend["text"] = str(txt)
        lx, _ = oedit._safe(lgnd.get_int, "left")
        ly, _ = oedit._safe(lgnd.get_int, "top")
        if lx is not None and ly is not None:
            legend["position"] = {"left": int(lx), "top": int(ly)}
    if legend:
        snap["legend"] = legend

    return snap, None


# ---------------------------------------------------------------------------
# 套用映射辅助（COM 线程内；全部委托 origin_edit 的逐项 status 编辑函数）
# ---------------------------------------------------------------------------
def _apply_axis(op, po, short, ax, ad, li):
    """把采集到的单轴样式映射成 origin_edit.edit_axis 调用。"""
    kwargs = {}
    if "title" in ad:
        kwargs["title"] = ad["title"]
    if "from" in ad:
        kwargs["from_"] = ad["from"]
    if "to" in ad:
        kwargs["to"] = ad["to"]
    if "scale" in ad:
        kwargs["scale"] = ad["scale"]
    for k in ("show_grids", "show_axes", "opposite", "tick_length", "reverse",
              "label_bold", "label_font_pt", "label_type", "label_decimals",
              "grid_major_type", "minor_ticks", "major_increment"):
        if k in ad:
            kwargs[k] = ad[k]
    if not kwargs:
        return None
    return oedit.edit_axis(op, po, short, axis=ax, layer=li, **kwargs)


def _plot_edit(p, li):
    """把单条曲线采集样式映射成 edit_plot 的 edits 项。"""
    e = {"plot": p["index"], "layer": li}
    for k in ("color", "line_width_pt", "line_style", "symbol_kind",
              "symbol_size", "symbol_interior", "transparency", "visible"):
        if k in p:
            e[k] = p[k]
    return e


def _legend_kwargs(legend):
    k = {}
    if "show" in legend:
        k["visible"] = bool(legend["show"])
    if "font_size_pt" in legend:
        k["font_size_pt"] = legend["font_size_pt"]
    if "background" in legend:
        k["background"] = legend["background"]
    if "text" in legend:
        k["text"] = legend["text"]
    return k


# ===========================================================================
# 裸 impl（COM 线程内调用）
# ===========================================================================
def template_save_impl(op, graph, template_name, category=None, overwrite=False):
    """把当前图页存为可复用用户模板（样式快照降级方案）。

    先采集完整样式写入 <name>.json 侧车（主资产，100% 可用），再最佳努力另存
    <name>.opju 项目快照作备份（失败不致命）。
    """
    try:
        if not template_name or template_name in (".", "..") or "/" in template_name \
                or "\\" in template_name:
            return oerr.fail("invalid_request",
                             f"template_name 非法: {template_name!r}（不能含路径分隔符）",
                             template_name=template_name)
        snap, err = _capture_style(op, op.po, graph)
        if err:
            return err

        base = _snapshot_dir()
        if category:
            base = os.path.join(base, _safe_cat(category))
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, f"{template_name}.json")

        if os.path.exists(path) and not overwrite:
            return oerr.fail("template_exists",
                             f"模板 {template_name!r}（category={category}）已存在，"
                             f"需传 overwrite=True 覆盖",
                             template=template_name, category=category, path=path)

        meta = {
            "_dsh_template": True,
            "engine": "origin_template_style_snapshot",
            "version": 1,
            "template_name": template_name,
            "category": category,
            "source_graph": snap.get("graph"),
            "note": ("Origin 2026 LabTalk save -i 实测无法生成 .otpu，COM 也无 "
                     "SaveTemplate；故采用 JSON 样式快照（套用风格 100% 可用）。"),
            "style": snap,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        # 最佳努力：另存 .opju 项目快照（参考/备份，失败不致命）
        opju_path = os.path.join(base, f"{template_name}.opju")
        opju_ok = False
        try:
            if os.path.exists(opju_path):
                os.remove(opju_path)
            op.save(opju_path)
            opju_ok = os.path.exists(opju_path)
        except Exception:
            opju_ok = False

        n_plots = sum(len(l.get("plots", [])) for l in snap.get("layers", []))
        return oerr.ok(
            template=template_name, category=category, path=path,
            opju_path=(opju_path if opju_ok else None),
            style_snapshot=True, n_layers=len(snap.get("layers", [])),
            n_plots=n_plots,
            detail=(f"已保存样式快照模板 {template_name!r}（{len(snap.get('layers', []))} 层、"
                     f"{n_plots} 条曲线）到 {path}"
                     + ("" if opju_ok else "（.opju 备份未生成，不影响套用）")))
    except Exception as e:
        return oerr.from_exception(e)


def template_list_impl(op, category=None):
    """列出可用模板：插件样式快照目录（含 category 子目录）+ Origin 用户模板目录。"""
    try:
        found = []
        # 1) 插件样式快照
        root = _snapshot_dir()
        for base, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".json"):
                    continue
                p = os.path.join(base, fn)
                m = _read_meta(p)
                if not m or not m.get("_dsh_template"):
                    continue
                cat = m.get("category")
                if not cat:
                    rel = os.path.relpath(base, root)
                    cat = rel if rel and rel != "." else None
                if category and cat != category:
                    continue
                found.append({
                    "name": m.get("template_name") or fn[:-5],
                    "category": cat, "source": "dsh_snapshot",
                    "kind": "style_snapshot", "has_style_json": True,
                    "path": p, "size": os.path.getsize(p),
                    "n_layers": len((m.get("style") or {}).get("layers", [])),
                })
        # 2) Origin 用户模板目录里的 .otpu/.opju（若有，缺 JSON 侧车）
        for tdir in _user_template_dirs(op):
            try:
                names = os.listdir(tdir)
            except Exception:
                names = []
            for fn in names:
                low = fn.lower()
                if not (low.endswith(".otpu") or low.endswith(".opju")):
                    continue
                full = os.path.join(tdir, fn)
                name = fn[:-5]
                if category and os.path.basename(tdir) != category:
                    continue
                found.append({
                    "name": name, "category": os.path.basename(tdir) or None,
                    "source": "origin_user_files", "kind": low[-4:],
                    "has_style_json": False, "path": full,
                    "size": os.path.getsize(full),
                })
        return oerr.ok(
            templates=found, count=len(found),
            detail=(f"共列出 {len(found)} 个模板（含样式快照 "
                    f"{sum(1 for t in found if t['has_style_json'])} 个）"))
    except Exception as e:
        return oerr.from_exception(e)


def template_apply_impl(op, graph, template_name):
    """把模板样式套用到指定图：读回 JSON 侧车，逐项调用 origin_edit 套用并收集 status。"""
    try:
        meta, path = _find_template(template_name)
        if meta is None:
            return oerr.fail("template_not_found",
                             f"未找到模板 {template_name!r}（用 origin_template_list 查看可用模板）",
                             template=template_name)
        short, err = oedit.ensure_active_graph(op, op.po, graph)
        if err:
            return err
        style = meta.get("style") or {}
        if not style.get("layers"):
            return oerr.fail("no_style_snapshot",
                             f"模板 {template_name!r} 不含可套用样式（可能只有 .opju 缺 JSON 侧车）",
                             template=template_name, path=path)

        all_changes = []

        # 页面尺寸/背景（一次性）
        page = style.get("page", {})
        pk = {}
        if "width_cm" in page or "height_cm" in page:
            pk["page_size_cm"] = {}
            if "width_cm" in page:
                pk["page_size_cm"]["width"] = page["width_cm"]
            if "height_cm" in page:
                pk["page_size_cm"]["height"] = page["height_cm"]
        if "background" in page:
            pk["background"] = page["background"]
        if pk:
            r = oedit.edit_page(op, op.po, short, **pk)
            all_changes += (r.get("changes") or [])

        # 逐层：几何 / 轴 / 曲线
        for li, layer in enumerate(style.get("layers", [])):
            geo = layer.get("geometry")
            if geo and all(k in geo for k in ("left", "top", "width", "height")):
                r = oedit.edit_page(op, op.po, short, layer=li,
                                    layer_geometry_pct=geo)
                all_changes += (r.get("changes") or [])
            for ax in ("x", "y"):
                ad = layer.get("axes", {}).get(ax)
                if ad:
                    r = _apply_axis(op, op.po, short, ax, ad, li)
                    if r is not None:
                        all_changes += (r.get("changes") or [])
            pedits = [_plot_edit(p, li) for p in layer.get("plots", [])]
            if pedits:
                r = oedit.edit_plot(op, op.po, short, pedits)
                all_changes += (r.get("changes") or [])

        # 图例
        legend = style.get("legend")
        if legend:
            r = oedit.edit_legend(op, op.po, short, **_legend_kwargs(legend))
            all_changes += (r.get("changes") or [])

        counts = {}
        for c in all_changes:
            s = c.get("status", "unknown")
            counts[s] = counts.get(s, 0) + 1
        return oerr.ok(
            graph=short, template=template_name, path=path,
            changes=all_changes, n_total=len(all_changes),
            status_counts=counts, n_applied=counts.get("applied", 0),
            detail=(f"套用模板 {template_name!r} 到 {short}："
                    f"{len(all_changes)} 项改动，状态分布 {counts}"))
    except Exception as e:
        return oerr.from_exception(e)


# ===========================================================================
# 公开入口（@_synchronized：投递到专用 COM 线程串行执行）
# ===========================================================================
@_synchronized
def template_save(graph, template_name, category=None, overwrite=False):
    """MCP 工具 origin_template_save：把当前图页存为可复用用户模板。

    Args:
        graph: 目标图页短名（先 origin_list_graphs 取）。
        template_name: 模板名（不含路径分隔符）。
        category: 可选分类，落到 <快照目录>/<category>/ 子目录。
        overwrite: 同名已存在时是否覆盖，默认 False（冲突返回 template_exists）。
    """
    try:
        ok, conn = _eng._connect_impl()
        if not ok:
            return conn
        return template_save_impl(_eng._origin_app, graph, template_name,
                                 category=category, overwrite=overwrite)
    except Exception as e:
        return oerr.from_exception(e)


@_synchronized
def template_list(category=None):
    """MCP 工具 origin_template_list：列出可用模板（快照目录 + Origin 用户模板目录）。

    不强制连接 Origin：仅列插件快照目录；已连接时一并扫描用户模板目录。
    """
    try:
        op = None
        try:
            ok, conn = _eng._connect_impl()
            if ok:
                op = _eng._origin_app
        except Exception:
            op = None
        return template_list_impl(op, category)
    except Exception as e:
        return oerr.from_exception(e)


@_synchronized
def template_apply(graph, template_name):
    """MCP 工具 origin_template_apply：把模板样式套用到指定图。

    逐项应用颜色/线宽/线型/符号/轴/图例，每项返回 status
    （applied / applied_unverified / rejected / unsupported 等）。
    """
    try:
        ok, conn = _eng._connect_impl()
        if not ok:
            return conn
        return template_apply_impl(_eng._origin_app, graph, template_name)
    except Exception as e:
        return oerr.from_exception(e)


# ---------------------------------------------------------------------------
# 待宿主补的错误码（本模块用到但 origin_errors.py 当前没有，按纪律不改动该文件）：
#   - template_exists     模板同名已存在（建议 recoverable=True，next_actions=覆盖提示）
#   - template_not_found 模板不存在（recoverable=True，引导先 template_list）
#   - no_style_snapshot  模板缺可套用样式（recoverable=True）
# 复用已有码：graph_not_found / invalid_request / com_blocked_by_dialog
# ---------------------------------------------------------------------------
