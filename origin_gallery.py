# -*- coding: utf-8 -*-
"""origin_gallery —— OriginLab Graph Gallery 模板搜索/下载（P2-5 基础版）。

参考 GG-pro-viki/Origin 的下载脚本思路：构造 Graph Gallery 搜索 URL ->
解析详情页链接 -> 下载 .zip。用浏览器式 User-Agent/Referer（403 缓解）。
页面结构变化时会如实报告（不猜）。
"""
import os
import re
import urllib.parse
import urllib.request

import origin_errors as oerr

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_SEARCH_URL = ("https://www.originlab.com/www/products/GraphGallery.aspx"
               "?s=1&k={kw}&sort=Newest")
_DETAIL_URL = "https://www.originlab.com/graphgallery/gallerydetail.aspx?ID={gid}"


def _fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "https://www.originlab.com/graphgallery.aspx",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def search_impl(keyword, max_items=5, download_dir=None):
    """搜索模板并（可选）下载 zip。失败/结构变化时如实返回，不猜。"""
    try:
        kw = urllib.parse.quote(str(keyword))
        html = _fetch(_SEARCH_URL.format(kw=kw)).decode("utf-8", "replace")
        gids = []
        for m in re.finditer(r"gallerydetail\.aspx\?ID=(\d+)", html):
            if m.group(1) not in gids:
                gids.append(m.group(1))
        gids = gids[:max(1, min(int(max_items), 20))]
        if not gids:
            return oerr.ok(keyword=str(keyword), found=0,
                            detail="搜索页无结果或页面结构已变化（未发现详情链接）；"
                                   "可到 originlab.com/graphgallery 手动确认关键词")
        items = []
        for gid in gids:
            item = {"id": gid, "detail_url": _DETAIL_URL.format(gid=gid)}
            if download_dir:
                try:
                    dhtml = _fetch(item["detail_url"]).decode("utf-8", "replace")
                    zm = re.search(r'href="([^"]+?\.zip[^"]*)"', dhtml)
                    if zm:
                        zurl = zm.group(1)
                        if zurl.startswith("/"):
                            zurl = "https://www.originlab.com" + zurl
                        zdata = _fetch(zurl)
                        os.makedirs(download_dir, exist_ok=True)
                        zpath = os.path.join(
                            download_dir, f"origin_gallery_{gid}.zip")
                        with open(zpath, "wb") as f:
                            f.write(zdata)
                        item["zip"] = zpath
                        item["zip_size"] = len(zdata)
                    else:
                        item["zip"] = None
                        item["note"] = "详情页未解析到 .zip 链接（可能需登录/结构变化）"
                except Exception as e:
                    item["error"] = str(e)[:120]
            items.append(item)
        n_zip = sum(1 for i in items if i.get("zip"))
        return oerr.ok(keyword=str(keyword), found=len(items), items=items,
                       downloaded=n_zip,
                       detail=f"找到 {len(items)} 个模板（{n_zip} 个已下载）")
    except Exception as e:
        return oerr.fail("network_error",
                         f"Graph Gallery 访问失败: {e}",
                         hint="检查网络；403 时稍后重试或缩小关键词；"
                              "也可手动到 originlab.com/graphgallery 下载")
