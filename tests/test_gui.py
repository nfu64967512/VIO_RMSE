"""GUI 煙霧測試:不需人工點擊,涵蓋主視窗建立、背景執行緒載入 .bin、
切換來源/對齊、每張圖繪製、匯出。

  python tests\test_gui.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import align as align_mod  # noqa: E402
from core import fakebin, sim  # noqa: E402
from ui import plots, theme  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def _pump(ms: int = 120):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _wait_until(pred, timeout_ms: int = 30000) -> bool:
    waited = 0
    while waited < timeout_ms:
        if pred():
            return True
        _pump(100)
        waited += 100
    return False


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(theme.QSS)
    w = MainWindow()
    w.resize(1500, 940)
    w.show()
    _pump(300)
    failed = 0

    def check(label, cond, extra=""):
        nonlocal failed
        if cond:
            print(f"  [OK]   {label} {extra}")
        else:
            failed += 1
            print(f"  [FAIL] {label} {extra}")

    print("[視窗建立]")
    check("主視窗顯示", w.isVisible())
    check("計算鈕在無資料時停用", not w.btn_run.isEnabled())
    check("分頁數 = 9", w.tabs.count() == 9, f"(實得 {w.tabs.count()})")

    with tempfile.TemporaryDirectory() as d:
        binpath = os.path.join(d, "gui_sim.bin")
        fakebin.write_logdata_to_bin(sim.make_sim_logdata(), binpath)

        print("\n[背景執行緒載入 .bin]")
        w.load_bin(binpath)
        ok = _wait_until(lambda: w.logdata is not None and w.result is not None)
        check("載入 + 自動計算完成", ok)
        if not ok:
            print("  !! 逾時,終止")
            return 1
        check("解析出 3 個來源", len(w.logdata.sources) == 3, f"({sorted(w.logdata.sources)})")
        check("進度條已隱藏", not w.progress.isVisible())
        check("計算鈕已啟用", w.btn_run.isEnabled())
        check("預設參考 = GPS", w.cmb_ref.currentData() == "GPS",
              f"({w.cmb_ref.currentData()})")
        check("預設估測 = VISP", w.cmb_est.currentData() == "VISP",
              f"({w.cmb_est.currentData()})")
        rmse = w.result.stats["3d"]["rmse"]
        check("RMSE 落在合理範圍", 0.5 < rmse < 2.0, f"(3D RMSE {rmse:.3f} m)")
        check("統計磚已填值", w.tile_3d.value.text() != "—", f"({w.tile_3d.value.text()})")
        check("統計表已填值", w.table.item(0, 1).text() != "—",
              f"({w.table.item(0, 1).text()})")
        check("報告已產生", "ATE RMSE" in w.txt_report.toPlainText())

        print("\n[每張圖都畫得出來]")
        for i in range(w.tabs.count()):
            page = w.tabs.widget(i)
            if not isinstance(page, plots.PlotWidget):
                continue
            w.tabs.setCurrentIndex(i)
            _pump(150)
            name = w.tabs.tabText(i)
            # 佔位訊息的 axes 只有文字、沒有資料 artist(Axes3D 的 axison 語意
            # 與 2D 不同,不能拿來判斷)
            n_art = sum(len(a.lines) + len(a.collections) + len(a.patches)
                        for a in page.fig.axes)
            check(f"分頁「{name}」", n_art > 0,
                  f"({len(page.fig.axes)} 個 axes, {n_art} 個 artist)")

        print("\n[切換對齊方式,結果要跟著變]")
        base = w.result.stats["3d"]["rmse"]
        seen = {}
        for idx in range(w.cmb_align.count()):
            w.cmb_align.setCurrentIndex(idx)
            _pump(250)
            key = w.cmb_align.itemData(idx)
            seen[key] = w.result.stats["3d"]["rmse"]
        check("Sim(3) ≤ SE(3) ≤ 不對齊",
              seen["sim3"] <= seen["se3"] + 1e-9 <= seen["none"] + 1e-9,
              f"(sim3 {seen['sim3']:.3f} / se3 {seen['se3']:.3f} / none {seen['none']:.3f})")
        check("涵蓋全部 5 種對齊", len(seen) == len(align_mod.ALIGN_METHODS))
        w.cmb_align.setCurrentIndex(0)
        _pump(250)
        check("切回 SE(3) 數值一致", abs(w.result.stats["3d"]["rmse"] - base) < 1e-9)

        print("\n[時間裁切]")
        w.spn_t0.setValue(30.0)
        w.spn_t1.setValue(90.0)
        _pump(400)
        check("裁切後時長 ≈ 60 s", 59 < w.result.duration < 61,
              f"({w.result.duration:.1f} s, {w.result.n} 對樣本)")
        w.spn_t0.setValue(0.0)
        w.spn_t1.setValue(w.logdata.duration)
        _pump(400)

        print("\n[自動估時間偏移]")
        w.on_auto_offset()
        _pump(600)
        check("偏移估值接近 0", abs(w.spn_offset.value()) < 0.3,
              f"({w.spn_offset.value() * 1000:+.0f} ms)")
        w.spn_offset.setValue(0.0)
        _pump(300)

        print("\n[換成 POS 當估測]")
        i_pos = next(i for i in range(w.cmb_est.count()) if w.cmb_est.itemData(i) == "POS")
        w.cmb_est.setCurrentIndex(i_pos)
        _pump(500)
        check("POS 比 VISP 更貼近 GPS",
              w.result.stats["3d"]["rmse"] < base,
              f"(POS {w.result.stats['3d']['rmse']:.3f} < VISP {base:.3f})")
        check("圖例文字已更新", "POS" in w.chip_est.lab.text(), f"({w.chip_est.lab.text()})")

        print("\n[參考 = 估測 時不當機]")
        i_gps = next(i for i in range(w.cmb_est.count()) if w.cmb_est.itemData(i) == "GPS")
        w.cmb_est.setCurrentIndex(i_gps)
        _pump(400)
        check("視窗仍存活且有前次結果", w.isVisible() and w.result is not None)
        w.cmb_est.setCurrentIndex(i_pos)
        _pump(400)

        print("\n[匯出]")
        csv_p = os.path.join(d, "out.csv")
        rep_p = os.path.join(d, "out.txt")
        png_p = os.path.join(d, "out.png")
        from core import pipeline
        pipeline.save_csv(w.result, w.logdata, csv_p)
        with open(rep_p, "w", encoding="utf-8") as f:
            f.write(pipeline.format_report(w.result, w.logdata))
        w.tabs.setCurrentIndex(1)
        _pump(150)
        w.tabs.currentWidget().fig.savefig(png_p, dpi=120, facecolor=theme.SURFACE)
        for p, lbl, mn in ((csv_p, "CSV", 10000), (rep_p, "報告", 500), (png_p, "PNG", 20000)):
            sz = os.path.getsize(p)
            check(f"{lbl} 寫出", sz > mn, f"({sz // 1024} KB)")

        print("\n[關閉視窗]")
        w.close()
        _pump(300)
        check("關閉後執行緒已停", w._thread is None or not w._thread.isRunning())

    print(f"\n{'=' * 60}\n{'全部通過' if not failed else f'{failed} 項失敗'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
