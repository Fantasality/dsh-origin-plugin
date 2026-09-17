# 化学科研绘图样式规范知识库（Nature 与各子领域惯例）

> 面向 AI 的绘图决策知识库：画图前先查本文件对应子领域的"惯例"，输出符合
> 期刊规范的图。来源：Nature 官方 research-figure-guide.nature.com、
> 各子领域期刊与社区惯例（IUCr、Rigaku、 ACS/RSC 投稿指南、Materials 社区）。
> 最后核验：2026-09-17。

---

## 1. Nature 系期刊硬性规范（官方 figure guide）

| 项 | 规范 |
|---|---|
| 图宽 | **单栏 89 mm / 1.5 栏 120 mm / 双栏 183 mm**（按最终印刷尺寸建画布，不要画完再缩放） |
| 图高 | 最大 170mm（预留图注空间）；子刊版式最大 247mm |
| 字号 | **全部文字 5-7pt**（最终印刷尺寸下）；面板标签 **8pt 粗体**（Nature 系主刊大写 A/B/C，Nature Methods 小写 a/b/c） |
| 字体 | **Arial / Helvetica**（无衬线）；文字必须保持**可编辑**（不要 outline、不要栅格化） |
| 分辨率 | 照片 ≥300dpi；图文混合 ≥500dpi；**纯线图 ≥1000dpi**；TIFF（LZW）600-1200dpi 是交付标准 |
| 格式 | TIFF / EPS / PDF 交付；**避免 JPEG**（压缩伪影毁细线）；PNG 仅初投 |
| 颜色 | RGB(sRGB) 在线出版；**色盲安全**：避免红绿组合与彩虹色标；推荐 **Okabe-Ito** 调色板 |
| 图例 | 颜色标识优先用**图内 key/line**（图注里只写文字描述不写颜色名） |
| 面板 | 紧凑排列、字母顺序（A,B,C…）、面板间距 2-3mm、整图单文件提交 |

**Okabe-Ito 色盲安全调色板（hex）**：
黑 #000000 · 橙 #E69F00 · 天蓝 #56B4E9 · 蓝绿 #009E73 · 黄 #F0E442 ·
蓝 #0072B2 · 朱红 #D55E00 · 紫红 #CC79A7

**字号换算（本项目）**：89mm @600dpi = **2102px** 画布宽；此时
- 轴题/刻度标签：**8pt**
- 峰标注：**7pt**
- 拟合信息/辅助注记：**6pt**（下限 5pt）
对应 `origin_figure intent="nature"`（自动 2100px + journal 样式）。

---

## 2. 各化学子领域图样式惯例

### 2.1 NMR（核磁共振）
- X 轴：**δ (ppm)，反向**（高场在左低场在右，惯例源自早期 CW 仪器扫描方向）——`reverse_x=True`
- Y 轴：Intensity (a.u.)
- 必标：化学位移 δ 值、**J 耦合常数**（¹J(C,H)≈125 Hz 类）、溶剂峰
- 峰形：Lorentzian 为主；拟合用 multi-peak deconvolution
- 拼写惯例：¹H/¹³C 用上标数字；"ppm" 不用 "×10⁻⁶"

### 2.2 XRD（X 射线衍射）
- X 轴：**2θ (degrees)**（不写 θ；源于布拉格几何），典型 10°-90°
- Y 轴：Intensity (a.u.) 或 Counts；**不标绝对强度**（相对比较才有意义）
- 必标：**Miller 指数 (hkl)**（如 (111)/(200)/(220)）、FWHM 括弧（Scherrer 用）、辐射源（CuKα λ=1.5406Å）
- 峰形：pseudo-Voigt；背景是缓变包络不是峰（多项式扣除后再标峰）
- FWHM 典型 0.1°-1°：峰越窄晶粒越大（Scherrer 公式）

### 2.3 Raman（拉曼）
- X 轴：**Raman Shift (cm⁻¹)**
- Y 轴：Intensity (a.u.)
- 必标：D 带（~1350）、G 带（~1580）、2D 带（~2700）等特征带 + **激发波长**（532/633/785nm 必须注明）
- 多样品：等间距 offset 堆叠（offset 增量必须一致）

### 2.4 FTIR（红外）
- X 轴：**Wavenumber (cm⁻¹)，反向**（4000→400，高波数在左——历史惯例：早期色散仪波长线性扫描）
- Y 轴：Transmittance (%) **正向向下** 或 Absorbance（现代定量用 A）
- 必标：官能团特征峰（O-H 3200-3550 / C=O 1700 / C-H 2850-2960 / 指纹区 <1500）

