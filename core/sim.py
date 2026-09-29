"""模擬資料:仿照戶外方形任務(如 Mission Planner 截圖場景)產生
GPS(5 Hz)/ VISP(30 Hz,含 VIO 漂移・偏航偏差・尺度誤差)/ POS(10 Hz)。

用途:在還沒有真實 .bin 之前驗證整條 ATE 流程與 UI;也供自我測試比對
已知注入誤差(偏航 / 尺度)是否能被對齊演算法還原。
"""
from __future__ import annotations

import numpy as np

from . import frames
from .types import KIND_GLOBAL, KIND_NED, LogData, TrajSource

# 模擬場景參數(虎尾附近經緯度,對應截圖場地尺度)
ORIGIN = (23.70200, 120.43100, 38.0)
YAW_MIS_DEG = 1.5        # 注入的 VIO 偏航偏差
SCALE_ERR = 1.004        # 注入的 VIO 尺度誤差
T_BOOT = 100.0           # 模擬開機時基(秒)
_DT = 1.0 / 60.0         # 真值取樣週期


def _smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x
    pad = (win // 2, win - 1 - win // 2)
    xp = np.pad(x, pad, mode="edge")
    return np.convolve(xp, np.ones(win) / win, mode="valid")


def _gauss_markov(n: int, dt: float, tau: float, sigma: float, rng) -> np.ndarray:
    a = float(np.exp(-dt / tau))
    q = sigma * np.sqrt(1.0 - a * a)
    x = np.empty(n)
    x[0] = rng.normal(0.0, sigma)
    w = rng.normal(0.0, q, n)
    for k in range(1, n):
        x[k] = a * x[k - 1] + w[k]
    return x


def _truth(laps: int, side: float, speed: float, alt: float):
    """組出:地面 → 爬升 → 懸停 → 方形繞圈(含高度正弦調變)→ 懸停 → 降落。"""
    dt = _DT
    corners = [(0.0, 0.0)]
    for _ in range(laps):
        corners += [(side, 0.0), (side, side), (0.0, side), (0.0, 0.0)]
    pts = np.asarray(corners)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    t_cruise = total / speed

    def en_at(s):
        return np.interp(s, cum, pts[:, 0]), np.interp(s, cum, pts[:, 1])

    phases = []  # (duration, fn(tau) -> (E, N, U))
    phases.append((6.0, lambda tau: (np.zeros_like(tau), np.zeros_like(tau), np.zeros_like(tau))))
    def climb(tau):
        s = np.clip(tau / 8.0, 0, 1)
        return np.zeros_like(tau), np.zeros_like(tau), alt * (3 * s ** 2 - 2 * s ** 3)
    phases.append((8.0, climb))
    phases.append((3.0, lambda tau: (np.zeros_like(tau), np.zeros_like(tau), np.full_like(tau, alt))))
    def cruise(tau):
        e, n = en_at(np.minimum(tau * speed, total))
        return e, n, alt + 1.2 * np.sin(2 * np.pi * tau / 45.0)
    phases.append((t_cruise, cruise))
    phases.append((3.0, lambda tau: (np.zeros_like(tau), np.zeros_like(tau), np.full_like(tau, alt))))
    def descend(tau):
        s = np.clip(tau / 8.0, 0, 1)
        return np.zeros_like(tau), np.zeros_like(tau), alt * (1 - (3 * s ** 2 - 2 * s ** 3))
    phases.append((8.0, descend))
    phases.append((4.0, lambda tau: (np.zeros_like(tau), np.zeros_like(tau), np.zeros_like(tau))))

    es, ns, us = [], [], []
    for dur, fn in phases:
        tau = np.arange(0.0, dur, dt)
        e, n, u = fn(tau)
        es.append(e); ns.append(n); us.append(u)
    e = np.concatenate(es); n = np.concatenate(ns); u = np.concatenate(us)
    w_en = int(round(1.0 / dt)) | 1
    w_u = int(round(0.5 / dt)) | 1
    e = _smooth(e, w_en); n = _smooth(n, w_en); u = _smooth(u, w_u)
    t = np.arange(e.size) * dt + T_BOOT
    return t, np.column_stack([e, n, u])


def make_sim_with_truth(seed: int = 7, laps: int = 4, side: float = 30.0,
                        speed: float = 3.0, alt: float = 10.0):
    """回傳 (LogData, truth_info)。truth_info 供自我測試核對注入誤差。"""
    rng = np.random.default_rng(seed)
    t, enu = _truth(laps, side, speed, alt)
    n_all = t.size
    dt = _DT

    # --- GPS 5 Hz:Gauss-Markov 慢漂 + 白雜訊 ---
    idx = np.arange(0, n_all, 12)
    tg = t[idx]
    ng = idx.size
    noise = np.column_stack([
        _gauss_markov(ng, dt * 12, 20.0, 0.35, rng) + rng.normal(0, 0.03, ng),
        _gauss_markov(ng, dt * 12, 20.0, 0.35, rng) + rng.normal(0, 0.03, ng),
        _gauss_markov(ng, dt * 12, 25.0, 0.70, rng) + rng.normal(0, 0.05, ng),
    ])
    lat, lng, galt = frames.enu_to_geodetic(enu[idx] + noise, ORIGIN)
    gps = TrajSource("GPS", KIND_GLOBAL, tg, lat=lat, lng=lng, alt=galt,
                     meta="模擬 GPS 5 Hz(水平 σ≈0.35 m、垂直 σ≈0.7 m 慢漂)")

    # --- VISP 30 Hz:偏航偏差 + 尺度 + 隨機漫步漂移 + 白雜訊(NED,相對 EKF 原點) ---
    idx = np.arange(0, n_all, 2)
    tv = t[idx]
    nv = idx.size
    ned = frames.enu_to_ned(enu[idx])
    mis = np.radians(YAW_MIS_DEG)
    c, s = np.cos(mis), np.sin(mis)
    n_rot = c * ned[:, 0] - s * ned[:, 1]
    e_rot = s * ned[:, 0] + c * ned[:, 1]
    ned_v = np.column_stack([n_rot, e_rot, ned[:, 2]]) * SCALE_ERR
    step = 0.9 / np.sqrt(nv)
    drift = np.cumsum(rng.normal(0, step, (nv, 3)) * np.array([1.0, 1.0, 0.5]), axis=0)
    ned_v += drift + rng.normal(0, 0.02, (nv, 3))
    visp = TrajSource("VISP", KIND_NED, tv, ned=ned_v,
                      meta=f"模擬 VIO 30 Hz(偏航 {YAW_MIS_DEG}°、尺度 {SCALE_ERR}、漂移≈0.9 m RW)")

    # --- POS 10 Hz:EKF 融合輸出(小誤差) ---
    idx = np.arange(0, n_all, 6)
    tp = t[idx]
    npn = idx.size
    noise = np.column_stack([
        _gauss_markov(npn, dt * 6, 30.0, 0.10, rng) + rng.normal(0, 0.02, npn),
        _gauss_markov(npn, dt * 6, 30.0, 0.10, rng) + rng.normal(0, 0.02, npn),
        _gauss_markov(npn, dt * 6, 30.0, 0.15, rng) + rng.normal(0, 0.03, npn),
    ])
    lat, lng, palt = frames.enu_to_geodetic(enu[idx] + noise, ORIGIN)
    pos = TrajSource("POS", KIND_GLOBAL, tp, lat=lat, lng=lng, alt=palt,
                     meta="模擬 EKF 位置 10 Hz(σ≈0.1 m)")

    logdata = LogData(
        sources={"GPS": gps, "VISP": visp, "POS": pos},
        origin=ORIGIN, origin_desc="ORGN(模擬)",
        path="(模擬資料)",
        notes=[
            f"模擬資料:{side:.0f} m 方形任務 × {laps} 圈,巡航 {speed} m/s,高度 {alt} m ± 1.2 m",
            f"VIO 注入誤差:偏航 {YAW_MIS_DEG}°、尺度 {SCALE_ERR}、隨機漫步漂移 ≈ 0.9 m(seed={seed})",
        ],
    )
    truth_info = {"t": t, "enu": enu, "yaw_mis_deg": YAW_MIS_DEG, "scale": SCALE_ERR, "origin": ORIGIN}
    return logdata, truth_info


def make_sim_logdata(seed: int = 7) -> LogData:
    return make_sim_with_truth(seed)[0]
