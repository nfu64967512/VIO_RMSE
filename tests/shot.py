r"""離線截圖:把每張圖用模擬資料算一次並存成 PNG,不需要開 GUI。

  python tests\shot.py [輸出資料夾]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import pipeline, sim  # noqa: E402
from ui import plots  # noqa: E402

CHARTS = [("top", plots.TopViewPlot), ("heat", plots.HeatmapPlot),
          ("3d", plots.Plot3D), ("err", plots.ErrorTimePlot),
          ("axis", plots.AxisErrorPlot), ("dist", plots.DistributionPlot)]


def main(outdir: str) -> int:
    os.makedirs(outdir, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    logdata = sim.make_sim_logdata()
    res = pipeline.compute(logdata, "GPS", "VISP", method="se3")

    ov = plots.OverviewPlot()
    ov.resize(1240, 820)
    ov.draw_logdata(logdata, logdata.origin)
    p = os.path.join(outdir, "00_overview.png")
    ov.fig.set_size_inches(12.4, 8.2)
    ov.fig.savefig(p, dpi=110)
    print("寫出", p)

    for i, (name, cls) in enumerate(CHARTS, start=1):
        w = cls()
        w.resize(1240, 820)
        w.fig.set_size_inches(12.4, 8.2)
        w.render(res, logdata)
        p = os.path.join(outdir, f"{i:02d}_{name}.png")
        w.fig.savefig(p, dpi=110)
        print("寫出", p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "output/shots"))