### 2.5 UV-Vis（紫外可见）
- X 轴：Wavelength (nm)（400-800 常见）
- Y 轴：Absorbance (a.u.)（定量用 A；Tauc plot 求带隙时画 (αhν)² vs hν）

### 2.6 电化学（CV / GCD / EIS / Tafel）
- **CV**：X 轴 Potential (V) vs 参比电极（**必须注明参比**：vs Ag/AgCl、vs RHE）；Y 轴 Current (mA) 或**电流密度 (mA/cm²)**（按面积归一）；**必标扫速**（mV/s）与氧化/还原峰
- **GCD**：X 轴 Time (s)，Y 轴 Voltage (V)；标注充放电分支
- **EIS**：Nyquist 图 **Z' vs -Z''**（负虚轴朝上！），**等比例轴**（aspect 1:1）；高频区半圆+低频区斜线；标注 Rs（溶液电阻）
- **Tafel**：log|j| vs E，标斜率 (mV/dec) 与交换电流密度
- 电流密度归一化要注明基底面积/质量（mA/cm² 或 A/g）

### 2.7 材料显微（SEM/TEM）
- **必须有校准比例尺**（烧进图像，不用轴代替）
- 图注含加速电压、工作距离；对比组用一致放大倍数
- 多面板：(a) 明场 (b) SAED (c) HRTEM+FFT inset；EDS map 用一致色标 + 元素标签

### 2.8 热分析（TGA/DSC）
- TGA：质量% vs Temperature (°C)，标升温速率（°C/min）与气氛（N₂/Air）
- DSC：热流 vs Temperature，吸热/放热方向注明（endothermic up/down）

---

## 3. 通用科研图规范（跨领域）

1. **轴**：每个轴都有**量 + 单位**（如 "δ (ppm)"、"2θ (degrees)"、"Current density (mA cm⁻²)"）；无单位的写 **(a.u.)**
2. **物理量斜体、单位正体**：T、V、k 斜体；mm、V、s 正体（LaTeX/Origin 里注意设置）
3. **统计**：误差棒注明含义（±SD 还是 ±SEM，n=几）；显著性标注 * p<0.05 / ** p<0.01 / *** p<0.001
4. **避免**：网格线（Nature 风格无网格）、图表框双线、3D 效果、阴影、渐变
5. **色盲安全**：Okabe-Ito 或 viridis/cividis 连续色标；**红线+绿线**禁止同时用作区分
6. **线宽**：数据线 0.5-1.5pt；拟合/辅助线虚线或细线；刻度朝内
7. **直接标注优于图例**：曲线少（≤3）时在曲线旁直接标文字（本项目 `origin_annotate`）；曲线多时用图例
8. **模拟数据必须声明**：图注写 "simulated" / 图内注记 "Simulated"，与实验数据视觉区分（虚线/浅色）

---

## 4. 本项目的快捷入口

| 需求 | 工具 |
|---|---|
| Nature 单栏一张图 | `origin_figure intent="nature"`（2100px + journal 样式） |
| 标峰（NMR/XRD/PL） | `origin_figure label_peaks=true` 或 `origin_find_peaks` + `origin_annotate` |
| 批量注记（J 值/R²/样品名） | `origin_layout_info`（拿映射）→ `origin_annotate`（一次成型） |
| 模拟谱（教学/演示） | `origin_simulate` → `origin_figure`（columns 喂入） |
| 交付 | `origin_export_delivery fmt="png,pdf,tif" width=2100`（TIFF 600dpi 是 Nature 交付标准） |
| 色盲安全 | `origin_figure family="okabe-ito"`（调色板带使用约束，见 plot_style） |

---

## 5. 参考

- Nature Research Figures Guide: https://research-figure-guide.nature.com/figures/building-and-exporting-figure-panels/
- Nature Final submission (figures): https://www.nature.com/nature/for-authors/final-submission
- Okabe-Ito palette: Okabe & Ito, *Color Universal Design*
- XRD 惯例：IUCr powder diffraction commission；Rigaku 应用指南
- 光谱轴方向惯例：Bohrium Sciencepedia《光谱单位和坐标轴约定》
- 材料图规范：plotivy《Materials Scientist's Guide to Publication-Ready Figures》(2025)
