"""主視窗:左側控制面板 + 右側圖表分頁。"""
from __future__ import annotations

import os
import traceback

from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                             QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                             QMainWindow, QMessageBox, QProgressBar, QPushButton,
                             QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from core import align as align_mod
from core import logio, pipeline, sim, sync
from core.types import KIND_LABEL

from . import plots, theme
from .widgets import Card, LegendChip, StatsTable, StatTile


class _LoadWorker(QObject):
    """在背景執行緒解析 .bin(大 log 可能要數十秒)。"""
    progress = pyqtSignal(int)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        try:
            self.done.emit(logio.load_bin(self.path, progress_cb=self.progress.emit))
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VIO / GPS ATE Studio — 絕對軌跡誤差分析")
        self.resize(1500, 940)
        self.logdata = None
        self.result = None
        self._thread = None
        self._worker = None

        self._build_menu()
        self._build_ui()
        self.statusBar().showMessage("就緒 — 請載入 .bin 記錄檔,或先用模擬資料試跑")

    # ---------------- 介面組裝 ----------------

    def _build_menu(self):
        m = self.menuBar().addMenu("檔案(&F)")
        for text, slot, key in (
            ("開啟 .bin 記錄檔…", self.on_open, QKeySequence.StandardKey.Open),
            ("載入模擬資料", self.on_load_sim, "Ctrl+D"),
            (None, None, None),
            ("匯出 CSV…", self.on_export_csv, "Ctrl+E"),
            ("匯出報告…", self.on_export_report, "Ctrl+R"),
            ("匯出目前圖表 PNG…", self.on_export_png, "Ctrl+P"),
            (None, None, None),
            ("結束", self.close, "Ctrl+Q"),
        ):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, self)
            a.triggered.connect(slot)
            if key:
                a.setShortcut(key)
            m.addAction(a)

        h = self.menuBar().addMenu("說明(&H)")
        a = QAction("ATE 是什麼 / 怎麼用", self)
        a.triggered.connect(self.on_help)
        h.addAction(a)

    def _build_ui(self):
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._build_left())
        split.addWidget(self._build_right())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([390, 1110])
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(10, 10, 10, 6)
        lay.addWidget(split)
        self.setCentralWidget(wrap)

    def _build_left(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 6, 0)
        v.setSpacing(9)

        # --- 資料 ---
        c = Card("1. 資料來源")
        row = QHBoxLayout()
        b1 = QPushButton("開啟 .bin…")
        b1.clicked.connect(self.on_open)
        b2 = QPushButton("模擬資料")
        b2.setToolTip("產生一段含已知 VIO 誤差(偏航 1.5°、尺度 1.004、漂移)的方形任務,用來試跑流程")
        b2.clicked.connect(self.on_load_sim)
        row.addWidget(b1); row.addWidget(b2)
        c.add_layout(row)
        self.lbl_file = QLabel("尚未載入")
        self.lbl_file.setObjectName("Hint")
        self.lbl_file.setWordWrap(True)
        c.add(self.lbl_file)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        c.add(self.progress)
        v.addWidget(c)

        # --- 軌跡選擇 ---
        c = Card("2. 選擇比較的兩條軌跡")
        g = QGridLayout()
        g.setSpacing(6)
        g.addWidget(QLabel("參考(基準)"), 0, 0)
        self.cmb_ref = QComboBox()
        self.cmb_ref.setToolTip("當成「真值」的軌跡,通常是 GPS")
        g.addWidget(self.cmb_ref, 0, 1)
        g.addWidget(QLabel("估測(受測)"), 1, 0)
        self.cmb_est = QComboBox()
        self.cmb_est.setToolTip("要評估的軌跡,例如 ROS2 餵進去的 VISP(vision position)")
        g.addWidget(self.cmb_est, 1, 1)
        g.setColumnStretch(1, 1)
        c.add_layout(g)
        self.chip_ref = LegendChip(theme.C_REF, "參考 —")
        self.chip_est = LegendChip(theme.C_EST, "估測 —")
        c.add(self.chip_ref)
        c.add(self.chip_est)
        self.lbl_origin = QLabel("")
        self.lbl_origin.setObjectName("Hint")
        self.lbl_origin.setWordWrap(True)
        c.add(self.lbl_origin)
        v.addWidget(c)

        # --- 對齊與同步 ---
        c = Card("3. 對齊與同步")
        g = QGridLayout()
        g.setSpacing(6)
        g.addWidget(QLabel("空間對齊"), 0, 0)
        self.cmb_align = QComboBox()
        for k, zh in align_mod.ALIGN_METHODS:
            self.cmb_align.addItem(zh, k)
        self.cmb_align.setCurrentIndex(0)
        self.cmb_align.setToolTip(
            "VIO 起始位置/朝向與 GPS 不同框時要對齊。\n"
            "SE(3):平移+旋轉;Sim(3):再加尺度(可看出 VIO 尺度誤差);\n"
            "4DOF:只繞垂直軸轉(適合已知重力對齊的情形)")
        g.addWidget(self.cmb_align, 0, 1, 1, 2)

        g.addWidget(QLabel("時間偏移"), 1, 0)
        self.spn_offset = QDoubleSpinBox()
        self.spn_offset.setRange(-30.0, 30.0)
        self.spn_offset.setDecimals(3)
        self.spn_offset.setSingleStep(0.01)
        self.spn_offset.setSuffix(" s")
        self.spn_offset.setToolTip("加在估測軌跡時間戳上的偏移")
        g.addWidget(self.spn_offset, 1, 1)
        self.btn_auto_off = QPushButton("自動估")
        self.btn_auto_off.setToolTip("用兩條軌跡的速度剖面互相關,自動找最佳時間偏移")
        self.btn_auto_off.clicked.connect(self.on_auto_offset)
        g.addWidget(self.btn_auto_off, 1, 2)

        g.addWidget(QLabel("分析起點"), 2, 0)
        self.spn_t0 = QDoubleSpinBox()
        self.spn_t0.setRange(0, 1e6); self.spn_t0.setDecimals(1); self.spn_t0.setSuffix(" s")
        g.addWidget(self.spn_t0, 2, 1, 1, 2)
        g.addWidget(QLabel("分析終點"), 3, 0)
        self.spn_t1 = QDoubleSpinBox()
        self.spn_t1.setRange(0, 1e6); self.spn_t1.setDecimals(1); self.spn_t1.setSuffix(" s")
        g.addWidget(self.spn_t1, 3, 1, 1, 2)

        g.addWidget(QLabel("內插間隙上限"), 4, 0)
        self.spn_gap = QDoubleSpinBox()
        self.spn_gap.setRange(0.02, 10.0); self.spn_gap.setDecimals(2)
        self.spn_gap.setValue(0.5); self.spn_gap.setSuffix(" s")
        self.spn_gap.setToolTip("估測軌跡若有資料空洞,超過此長度的區段不參與比較")
        g.addWidget(self.spn_gap, 4, 1, 1, 2)
        g.setColumnStretch(1, 1)
        c.add_layout(g)

        self.chk_auto = QCheckBox("改動設定時自動重算")
        self.chk_auto.setChecked(True)
        c.add(self.chk_auto)
        v.addWidget(c)

        self.btn_run = QPushButton("計算 ATE")
        self.btn_run.setObjectName("Primary")
        self.btn_run.setMinimumHeight(38)
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self.compute)
        v.addWidget(self.btn_run)

        # --- 結果磚 ---
        c = Card("結果")
        g = QGridLayout()
        g.setSpacing(8)
        self.tile_3d = StatTile("ATE RMSE(3D)")
        self.tile_2d = StatTile("ATE RMSE(水平)")
        self.tile_p95 = StatTile("3D P95")
        self.tile_max = StatTile("3D 最大")
        g.addWidget(self.tile_3d, 0, 0); g.addWidget(self.tile_2d, 0, 1)
        g.addWidget(self.tile_p95, 1, 0); g.addWidget(self.tile_max, 1, 1)
        c.add_layout(g)
        self.lbl_align_info = QLabel("")
        self.lbl_align_info.setObjectName("Hint")
        self.lbl_align_info.setWordWrap(True)
        c.add(self.lbl_align_info)
        v.addWidget(c)

        row = QHBoxLayout()
        for text, slot in (("匯出 CSV", self.on_export_csv),
                           ("匯出報告", self.on_export_report),
                           ("存圖 PNG", self.on_export_png)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)
        v.addStretch(1)

        for w in (self.cmb_ref, self.cmb_est, self.cmb_align):
            w.currentIndexChanged.connect(self._on_setting_changed)
        for w in (self.spn_offset, self.spn_t0, self.spn_t1, self.spn_gap):
            w.valueChanged.connect(self._on_setting_changed)

        from PyQt6.QtWidgets import QScrollArea
        sc = QScrollArea()
        sc.setWidget(panel)
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        sc.setMinimumWidth(360)
        return sc

    def _build_right(self) -> QWidget:
        self.tabs = QTabWidget()
        self.p_overview = plots.OverviewPlot()
        self.p_top = plots.TopViewPlot()
        self.p_heat = plots.HeatmapPlot()
        self.p_3d = plots.Plot3D()
        self.p_err = plots.ErrorTimePlot()
        self.p_axis = plots.AxisErrorPlot()
        self.p_dist = plots.DistributionPlot()
        self.tabs.addTab(self.p_overview, "資料總覽")
        self.tabs.addTab(self.p_top, "平面軌跡")
        self.tabs.addTab(self.p_heat, "誤差熱點")
        self.tabs.addTab(self.p_3d, "3D 軌跡")
        self.tabs.addTab(self.p_err, "誤差時序")
        self.tabs.addTab(self.p_axis, "軸向誤差")
        self.tabs.addTab(self.p_dist, "誤差分佈")

        tbl = QWidget()
        tv = QVBoxLayout(tbl)
        tv.setContentsMargins(12, 12, 12, 12)
        lab = QLabel("ATE 統計(公尺)— 圖表的表格版")
        lab.setObjectName("SectionTitle")
        tv.addWidget(lab)
        self.table = StatsTable()
        tv.addWidget(self.table)
        hint = QLabel("「平均 / 中位數」在軸向欄位保留正負號(代表系統性偏差);"
                      "「P90 / P95 / 最大」取絕對值。")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        tv.addWidget(hint)
        tv.addStretch(1)
        self.tabs.addTab(tbl, "統計表")

        self.txt_report = QTextEdit()
        self.txt_report.setReadOnly(True)
        self.txt_report.setPlainText("計算後在此顯示完整文字報告。")
        self.tabs.addTab(self.txt_report, "報告")
        return self.tabs

    # ---------------- 資料載入 ----------------

    def on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇 ArduPilot 記錄檔", "", "DataFlash 記錄檔 (*.bin *.BIN);;所有檔案 (*.*)")
        if path:
            self.load_bin(path)

    def load_bin(self, path: str):
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.btn_run.setEnabled(False)
        self.lbl_file.setText(f"解析中:{os.path.basename(path)}")
        self.statusBar().showMessage(f"解析 {os.path.basename(path)} …")

        self._thread = QThread(self)
        self._worker = _LoadWorker(path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.done.connect(self._on_loaded)
        self._worker.failed.connect(self._on_load_failed)
        for sig in (self._worker.done, self._worker.failed):
            sig.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def _on_loaded(self, logdata):
        self.progress.setVisible(False)
        self.set_logdata(logdata)

    def _on_load_failed(self, msg: str):
        self.progress.setVisible(False)
        self.lbl_file.setText("載入失敗")
        self.statusBar().showMessage("載入失敗")
        QMessageBox.critical(self, "無法解析記錄檔", msg[:2000])

    def on_load_sim(self):
        self.set_logdata(sim.make_sim_logdata())

    def set_logdata(self, logdata):
        self.logdata = logdata
        self.result = None
        names = list(logdata.sources)

        for cmb in (self.cmb_ref, self.cmb_est):
            cmb.blockSignals(True)
            cmb.clear()
            for n in names:
                src = logdata.sources[n]
                cmb.addItem(f"{n} — {KIND_LABEL[src.kind]}, {src.n} 筆 @ {src.rate_hz:.0f} Hz", n)
        # 預設:GPS 當參考、VISP(vision position)當估測
        ref_i = next((i for i, n in enumerate(names) if n.upper().startswith("GPS")), 0)
        est_i = next((i for i, n in enumerate(names) if "VIS" in n.upper()),
                     1 if len(names) > 1 else 0)
        if est_i == ref_i and len(names) > 1:
            est_i = (ref_i + 1) % len(names)
        self.cmb_ref.setCurrentIndex(ref_i)
        self.cmb_est.setCurrentIndex(est_i)
        for cmb in (self.cmb_ref, self.cmb_est):
            cmb.blockSignals(False)

        dur = logdata.duration
        for spn, val in ((self.spn_t0, 0.0), (self.spn_t1, dur)):
            spn.blockSignals(True)
            spn.setRange(0.0, max(dur, 1.0))
            spn.setValue(val)
            spn.blockSignals(False)
        self.spn_offset.blockSignals(True)
        self.spn_offset.setValue(0.0)
        self.spn_offset.blockSignals(False)

        name = os.path.basename(logdata.path) if logdata.path else "模擬資料"
        self.lbl_file.setText(
            f"<b style='color:{theme.INK}'>{name}</b><br>"
            f"{len(names)} 個位置來源,時長 {dur:.0f} 秒"
            + (f"<br>原點:{logdata.origin_desc}" if logdata.origin_desc else ""))
        self.lbl_origin.setText("")
        self.btn_run.setEnabled(True)

        origin = logdata.origin
        if origin is None:
            for s in logdata.sources.values():
                if s.kind == "global":
                    origin = (float(s.lat[0]), float(s.lng[0]), float(s.alt[0]))
                    break
        self.p_overview.draw_logdata(logdata, origin)
        for p in (self.p_top, self.p_heat, self.p_3d, self.p_err, self.p_axis, self.p_dist):
            p.show_placeholder()
        self.table.clear_values()
        for t in (self.tile_3d, self.tile_2d, self.tile_p95, self.tile_max):
            t.set(None)
        self.lbl_align_info.setText("")
        self.tabs.setCurrentIndex(0)

        msgs = " | ".join(logdata.notes[:3])
        self.statusBar().showMessage(
            f"已載入 {name}:{', '.join(names)}" + (f"  ⚠ {msgs}" if msgs else ""))
        self._update_chips()
        self.compute()

    # ---------------- 計算 ----------------

    def _names(self):
        return self.cmb_ref.currentData(), self.cmb_est.currentData()

    def _update_chips(self):
        ref, est = self._names()
        self.chip_ref.set_text(f"參考 — {ref or '—'}")
        self.chip_est.set_text(f"估測 — {est or '—'}")

    def _on_setting_changed(self):
        self._update_chips()
        if self.chk_auto.isChecked() and self.logdata is not None:
            self.compute(quiet=True)

    def compute(self, quiet: bool = False):
        if self.logdata is None:
            return
        ref, est = self._names()
        if not ref or not est:
            return
        if ref == est:
            self.statusBar().showMessage("⚠ 參考與估測是同一個來源,請換一個")
            return

        tb = self.logdata.t_base
        t0, t1 = self.spn_t0.value(), self.spn_t1.value()
        if t1 <= t0:
            self.statusBar().showMessage("⚠ 分析終點必須大於起點")
            return

        try:
            res = pipeline.compute(
                self.logdata, ref, est,
                t0=tb + t0, t1=tb + t1,
                offset_s=self.spn_offset.value(),
                method=self.cmb_align.currentData(),
                max_gap_s=self.spn_gap.value())
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(f"⚠ {e}")
            if not quiet:
                QMessageBox.warning(self, "無法計算 ATE", str(e))
            return

        self.result = res
        self._show_result(res)

    def _show_result(self, res):
        s = res.stats
        self.tile_3d.set(s["3d"]["rmse"], f"{res.n} 對樣本 · {res.duration:.0f} s", theme.INK)
        self.tile_2d.set(s["2d"]["rmse"], "東北平面")
        self.tile_p95.set(s["3d"]["p95"], "95% 樣本在此之內")
        self.tile_max.set(s["3d"]["max"], "單點最差")

        a = res.align_info
        scale_txt = (f",尺度 {a['scale']:.4f}(≈{(a['scale'] - 1) * 100:+.2f}%)"
                     if a["method"] == "sim3" else "")
        self.lbl_align_info.setText(
            f"對齊:{a['label']}<br>"
            f"偏航修正 {a['yaw_deg']:+.2f}°,平移 "
            f"[{a['translation'][0]:+.2f}, {a['translation'][1]:+.2f}, "
            f"{a['translation'][2]:+.2f}] m{scale_txt}<br>"
            f"路徑長:參考 {res.path_len_ref:.0f} m / 估測 {res.path_len_est:.0f} m")
        self.lbl_origin.setText(f"ENU 原點:{res.origin_desc}" if res.origin_desc else "")

        self.table.set_stats(s)
        self.txt_report.setPlainText(pipeline.format_report(res, self.logdata))
        for p in (self.p_top, self.p_heat, self.p_3d, self.p_err, self.p_axis, self.p_dist):
            p.render(res, self.logdata)
        if self.tabs.currentIndex() == 0:
            self.tabs.setCurrentIndex(1)
        self.statusBar().showMessage(
            f"{res.est_name} 相對 {res.ref_name}:3D RMSE {s['3d']['rmse']:.3f} m,"
            f"水平 {s['2d']['rmse']:.3f} m,P95 {s['3d']['p95']:.3f} m({res.n} 對樣本)")

    def on_auto_offset(self):
        if self.logdata is None:
            return
        ref, est = self._names()
        if ref == est:
            QMessageBox.warning(self, "無法估測", "參考與估測是同一個來源")
            return
        try:
            rt, rp, et, ep = pipeline.get_enu_pair(self.logdata, ref, est)
            off, corr = sync.find_time_offset(rt, rp, et, ep, search_s=5.0)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "無法自動估測時間偏移", str(e))
            return
        self.spn_offset.setValue(off)
        self.statusBar().showMessage(
            f"自動時間偏移:{off * 1000:+.0f} ms(速度剖面相關係數 {corr:.3f})"
            + ("" if corr > 0.5 else " — 相關性偏低,結果僅供參考"))

    # ---------------- 匯出 ----------------

    def _need_result(self) -> bool:
        if self.result is None:
            QMessageBox.information(self, "尚無結果", "請先計算 ATE。")
            return False
        return True

    def _default_name(self, ext: str) -> str:
        base = os.path.splitext(os.path.basename(self.logdata.path or "sim"))[0] or "sim"
        r = self.result
        return f"{base}_ATE_{r.est_name}_vs_{r.ref_name}".replace("[", "").replace("]", "") + ext

    def on_export_csv(self):
        if not self._need_result():
            return
        path, _ = QFileDialog.getSaveFileName(self, "匯出逐點誤差 CSV",
                                              self._default_name(".csv"), "CSV (*.csv)")
        if not path:
            return
        try:
            pipeline.save_csv(self.result, self.logdata, path)
        except OSError as e:
            QMessageBox.critical(self, "寫入失敗", str(e))
            return
        self.statusBar().showMessage(f"已匯出 {path}")

    def on_export_report(self):
        if not self._need_result():
            return
        path, _ = QFileDialog.getSaveFileName(self, "匯出文字報告",
                                              self._default_name(".txt"), "文字檔 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(pipeline.format_report(self.result, self.logdata))
        except OSError as e:
            QMessageBox.critical(self, "寫入失敗", str(e))
            return
        self.statusBar().showMessage(f"已匯出 {path}")

    def on_export_png(self):
        w = self.tabs.currentWidget()
        if not isinstance(w, plots.PlotWidget):
            QMessageBox.information(self, "無法存圖", "目前分頁不是圖表,請切到任一張圖再存。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "儲存圖表",
            self._default_name(f"_{self.tabs.tabText(self.tabs.currentIndex())}.png")
            if self.result else "chart.png", "PNG 影像 (*.png)")
        if not path:
            return
        try:
            w.fig.savefig(path, dpi=160, facecolor=theme.SURFACE)
        except OSError as e:
            QMessageBox.critical(self, "寫入失敗", str(e))
            return
        self.statusBar().showMessage(f"已儲存 {path}")

    def on_help(self):
        QMessageBox.information(self, "ATE 是什麼 / 怎麼用", HELP_TEXT)

    def closeEvent(self, ev):
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        ev.accept()


HELP_TEXT = """ATE(Absolute Trajectory Error,絕對軌跡誤差)

把兩條軌跡做時間同步、(可選)空間對齊後,逐點相減得到誤差向量,再取統計量。
3D 誤差 = 誤差向量長度;水平誤差 = 只取東、北兩軸。

使用流程
1. 開啟 ArduPilot 的 .bin 記錄檔(或按「模擬資料」先試跑)。
2. 參考選 GPS,估測選 VISP(從 ROS2 餵進去的 vision position)。
3. 空間對齊:
   • SE(3):平移 + 旋轉。VIO 與 GPS 起始框不同時最常用。
   • Sim(3):再加尺度。想知道 VIO 有沒有尺度誤差就選這個,看「尺度」欄位。
   • 4DOF:只繞垂直軸旋轉,適合重力方向已對齊的情形。
   • 不對齊:兩者本來就在同一座標框(例如都已經是 EKF 原點座標)時用。
4. 若兩邊時間戳有系統性延遲,按「自動估」用速度剖面互相關找出來。
5. 起降前後的地面段會稀釋統計,用「分析起點/終點」裁掉。

各分頁
• 平面軌跡:兩條軌跡疊圖,細線是每一時刻的誤差連線,最大誤差有標註。
• 誤差時序:誤差怎麼隨時間長大(VIO 漂移會看到單調上升)。
• 軸向誤差:分東/北/天三軸,虛線是平均偏差 — 用來判斷是哪個方向在系統性偏。
• 誤差分佈:直方圖與累積分佈,P95 代表 95% 的樣本落在多少誤差之內。

注意
GPS 本身有 0.3~2 m 誤差,所以拿 GPS 當「真值」時,算出來的 ATE 是
「VIO 與 GPS 的差異」,不是 VIO 的絕對精度。要真值請用 RTK 或動作捕捉。
"""


def run():
    import sys
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.QSS)
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        w.load_bin(sys.argv[1])
    sys.exit(app.exec())
