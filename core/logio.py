"""ArduPilot DataFlash .bin 解析(pymavlink DFReader)。

策略:掃描候選訊息型別,凡欄位含 Lat/Lng(經緯度)或 PX/PY/PZ、PN/PE/PD
(局部 NED)者皆收為可選軌跡來源;GPS 依 instance 拆分(GPS[1] 常見為
VIO 注入的第二顆 GPS);ORGN 取為 ENU 原點。
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .types import KIND_GLOBAL, KIND_NED, LogData, TrajSource

# 想掃的訊息(涵蓋常見位置來源;VISO 記的是增量位移,不是位置,故不含)
#   GPS/GPS2/GPSB  衛星定位(VIO 若以 GPS_INPUT 注入會出現在 instance 1)
#   POS            EKF 融合後的位置
#   VISP           VISION_POSITION_ESTIMATE(ROS2 餵進來的 vision position)
#   XKF1/NKF1      EKF3/EKF2 的內部位置估計
#   AHR2           次要 AHRS 解
CANDIDATE_TYPES = ["GPS", "GPS2", "GPSB", "POS", "VISP", "XKF1", "NKF1", "AHR2", "ORGN"]

_MIN_SAMPLES = 10


def _instance_of(m) -> int | None:
    for f in ("I", "C", "Instance"):
        if hasattr(m, f):
            try:
                return int(getattr(m, f))
            except (TypeError, ValueError):
                return None
    return None


def _fix_latlng(v: np.ndarray) -> np.ndarray:
    """保險:某些工具鏈輸出未除 1e7 的原始整數經緯度。"""
    if v.size and np.nanmax(np.abs(v)) > 400.0:
        return v * 1e-7
    return v


def load_bin(path: str, progress_cb=None) -> LogData:
    """解析 .bin,回傳 LogData。progress_cb(percent:int) 供 UI 進度條。"""
    from pymavlink import mavutil  # 延遲載入,加快程式啟動

    mlog = mavutil.mavlink_connection(path)
    try:
        return _scan(mlog, path, progress_cb)
    finally:
        try:
            mlog.close()   # Windows 上不關會鎖住檔案
        except Exception:  # noqa: BLE001
            pass


def _scan(mlog, path: str, progress_cb) -> LogData:
    buckets: dict[tuple[str, int | None], dict[str, list]] = defaultdict(lambda: defaultdict(list))
    origin_ekf = None      # ORGN Type==0(EKF 原點,優先)
    origin_any = None
    count = 0

    while True:
        m = mlog.recv_match(type=CANDIDATE_TYPES)
        if m is None:
            break
        name = m.get_type()
        count += 1
        if progress_cb is not None and count % 5000 == 0:
            pct = getattr(mlog, "percent", None)
            if pct is not None:
                progress_cb(int(pct))

        if name == "ORGN":
            try:
                o = (float(m.Lat), float(m.Lng), float(m.Alt))
                o = (o[0] * 1e-7 if abs(o[0]) > 400 else o[0],
                     o[1] * 1e-7 if abs(o[1]) > 400 else o[1], o[2])
                if int(getattr(m, "Type", 0)) == 0:
                    origin_ekf = o
                origin_any = o
            except (AttributeError, TypeError, ValueError):
                pass
            continue

        t_us = getattr(m, "TimeUS", None)
        if t_us is None:
            continue
        key = (name, _instance_of(m))
        b = buckets[key]
        b["t"].append(float(t_us) * 1e-6)

        if hasattr(m, "Lat") and (hasattr(m, "Lng") or hasattr(m, "Lon")):
            b["lat"].append(float(m.Lat))
            b["lng"].append(float(getattr(m, "Lng", getattr(m, "Lon", 0.0))))
            b["alt"].append(float(getattr(m, "Alt", 0.0)))
            if hasattr(m, "Status"):
                b["status"].append(int(m.Status))
        elif hasattr(m, "PX"):
            b["px"].append(float(m.PX)); b["py"].append(float(m.PY)); b["pz"].append(float(m.PZ))
        elif hasattr(m, "PN"):
            b["px"].append(float(m.PN)); b["py"].append(float(m.PE)); b["pz"].append(float(m.PD))
        else:
            b["t"].pop()  # 不是位置訊息,收回

    if progress_cb is not None:
        progress_cb(100)

    # 每個訊息名稱有哪些 instance(決定顯示名稱)
    inst_map: dict[str, list] = defaultdict(list)
    for (name, inst), _ in buckets.items():
        inst_map[name].append(inst)

    sources: dict[str, TrajSource] = {}
    notes: list[str] = []
    for (name, inst), b in sorted(buckets.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        t = np.asarray(b["t"], dtype=float)
        if t.size == 0:
            continue

        label = name
        if len(inst_map[name]) > 1 or (inst not in (None, 0)):
            label = f"{name}[{inst}]"
            if name == "GPS" and inst == 1:
                label = "GPS[1] (GPS2)"

        if "lat" in b:
            kind = KIND_GLOBAL
            lat = _fix_latlng(np.asarray(b["lat"], dtype=float))
            lng = _fix_latlng(np.asarray(b["lng"], dtype=float))
            alt = np.asarray(b["alt"], dtype=float)
            cols = [lat, lng, alt]
            if "status" in b and len(b["status"]) == t.size:
                ok = np.asarray(b["status"]) >= 3
                dropped = int((~ok).sum())
                if dropped and ok.sum() >= _MIN_SAMPLES:
                    t, cols = t[ok], [c[ok] for c in cols]
                    notes.append(f"{label}:濾除 {dropped} 筆非 3D-fix 樣本")
            lat, lng, alt = cols
            ned = None
        else:
            kind = KIND_NED
            ned = np.column_stack([b["px"], b["py"], b["pz"]]).astype(float)
            lat = lng = alt = None

        # 有效值 → 時間排序 → 去除重複時間戳(內插要求嚴格遞增)
        if kind == KIND_GLOBAL:
            good = np.isfinite(t) & np.isfinite(lat) & np.isfinite(lng) & np.isfinite(alt)
        else:
            good = np.isfinite(t) & np.all(np.isfinite(ned), axis=1)
        sel = np.flatnonzero(good)
        sel = sel[np.argsort(t[sel], kind="stable")]
        if sel.size:
            ts = t[sel]
            sel = sel[np.concatenate([[True], np.diff(ts) > 1e-9])]
        t = t[sel]
        if kind == KIND_GLOBAL:
            lat, lng, alt = lat[sel], lng[sel], alt[sel]
        else:
            ned = ned[sel]

        if t.size < _MIN_SAMPLES:
            notes.append(f"{label}:樣本不足({t.size} 筆),略過")
            continue

        sources[label] = TrajSource(label, kind, t, lat=lat, lng=lng, alt=alt, ned=ned)

    origin = origin_ekf or origin_any
    origin_desc = "ORGN(EKF 原點)" if origin_ekf else ("ORGN" if origin_any else "")
    if not sources:
        raise ValueError(
            "log 中找不到任何可用的位置訊息(找過 "
            + "、".join(CANDIDATE_TYPES) + ")。\n\n"
            "這份 log 實際包含的訊息型別:\n" + _available_types(mlog) + "\n\n"
            "若你的 VIO 位置記在別的訊息名稱下,請把該名稱告知或加進 "
            "core/logio.py 的 CANDIDATE_TYPES。")
    return LogData(sources=sources, origin=origin, origin_desc=origin_desc,
                   path=path, notes=notes)


def _available_types(mlog, per_line: int = 12) -> str:
    """列出 log 中實際定義過的訊息名稱 — 找不到位置訊息時給使用者的線索。"""
    try:
        names = sorted({f.name for f in mlog.formats.values()})
    except AttributeError:
        return "(無法列出)"
    if not names:
        return "(無)"
    return "\n".join("  " + "  ".join(names[i:i + per_line])
                     for i in range(0, len(names), per_line))
