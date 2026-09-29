"""matplotlib 圖表元件(全部畫在深色表面上)。

每張圖的序列角色固定:參考=藍、估測=橘、誤差=洋紅、誤差水平=紫。
軸向誤差用單序列 small multiples(標題命名身分),不另外配類別色。
"""
from __future__ import annotations

import matplotlib
import numpy as np

matplotlib.use("QtAgg")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget  # noqa: E402

from . import theme  # noqa: E402

theme.apply_mpl(matplotlib)

_HINT = "載入 log 或模擬資料後,按「計算 ATE」"
# U+2212 在多數中文字型缺字形,一律用 ASCII 連字號
MINUS = "-"
CMAP_ERR = LinearSegmentedColormap.from_list("ate_seq", theme.SEQ_RAMP)


class PlotWidget(QWidget):
    """帶工具列的 matplotlib 畫布;子類實作 draw_result()。"""

    def __init__(self, parent=None, toolbar: bool = True):
        super().__init__(parent)
        self.fig = Figure(figsize=(7, 5), dpi=100, facecolor=theme.SURFACE)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if toolbar:
            tb = NavigationToolbar2QT(self.canvas, self)
            tb.setStyleSheet(
                f"QToolBar{{background:{theme.PANEL};border:none;padding:2px;}}"
                f"QToolButton{{color:{theme.INK_2};padding:3px;}}"
                f"QToolButton:hover{{background:#3a3a37;border-radius:4px;}}"
                f"QLabel{{color:{theme.MUTED};}}")
            lay.addWidget(tb)
        lay.addWidget(self.canvas)
        self.show_placeholder()

    def show_placeholder(self, text: str = _HINT):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_facecolor(theme.SURFACE)
        ax.axis("off")
        ax.text(0.5, 0.5, text, ha="center", va="center",
                color=theme.MUTED, fontsize=11, transform=ax.transAxes)
        self.canvas.draw_idle()

    def render(self, res, logdata):
        try:
            self.fig.clear()
            self.draw_result(res, logdata)
            self.canvas.draw_idle()
        except Exception as e:  # noqa: BLE001
            self.show_placeholder(f"繪圖失敗:{type(e).__name__}: {e}")

    def draw_result(self, res, logdata):
        raise NotImplementedError

    def _equal_xy(self, ax, x, y, pad_frac: float = 0.08):
        """等比例座標:避免形狀被拉伸而誤判軌跡偏差。

        用 adjustable="box"(縮 axes 外框)而非 "datalim"(改資料範圍),
        後者會與明確設定的 xlim/ylim 衝突並被 matplotlib 默默忽略其一。
        """
        ax.set_aspect("equal", adjustable="box")
        cx, cy = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
        half = max(x.max() - x.min(), y.max() - y.min(), 1.0) / 2 * (1 + pad_frac)
        ax.set_xlim(cx - half, cx + half)
        ax.set_ylim(cy - half, cy + half)


def _style_axes(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme.BASELINE)
        ax.spines[side].set_linewidth(1.0)
    ax.grid(True, color=theme.GRID, linewidth=0.8)


def _endpoints(ax, x, y, color, label_prefix=""):
    """起點空心圓、終點實心方塊 — 形狀差異讓方向不靠顏色。"""
    ax.plot(x[0], y[0], "o", ms=9, mfc="none", mec=color, mew=2.0, zorder=5,
            label=f"{label_prefix}起點" if label_prefix else None)
    ax.plot(x[-1], y[-1], "s", ms=8, color=color, zorder=5,
            mec=theme.SURFACE, mew=1.5,
            label=f"{label_prefix}終點" if label_prefix else None)


