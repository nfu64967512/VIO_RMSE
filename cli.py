"""命令列介面(headless):批次算 ATE、匯出 CSV / 報告。

  python main.py --cli info <log.bin>
  python main.py --cli ate <log.bin> --ref GPS --est VISP --align se3
  python main.py --cli ate <log.bin> --ref GPS --est VISP --csv out.csv --report out.txt
  python main.py --cli ate --sim --align sim3
"""
from __future__ import annotations

import argparse
import sys

from core import align as align_mod
from core import logio, pipeline, sim, sync
from core.types import KIND_LABEL


def _load(args):
    if args.sim:
        return sim.make_sim_logdata()
    if not args.log:
        raise SystemExit("請指定 .bin 檔,或用 --sim 跑模擬資料")
    return logio.load_bin(args.log)


def _print_sources(logdata):
    print(f"資料來源:{logdata.path or '(模擬)'}")
    print(f"時長 {logdata.duration:.1f} s"
          + (f",ENU 原點 {logdata.origin_desc}" if logdata.origin_desc else ""))
    print(f"{'名稱':<18}{'座標框':<12}{'樣本':>8}{'速率':>9}")
    for name, s in logdata.sources.items():
        print(f"{name:<18}{KIND_LABEL[s.kind]:<12}{s.n:>8}{s.rate_hz:>7.1f} Hz")
    for n in logdata.notes:
        print(f"  註:{n}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vio_ate_studio --cli", description="VIO/GPS ATE 分析")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("info", help="列出 log 中的位置來源")
    pi.add_argument("log", nargs="?")
    pi.add_argument("--sim", action="store_true", help="改用模擬資料")

    pa = sub.add_parser("ate", help="計算 ATE")
    pa.add_argument("log", nargs="?")
    pa.add_argument("--sim", action="store_true", help="改用模擬資料")
    pa.add_argument("--ref", default="GPS", help="參考來源名稱(預設 GPS)")
    pa.add_argument("--est", default="VISP", help="估測來源名稱(預設 VISP)")
    pa.add_argument("--align", default="se3",
                    choices=[k for k, _ in align_mod.ALIGN_METHODS], help="空間對齊方式")
    pa.add_argument("--offset", type=float, default=0.0, help="時間偏移(秒)")
    pa.add_argument("--auto-offset", action="store_true", help="自動估測時間偏移")
    pa.add_argument("--t0", type=float, help="分析起點(秒,相對 log 起點)")
    pa.add_argument("--t1", type=float, help="分析終點(秒,相對 log 起點)")
    pa.add_argument("--max-gap", type=float, default=0.5, help="內插間隙上限(秒)")
    pa.add_argument("--csv", help="匯出逐點誤差 CSV")
    pa.add_argument("--report", help="匯出文字報告")

    args = p.parse_args(argv)
    logdata = _load(args)

    if args.cmd == "info":
        _print_sources(logdata)
        return 0

    for which, name in (("--ref", args.ref), ("--est", args.est)):
        if name not in logdata.sources:
            print(f"找不到來源 {name}({which});可用:{', '.join(logdata.sources)}", file=sys.stderr)
            return 2

    offset = args.offset
    if args.auto_offset:
        rt, rp, et, ep = pipeline.get_enu_pair(logdata, args.ref, args.est)
        offset, corr = sync.find_time_offset(rt, rp, et, ep, search_s=5.0)
        print(f"自動時間偏移:{offset * 1000:+.0f} ms(相關係數 {corr:.3f})")

    tb = logdata.t_base
    res = pipeline.compute(
        logdata, args.ref, args.est,
        t0=tb + args.t0 if args.t0 is not None else None,
        t1=tb + args.t1 if args.t1 is not None else None,
        offset_s=offset, method=args.align, max_gap_s=args.max_gap)

    print()
    print(pipeline.format_report(res, logdata))

    if args.csv:
        pipeline.save_csv(res, logdata, args.csv)
        print(f"\n已寫出 {args.csv}")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(pipeline.format_report(res, logdata))
        print(f"已寫出 {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
