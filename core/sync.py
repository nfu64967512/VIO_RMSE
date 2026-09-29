"""時間同步:重疊區間裁切、線性內插配對、時間偏移自動搜尋。"""
from __future__ import annotations

import numpy as np


def sync_pair(ref_t, ref_p, est_t, est_p,
              t0: float | None = None, t1: float | None = None,
              offset_s: float = 0.0, max_gap_s: float = 0.5):
    """把估測軌跡內插到參考軌跡的時間戳上。

    - offset_s: 加在估測時間戳上的偏移(秒)。
    - max_gap_s: 參考時間戳離最近估測樣本超過此值時捨棄(避免跨資料空洞內插)。
    回傳 (t, ref_xyz, est_xyz),皆已裁切到重疊區間。
    """
    ref_t = np.asarray(ref_t, dtype=float)
    est_t = np.asarray(est_t, dtype=float) + float(offset_s)
    ref_p = np.asarray(ref_p, dtype=float)
    est_p = np.asarray(est_p, dtype=float)

    lo = max(ref_t[0], est_t[0])
    hi = min(ref_t[-1], est_t[-1])
    if t0 is not None:
        lo = max(lo, float(t0))
    if t1 is not None:
        hi = min(hi, float(t1))
    if hi <= lo:
        raise ValueError("兩軌跡(或所選時間區間)沒有重疊,請檢查時間範圍與偏移設定")

    m = (ref_t >= lo) & (ref_t <= hi)
    rt = ref_t[m]
    rp = ref_p[m]
    if rt.size == 0:
        raise ValueError("時間區間內沒有參考軌跡樣本")

    est_i = np.column_stack([np.interp(rt, est_t, est_p[:, k]) for k in range(est_p.shape[1])])

    # 空洞保護:到最近估測樣本的時間差
    idx = np.searchsorted(est_t, rt)
    left = np.clip(idx - 1, 0, est_t.size - 1)
    right = np.clip(idx, 0, est_t.size - 1)
    nearest = np.minimum(np.abs(rt - est_t[left]), np.abs(est_t[right] - rt))
    keep = nearest <= max_gap_s
    return rt[keep], rp[keep], est_i[keep]


def _speed_profile(t: np.ndarray, p: np.ndarray):
    """水平速度剖面(取樣於區段中點),供時間偏移相關性搜尋。"""
    t = np.asarray(t, dtype=float)
    p = np.asarray(p, dtype=float)
    dt = np.diff(t)
    ok = dt > 1e-6
    v = np.full(dt.shape, np.nan)
    d = np.linalg.norm(np.diff(p[:, :2], axis=0), axis=1)
    v[ok] = d[ok] / dt[ok]
    tm = 0.5 * (t[1:] + t[:-1])
    m = np.isfinite(v)
    return tm[m], v[m]


def find_time_offset(ref_t, ref_p, est_t, est_p,
                     search_s: float = 2.0, step_s: float = 0.02):
    """掃描 offset ∈ [-search, +search],找讓兩軌跡水平速度剖面相關係數最大的偏移。

    回傳 (offset_s, corr)。offset 意義同 sync_pair(加在估測時間戳上)。
    """
    rt, rv = _speed_profile(ref_t, ref_p)
    et, ev = _speed_profile(est_t, est_p)
    if rt.size < 20 or et.size < 20:
        raise ValueError("樣本太少,無法自動估測時間偏移")

    lo = max(rt[0], et[0] - search_s)
    hi = min(rt[-1], et[-1] + search_s)
    if hi - lo < 5.0:
        raise ValueError("重疊時間太短(< 5 s),無法自動估測時間偏移")

    grid = np.arange(lo, hi, 0.05)
    rg = np.interp(grid, rt, rv)

    best_off, best_corr = 0.0, -np.inf
    for off in np.arange(-search_s, search_s + 1e-9, step_s):
        eg = np.interp(grid, et + off, ev, left=np.nan, right=np.nan)
        m = np.isfinite(eg)
        if int(m.sum()) < 50:
            continue
        a = rg[m] - rg[m].mean()
        b = eg[m] - eg[m].mean()
        den = np.sqrt(float((a * a).sum()) * float((b * b).sum()))
        if den <= 1e-12:
            continue
        corr = float((a * b).sum()) / den
        if corr > best_corr:
            best_off, best_corr = float(off), corr
    if not np.isfinite(best_corr):
        raise ValueError("速度剖面無變化,無法自動估測時間偏移")
    return best_off, best_corr