class TopViewPlot(PlotWidget):
    """平面軌跡(東-北)+ 誤差連線。"""

    def draw_result(self, res, logdata):
        ax = self.fig.add_subplot(111)
        ax.set_facecolor(theme.SURFACE)
        r, e = res.ref_enu, res.est_enu

        # 誤差連線:每隔幾點畫一條,直接讓「差多少」看得見。
        # 軌跡尺度通常遠大於誤差,這裡只給尺度感 — 要看誤差落在哪請用「誤差熱點」分頁。
        stride = max(1, res.n // 140)
        k = np.arange(0, res.n, stride)
        ax.add_collection(LineCollection(
            np.stack([np.column_stack([r[k, 0], r[k, 1]]),
                      np.column_stack([e[k, 0], e[k, 1]])], axis=1),
            colors=theme.C_ERR, linewidths=1.1, alpha=0.65, zorder=1))

        ax.plot(r[:, 0], r[:, 1], color=theme.C_REF, lw=2.0,
                label=f"參考:{res.ref_name}", zorder=3)
        ax.plot(e[:, 0], e[:, 1], color=theme.C_EST, lw=2.0,
                label=f"估測:{res.est_name}", zorder=4)
        _endpoints(ax, r[:, 0], r[:, 1], theme.C_REF)
        _endpoints(ax, e[:, 0], e[:, 1], theme.C_EST)

        # 最大誤差點:註解出來(狀態色 + 文字標籤,不靠顏色單獨表意)
        k = int(np.argmax(res.e3))
        ax.plot([r[k, 0], e[k, 0]], [r[k, 1], e[k, 1]],
                color=theme.S_WARN, lw=2.0, zorder=6)
        ax.annotate(f"最大誤差 {res.e3[k]:.2f} m",
                    xy=(e[k, 0], e[k, 1]), xytext=(12, 12), textcoords="offset points",
                    color=theme.S_WARN, fontsize=9, fontweight="bold", zorder=7,
                    arrowprops=dict(arrowstyle="-", color=theme.S_WARN, lw=1.0))

        self._equal_xy(ax, np.concatenate([r[:, 0], e[:, 0]]),
                       np.concatenate([r[:, 1], e[:, 1]]))
        ax.set_xlabel("東 E(公尺)")
        ax.set_ylabel("北 N(公尺)")
        ax.set_title(f"平面軌跡對照 — 3D RMSE {res.stats['3d']['rmse']:.3f} m"
                     f"(軌跡尺度 {res.path_len_ref:.0f} m,誤差相對很小是正常的)")
        _style_axes(ax)
        ax.legend(loc="upper left", bbox_to_anchor=(0.01, 0.99),
                  labelspacing=0.35, handlelength=1.6)
        self.fig.tight_layout()


class HeatmapPlot(PlotWidget):
    """誤差熱點:參考軌跡依該時刻的 3D 誤差著色。

    軌跡尺度(數十~數百公尺)通常遠大於誤差(公尺級),兩條線疊圖看不出差別;
    把誤差映成顏色畫在軌跡上,才看得出「在場地的哪個位置」誤差變大。
    """

    def draw_result(self, res, logdata):
        ax = self.fig.add_subplot(111)
        ax.set_facecolor(theme.SURFACE)
        p = res.ref_enu
        segs = np.stack([p[:-1, :2], p[1:, :2]], axis=1)
        mag = 0.5 * (res.e3[:-1] + res.e3[1:])          # 線段兩端誤差的平均

        norm = Normalize(vmin=float(res.e3.min()), vmax=float(res.e3.max()))
        lc = LineCollection(segs, cmap=CMAP_ERR, norm=norm, linewidths=3.2,
                            capstyle="round", zorder=3)
        lc.set_array(mag)
        ax.add_collection(lc)

        cb = self.fig.colorbar(lc, ax=ax, pad=0.02, fraction=0.045)
        cb.set_label("3D 誤差(公尺)", color=theme.INK_2, fontsize=9)
        cb.ax.tick_params(colors=theme.MUTED, labelsize=8.5)
        cb.outline.set_edgecolor(theme.BASELINE)
        for v, lbl in ((res.stats["3d"]["p95"], "P95"), (res.stats["3d"]["rmse"], "RMSE")):
            cb.ax.axhline(v, color=theme.SURFACE, lw=2.4)
            cb.ax.axhline(v, color=theme.S_WARN if lbl == "P95" else theme.INK_2, lw=1.2)

        k = int(np.argmax(res.e3))
        ax.plot(p[k, 0], p[k, 1], "o", ms=13, mfc="none",
                mec=theme.S_WARN, mew=2.2, zorder=5)
        ax.annotate(f"最大 {res.e3[k]:.2f} m", xy=(p[k, 0], p[k, 1]),
                    xytext=(14, 12), textcoords="offset points",
                    color=theme.S_WARN, fontsize=9.5, fontweight="bold", zorder=6,
                    arrowprops=dict(arrowstyle="-", color=theme.S_WARN, lw=1.0))
        _endpoints(ax, p[:, 0], p[:, 1], theme.MUTED)
        ax.annotate("○ 起點  ■ 終點", xy=(0.01, 0.985), xycoords="axes fraction",
                    va="top", color=theme.MUTED, fontsize=8.5)

        self._equal_xy(ax, p[:, 0], p[:, 1])
        ax.set_xlabel("東 E(公尺)")
        ax.set_ylabel("北 N(公尺)")
        ax.set_title(f"誤差熱點 — 顏色越亮誤差越大(畫在 {res.ref_name} 軌跡上)")
        _style_axes(ax)
        self.fig.tight_layout()


class Plot3D(PlotWidget):
    """3D 軌跡。"""

    def draw_result(self, res, logdata):
        ax = self.fig.add_subplot(111, projection="3d")
        ax.set_facecolor(theme.SURFACE)
        r, e = res.ref_enu, res.est_enu
        ax.plot(r[:, 0], r[:, 1], r[:, 2], color=theme.C_REF, lw=1.8,
                label=f"參考:{res.ref_name}")
        ax.plot(e[:, 0], e[:, 1], e[:, 2], color=theme.C_EST, lw=1.8,
                label=f"估測:{res.est_name}")
        ax.scatter(r[0, 0], r[0, 1], r[0, 2], s=55, facecolors="none",
                   edgecolors=theme.C_REF, linewidths=2)
        ax.scatter(e[0, 0], e[0, 1], e[0, 2], s=55, facecolors="none",
                   edgecolors=theme.C_EST, linewidths=2)

        for pane in (ax.xaxis, ax.yaxis, ax.zaxis):
            pane.set_pane_color((0.102, 0.102, 0.098, 1.0))
            pane._axinfo["grid"]["color"] = theme.GRID
            pane._axinfo["grid"]["linewidth"] = 0.7
        ax.set_xlabel("東 E(m)")
        ax.set_ylabel("北 N(m)")
        ax.set_zlabel("天 U(m)")
        ax.set_title("3D 軌跡對照")
        # 等比例:XY 同尺度,Z 依實際範圍(高度變化通常遠小於水平)
        allp = np.vstack([r, e])
        rng = allp.max(axis=0) - allp.min(axis=0)
        xy = max(rng[0], rng[1], 1.0)
        ax.set_box_aspect((1, 1, max(0.25, min(1.0, rng[2] / xy * 3))))
        ax.legend(loc="upper left")
        self.fig.subplots_adjust(left=0.02, right=0.98, top=0.94, bottom=0.04)


class ErrorTimePlot(PlotWidget):
    """誤差-時間序列(3D / 水平)+ P95 參考線。"""

    def draw_result(self, res, logdata):
        ax = self.fig.add_subplot(111)
        ax.set_facecolor(theme.SURFACE)
        t = res.t - logdata.t_base
        ax.fill_between(t, 0, res.e3, color=theme.C_ERR, alpha=0.14, lw=0)
        ax.plot(t, res.e3, color=theme.C_ERR, lw=1.8, label="3D 誤差 ‖Δ‖")
        ax.plot(t, res.e2, color=theme.C_ERR2, lw=1.5, label="水平誤差 ‖Δ_EN‖")

        # 兩條參考線的標籤貼在同一側、用軸座標定位,才不會被軸邊界切掉
        for val, lbl, col, ls in ((res.stats["3d"]["p95"], "3D P95", theme.S_WARN, "--"),
                                  (res.stats["3d"]["rmse"], "3D RMSE", theme.MUTED, ":")):
            ax.axhline(val, color=col, lw=1.3, ls=ls, zorder=2)
            ax.annotate(f"{lbl} = {val:.2f} m", xy=(0.995, val),
                        xycoords=("axes fraction", "data"),
                        xytext=(0, 5), textcoords="offset points",
                        ha="right", color=col, fontsize=9, fontweight="bold", zorder=6,
                        bbox=dict(boxstyle="round,pad=0.25", fc=theme.SURFACE, ec="none", alpha=0.85))

        ax.set_xlim(t[0], t[-1])
        ax.set_ylim(0, max(res.e3.max() * 1.12, 1e-3))
        ax.set_xlabel("時間(秒,相對 log 起點)")
        ax.set_ylabel("誤差(公尺)")
        ax.set_title(f"誤差隨時間變化 — {res.est_name} 相對 {res.ref_name}")
        _style_axes(ax)
        ax.legend(loc="upper left", ncols=2)
        self.fig.tight_layout()


def _moving_average(t: np.ndarray, v: np.ndarray, window_s: float) -> np.ndarray:
    """等時距移動平均(以樣本數近似,取樣率固定時等價)。"""
    dt = float(np.median(np.diff(t))) if t.size > 1 else 1.0
    n = max(3, int(round(window_s / max(dt, 1e-6)))) | 1
    if n >= v.size:
        return np.full_like(v, float(v.mean()))
    pad = n // 2
    vp = np.pad(v, (pad, pad), mode="edge")
    return np.convolve(vp, np.ones(n) / n, mode="valid")[:v.size]


class AxisErrorPlot(PlotWidget):
    """軸向誤差 small multiples(東/北/天),共用時間軸與 y 範圍。

    疊一條移動平均趨勢線:對齊後各軸平均誤差恆為 0(質心已對齊),
    畫平均值沒有資訊量;低頻趨勢才看得出 VIO 往哪個方向慢慢漂。
    """

    def draw_result(self, res, logdata):
        t = res.t - logdata.t_base
        win = max(5.0, res.duration / 20.0)
        lim = float(np.abs(res.err).max()) * 1.12 or 1.0
        axes = self.fig.subplots(3, 1, sharex=True, sharey=True)

        for ax, (zh, k) in zip(axes, [("東 E", 0), ("北 N", 1), ("天 U", 2)]):
            ax.set_facecolor(theme.SURFACE)
            v = res.err[:, k]
            ax.fill_between(t, 0, v, color=theme.C_ERR, alpha=0.16, lw=0)
            ax.plot(t, v, color=theme.C_ERR, lw=1.1, alpha=0.75)
            ax.plot(t, _moving_average(t, v, win), color=theme.S_WARN, lw=2.0)
            ax.axhline(0, color=theme.BASELINE, lw=1.2)
            st = res.stats["axes"]["ENU"[k]]
            ax.set_title(f"{zh} 軸 — RMSE {st['rmse']:.3f} m,P95 {st['p95']:.3f} m,"
                         f"最大 {st['max']:.3f} m", loc="left", fontsize=9.5)
            ax.set_ylim(-lim, lim)
            _style_axes(ax)

        axes[0].annotate(f"粗線 = {win:.0f} 秒移動平均(低頻漂移趨勢)",
                         xy=(0.995, 0.88), xycoords="axes fraction",
                         ha="right", color=theme.S_WARN, fontsize=8.5)
        axes[1].set_ylabel("誤差(公尺)")
        axes[-1].set_xlabel("時間(秒,相對 log 起點)")
        axes[-1].set_xlim(t[0], t[-1])
        self.fig.suptitle(f"各軸誤差(估測 {MINUS} 參考)— 看得出是哪個方向在偏",
                          color=theme.INK, fontsize=11, fontweight="bold")
        self.fig.subplots_adjust(left=0.07, right=0.985, top=0.90, bottom=0.09, hspace=0.42)


class DistributionPlot(PlotWidget):
    """誤差分佈:直方圖 + 累積分佈(CDF)。"""

    def draw_result(self, res, logdata):
        ax1, ax2 = self.fig.subplots(1, 2)
        for ax in (ax1, ax2):
            ax.set_facecolor(theme.SURFACE)

        e = res.e3
        st = res.stats["3d"]
        ax1.hist(e, bins=min(60, max(12, res.n // 25)), color=theme.C_ERR,
                 alpha=0.85, edgecolor=theme.SURFACE, linewidth=0.8)
        # 三條標記線的標籤錯開高度,避免中位數與 RMSE 靠近時字疊在一起
        # 三條標記線靠「線型 + 文字標籤」區分身分,不靠顏色
        marks = ((st["median"], "中位數", theme.MUTED, ":", 0.97),
                 (st["rmse"], "RMSE", theme.MUTED, "-", 0.88),
                 (st["p95"], "P95", theme.S_WARN, "--", 0.79))
        for val, lbl, col, ls, y in marks:
            ax1.axvline(val, color=col, lw=1.2, ls=ls)
            ax1.annotate(f"{lbl} {val:.2f} m", xy=(val, y), xycoords=("data", "axes fraction"),
                         xytext=(5, 0), textcoords="offset points",
                         va="center", ha="left", color=col, fontsize=8.5,
                         bbox=dict(boxstyle="round,pad=0.22", fc=theme.SURFACE, ec="none", alpha=0.85))
        ax1.set_xlabel("3D 誤差(公尺)")
        ax1.set_ylabel("樣本數")
        ax1.set_title("誤差分佈", loc="left")
        _style_axes(ax1)

        s = np.sort(e)
        cdf = np.arange(1, s.size + 1) / s.size * 100
        ax2.plot(s, cdf, color=theme.C_ERR, lw=2.0)
        ax2.fill_between(s, 0, cdf, color=theme.C_ERR, alpha=0.12, lw=0)
        for pct, col in ((50, theme.MUTED), (90, theme.INK_2), (95, theme.S_WARN)):
            val = float(np.percentile(e, pct))
            ax2.plot([s[0], val], [pct, pct], color=col, lw=1.1, ls="--")
            ax2.plot([val, val], [0, pct], color=col, lw=1.1, ls="--")
            ax2.annotate(f"P{pct} = {val:.2f} m", xy=(val, pct), xytext=(6, -11),
                         textcoords="offset points", color=col, fontsize=8.5)
        ax2.set_xlim(s[0], s[-1])
        ax2.set_ylim(0, 100)
        ax2.set_xlabel("3D 誤差(公尺)")
        ax2.set_ylabel("累積百分比(%)")
        ax2.set_title("累積分佈 — 有幾成樣本落在誤差之內", loc="left")
        _style_axes(ax2)
        self.fig.tight_layout()


class OverviewPlot(PlotWidget):
    """載入後、計算前的總覽:每個來源一張小平面圖(small multiples)+ 時間涵蓋。

    用小倍數而非疊圖:真實 log 常有 5~6 個位置來源,再怎麼配色都無法讓那麼多條
    線彼此可辨(尤其對色覺缺陷讀者);每張小圖單一序列、標題命名身分,
    數量再多也不會失效,而且各來源的形狀看得更清楚。
    """

    def draw_logdata(self, logdata, origin):
        try:
            self.fig.clear()
            self._draw(logdata, origin)
            self.canvas.draw_idle()
        except Exception as ex:  # noqa: BLE001
            self.show_placeholder(f"繪圖失敗:{type(ex).__name__}: {ex}")

    def _draw(self, logdata, origin):
        names = sorted(logdata.sources)
        drawable = []
        for name in names:
            try:
                drawable.append((name, logdata.sources[name], logdata.sources[name].enu(origin)))
            except ValueError:
                pass
        if not drawable:
            raise ValueError("沒有可繪製的來源(缺少 ENU 原點)")

        ncols = min(3, len(drawable))
        nrows = int(np.ceil(len(drawable) / ncols))
        gs = self.fig.add_gridspec(nrows + 1, ncols,
                                   height_ratios=[3] * nrows + [1.5],
                                   hspace=0.55, wspace=0.3)

        for i, (name, src, p) in enumerate(drawable):
            ax = self.fig.add_subplot(gs[i // ncols, i % ncols])
            ax.set_facecolor(theme.SURFACE)
            ax.plot(p[:, 0], p[:, 1], color=theme.C_REF, lw=1.3)
            _endpoints(ax, p[:, 0], p[:, 1], theme.C_REF)
            self._equal_xy(ax, p[:, 0], p[:, 1])
            ax.set_title(f"{name} — {src.n} 筆 · {src.rate_hz:.0f} Hz",
                         loc="left", fontsize=9.5)
            ax.tick_params(labelsize=7.5)
            _style_axes(ax)
            if i // ncols == nrows - 1:
                ax.set_xlabel("東 E(m)", fontsize=8.5)
            if i % ncols == 0:
                ax.set_ylabel("北 N(m)", fontsize=8.5)

        axt = self.fig.add_subplot(gs[nrows, :])
        axt.set_facecolor(theme.SURFACE)
        for i, (name, src, _) in enumerate(drawable):
            axt.barh(i, src.t[-1] - src.t[0], left=src.t[0] - logdata.t_base,
                     height=0.55, color=theme.C_REF)
        axt.set_yticks(range(len(drawable)))
        axt.set_yticklabels([n for n, _, _ in drawable], fontsize=8.5)
        axt.invert_yaxis()
        axt.set_xlabel("時間涵蓋(秒,相對 log 起點)")
        axt.set_xlim(0, max(logdata.duration, 1.0))
        axt.set_title("各來源的時間涵蓋 — 錯開或缺漏的區段不能拿來比較",
                      loc="left", fontsize=9.5)
        _style_axes(axt)
        axt.grid(axis="y", visible=False)

        self.fig.suptitle(f"資料總覽 — {len(drawable)} 個位置來源",
                          color=theme.INK, fontsize=11, fontweight="bold")
        self.fig.subplots_adjust(left=0.06, right=0.98, top=0.91, bottom=0.08)
