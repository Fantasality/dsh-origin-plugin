# -*- coding: utf-8 -*-
"""
origin_analysis —— 纯 numpy 统计分析（clean-room 设计）
=======================================================

为引擎补充官方 X-Function 之外也能稳定算出的统计批（不依赖 scipy）：

- ttest_one_sample / ttest_two_sample / ttest_paired（Welch 修正，t 分布 p 值）
- anova_oneway（单因素方差分析 F 检验）
- pca（主成分分析：载荷/解释方差/得分）
- kaplan_meier（生存分析 KM 估计：事件表/中位生存）

t/F 分布的 p 值用正则化不完全贝塔函数 I(x; a, b) 的标准连分数/级数实现
（数值分析教材经典算法，独立编码）。所有函数输入 numpy 数组、输出 dict，
便于脱离 Origin 单测。
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# 数值基础：Gamma / 不完全贝塔
# ---------------------------------------------------------------------------
_LANCZOS_G = 7
_LANCZOS_C = [
    0.99999999999980993, 676.5203681218851, -1259.1392167224028,
    771.32342877765313, -176.61502916214059, 12.507343278686905,
    -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7,
]


def _gammaln(x: float) -> float:
    if x < 0.5:
        return math.log(math.pi / math.sin(math.pi * x)) - _gammaln(1.0 - x)
    z = x - 1.0
    acc = 0.99999999999980993
    for i, c in enumerate(_LANCZOS_C[1:], start=1):
        acc += c / (z + i)
    t = z + _LANCZOS_G + 0.5
    return 0.5 * math.log(2 * math.pi) + (z + 0.5) * math.log(t) - t + math.log(acc)


def _betacf(a: float, b: float, x: float) -> float:
    """不完全贝塔连分数（Lentz 算法），仅 x<(a+1)/(a+b+2) 方向使用。"""
    max_iter, eps = 300, 3e-10
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _incbeta(a: float, b: float, x: float) -> float:
    """正则化不完全贝塔函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = (_gammaln(a + b) - _gammaln(a) - _gammaln(b)
             + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_sf(t: float, df: float) -> float:
    """t 分布双侧尾概率（即双侧 p 值）。"""
    td = max(1e-12, float(df))
    x = td / (td + t * t)
    return float(_incbeta(td / 2.0, 0.5, x))


def _f_sf(f: float, df1: float, df2: float) -> float:
    """F 分布右尾概率。"""
    d1, d2 = max(1e-12, float(df1)), max(1e-12, float(df2))
    x = d1 * f / (d1 * f + d2)
    return float(_incbeta(d2 / 2.0, d1 / 2.0, 1.0 - x))


# ---------------------------------------------------------------------------
# 统计批
# ---------------------------------------------------------------------------
def _clean(v: np.ndarray, name: str = "data") -> np.ndarray:
    a = np.asarray(v, dtype=float)
    a = a[np.isfinite(a)]
    return a


def ttest_one_sample(data: Sequence[float], mu: float = 0.0,
                     alternative: str = "two_sided") -> Dict:
    """单样本 t 检验：H0: mean = mu。"""
    v = _clean(data)
    n = v.size
    if n < 2:
        return {"ok": False, "error": "有效样本不足 2"}
    m = float(v.mean())
    s = float(v.std(ddof=1))
    se = s / math.sqrt(n)
    stat = (m - mu) / se if se else 0.0
    df = n - 1.0
    p = _t_sf(stat, df)
    if alternative == "greater":
        p = p / 2
    elif alternative == "less":
        p = p / 2
    return {"ok": True, "kind": "one_sample", "statistic": stat, "df": df,
            "p_value": min(1.0, p), "mean": m, "std": s, "n": int(n), "mu": mu}


def ttest_two_sample(a: Sequence[float], b: Sequence[float],
                     alternative: str = "two_sided") -> Dict:
    """双样本 t 检验（Welch，不假设方差齐性）。"""
    va, vb = _clean(a), _clean(b)
    na, nb = va.size, vb.size
    if na < 2 or nb < 2:
        return {"ok": False, "error": "任一组有效样本不足 2"}
    ma, mb = float(va.mean()), float(vb.mean())
    sa2, sb2 = float(va.var(ddof=1)), float(vb.var(ddof=1))
    stat = (ma - mb) / math.sqrt(sa2 / na + sb2 / nb) if (sa2 / na + sb2 / nb) else 0.0
    num = (sa2 / na + sb2 / nb) ** 2
    den = (sa2 / na) ** 2 / (na - 1) + (sb2 / nb) ** 2 / (nb - 1)
    df = num / den if den else 0.0
    p = _t_sf(stat, df)
    if alternative == "greater":
        p = p / 2
    elif alternative == "less":
        p = p / 2
    return {"ok": True, "kind": "two_sample_welch", "statistic": stat, "df": df,
            "p_value": min(1.0, p), "mean_a": ma, "mean_b": mb,
            "std_a": math.sqrt(sa2), "std_b": math.sqrt(sb2), "n_a": int(na), "n_b": int(nb)}


def ttest_paired(a: Sequence[float], b: Sequence[float],
                 alternative: str = "two_sided") -> Dict:
    """配对 t 检验：对差值做单样本检验。"""
    va, vb = _clean(a), _clean(b)
    n = min(va.size, vb.size)
    if n < 2:
        return {"ok": False, "error": "配对有效样本不足 2"}
    d = va[:n] - vb[:n]
    d = d[np.isfinite(d)]
    n = d.size
    if n < 2:
        return {"ok": False, "error": "配对差值有效样本不足 2"}
    m = float(d.mean())
    s = float(d.std(ddof=1))
    stat = m / (s / math.sqrt(n)) if s else 0.0
    df = n - 1.0
    p = _t_sf(stat, df)
    if alternative == "greater":
        p = p / 2
    elif alternative == "less":
        p = p / 2
    return {"ok": True, "kind": "paired", "statistic": stat, "df": df,
            "p_value": min(1.0, p), "mean_diff": m, "std_diff": s, "n": int(n)}


def anova_oneway(groups: Sequence[Sequence[float]]) -> Dict:
    """单因素方差分析：F 检验，返回组间 SS/组内 SS/F/p。"""
    gs = [_clean(g) for g in groups]
    gs = [g for g in gs if g.size > 0]
    k = len(gs)
    if k < 2:
        return {"ok": False, "error": "需要至少 2 组"}
    sizes = [g.size for g in gs]
    if any(s < 2 for s in sizes):
        return {"ok": False, "error": "每组需要至少 2 个样本"}
    n = sum(sizes)
    means = [float(g.mean()) for g in gs]
    grand = float(np.concatenate(gs).mean())
    ssb = sum(sz * (m - grand) ** 2 for sz, m in zip(sizes, means))
    ssw = sum(float(((g - g.mean()) ** 2).sum()) for g in gs)
    d1, d2 = k - 1, n - k
    msb, msw = ssb / d1, (ssw / d2) if d2 else 0.0
    f = msb / msw if msw else float("inf")
    p = _f_sf(f, d1, d2) if math.isfinite(f) else 0.0
    return {"ok": True, "k_groups": k, "n_total": int(n),
            "group_means": means, "group_sizes": sizes,
            "ss_between": ssb, "ss_within": ssw,
            "df_between": int(d1), "df_within": int(d2),
            "f_statistic": f, "p_value": min(1.0, p)}


def pca(matrix: Sequence[Sequence[float]], *, center: bool = True,
        scale: bool = False, n_components: Optional[int] = None) -> Dict:
    """主成分分析：SVD 实现。

    matrix: (n_samples, n_features)。默认数据中心化；scale=True 时标准化到单位方差。
    返回: 特征值(解释方差)、载荷矩阵(每列一个主成分)、各主成分解释方差比、得分。
    """
    X = np.asarray(matrix, dtype=float)
    if X.ndim != 2 or X.size == 0:
        return {"ok": False, "error": "需要 2D 数值矩阵"}
    if center:
        X = X - X.mean(axis=0)
    if scale:
        sd = X.std(axis=0)
        X = X / np.where(sd == 0, 1.0, sd)
    U, S, Vt = np.linalg.svd(X, full_matrices=False)
    evals = (S ** 2) / max(1, X.shape[0] - 1)
    total = evals.sum()
    k = n_components or evals.size
    k = max(1, min(int(k), evals.size))
    loadings = [list(Vt[i, :]) for i in range(k)]
    scores = [list(U[:, i] * S[i]) for i in range(k)]
    return {"ok": True,
            "n_samples": int(X.shape[0]), "n_features": int(X.shape[1]),
            "eigenvalues": [float(e) for e in evals[:k]],
            "explained_variance_ratio": [float(e / total) if total else 0.0 for e in evals[:k]],
            "cumulative_explained_variance": [float(evals[:i + 1].sum() / total) if total else 0.0
                                              for i in range(k)],
            "loadings": loadings, "scores": scores,
            "n_components": k}


def kaplan_meier(times: Sequence[float], events: Sequence[int]) -> Dict:
    """Kaplan-Meier 生存估计（含中位生存时间）。

    events: 1=事件发生, 0=删失(censored)。
    返回: 时间/风险数/事件数/生存概率表 + 中位生存时间。
    """
    t = np.asarray(times, dtype=float)
    e = np.asarray(events, dtype=int)
    mask = np.isfinite(t)
    t, e = t[mask], e[mask]
    n = t.size
    if n == 0:
        return {"ok": False, "error": "空数据"}
    order = np.argsort(t)
    t, e = t[order], e[order]
    # 逐唯一时间点计算 KM
    surv = 1.0
    table = []
    i = 0
    while i < n:
        ti = t[i]
        j = i
        while j < n and t[j] == ti:
            j += 1
        d = int(e[i:j].sum())          # 该时间点事件数
        at_risk = n - i                # 该时间点前仍在风险的人数
        if at_risk > 0 and d > 0:
            surv *= (1.0 - d / at_risk)
        table.append({"time": float(ti), "n_at_risk": int(at_risk),
                      "n_events": int(d), "survival": float(surv)})
        i = j
    # 中位生存：survival 首次 <= 0.5 的时间点
    median_time = None
    for row in table:
        if row["survival"] <= 0.5:
            median_time = row["time"]
            break
    return {"ok": True, "n": int(n), "n_events": int(e.sum()),
            "n_censored": int(n - e.sum()),
            "events": table, "median_survival_time": median_time,
            "final_survival": float(surv)}
