"""核心驗證(不需 PyQt6):python -m tests.test_core 或 pytest tests/"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import align, fakebin, frames, logio, metrics, pipeline, sim, sync  # noqa: E402


def test_frames_roundtrip():
    origin = (23.702, 120.431, 38.0)
    enu = np.array([[0.0, 0.0, 0.0], [100.0, -250.0, 12.5], [-3000.0, 4000.0, -80.0]])
    lat, lng, alt = frames.enu_to_geodetic(enu, origin)
    back = frames.geodetic_to_enu(lat, lng, alt, origin)
    err = np.max(np.abs(back - enu))
    assert err < 1e-6, f"ENU↔WGS84 往返誤差 {err:.3e} m 過大"
    print(f"  ENU↔WGS84 往返誤差 {err:.2e} m")


def test_frames_scale():
    """1e-5° 緯度 ≈ 1.11 m;經度需乘 cos(lat)。"""
    origin = (23.702, 120.431, 0.0)
    enu = frames.geodetic_to_enu([23.70201], [120.43101], [0.0], origin)[0]
    assert abs(enu[1] - 1.1057) < 0.01, f"北向尺度異常: {enu[1]:.4f}"
    expected_e = 1.1132 * np.cos(np.radians(23.702))
    assert abs(enu[0] - expected_e) < 0.01, f"東向尺度異常: {enu[0]:.4f}"
    print(f"  1e-5° → E {enu[0]:.4f} m / N {enu[1]:.4f} m")


def test_ned_enu():
    ned = np.array([[1.0, 2.0, 3.0]])
    enu = frames.ned_to_enu(ned)
    assert np.allclose(enu, [[2.0, 1.0, -3.0]])
    assert np.allclose(frames.enu_to_ned(enu), ned)
    print("  NED↔ENU 互換正確")


def test_align_recovers_transform():
    """對已知 yaw+平移+尺度變換的軌跡,對齊後應幾乎零誤差且參數可還原。"""
    rng = np.random.default_rng(1)
    ref = rng.normal(0, 20, (500, 3))
    yaw = np.radians(7.5)
    c, s = np.cos(yaw), np.sin(yaw)
    r = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    scale, t = 1.02, np.array([13.0, -4.0, 2.5])
    est = (ref @ r.T) * scale + t          # est = scale·R·ref + t

    out, info = align.align(est, ref, "sim3")
    assert np.max(np.abs(out - ref)) < 1e-8
    assert abs(info["scale"] - 1 / scale) < 1e-6, info["scale"]
    assert abs(info["yaw_deg"] + 7.5) < 1e-6, info["yaw_deg"]
    print(f"  Sim(3) 還原:殘差 {np.max(np.abs(out - ref)):.2e} m, "
          f"yaw {info['yaw_deg']:+.4f}°, scale {info['scale']:.6f}")

    est_rigid = (ref @ r.T) + t
    out, info = align.align(est_rigid, ref, "se3")
    assert np.max(np.abs(out - ref)) < 1e-8
    out, info = align.align(est_rigid, ref, "yaw")
    assert np.max(np.abs(out - ref)) < 1e-8, "純 yaw 變換應被 4DOF 完全吸收"
    print("  SE(3) / 4DOF 還原:殘差 < 1e-8 m")

    out, _ = align.align(est, ref, "none")
    assert np.allclose(out, est), "不對齊應原封不動"


def test_align_translation_only():
    rng = np.random.default_rng(2)
    ref = rng.normal(0, 10, (200, 3))
    est = ref + np.array([5.0, -2.0, 1.0])
    out, info = align.align(est, ref, "translation")
    assert np.max(np.abs(out - ref)) < 1e-9
    assert np.allclose(info["translation"], [-5.0, 2.0, -1.0])
    print("  僅平移對齊正確")


def test_metrics_known():
    """已知誤差:E 恆 +3 m → 3D RMSE = 3,E 平均 = +3(帶號),U 全 0。"""
    err = np.zeros((100, 3))
    err[:, 0] = 3.0
    s = metrics.ate_stats(err)
    assert abs(s["3d"]["rmse"] - 3.0) < 1e-12
    assert abs(s["2d"]["rmse"] - 3.0) < 1e-12
    assert abs(s["axes"]["E"]["mean"] - 3.0) < 1e-12
    assert abs(s["axes"]["U"]["max"]) < 1e-12
    assert s["n"] == 100

    err2 = np.zeros((4, 3))
    err2[:, 0] = [3.0, -4.0, 0.0, 0.0]
    err2[:, 1] = [4.0, 3.0, 0.0, 0.0]
    s2 = metrics.ate_stats(err2)
    assert abs(s2["3d"]["max"] - 5.0) < 1e-12, "3D 最大應為 5 (3-4-5)"
    assert abs(s2["axes"]["E"]["mean"] + 0.25) < 1e-12, "帶號平均 = (3-4)/4"
    assert abs(s2["axes"]["E"]["max"] - 4.0) < 1e-12, "軸向最大取絕對值"
    print("  ATE 統計:3-4-5 三角、帶號平均、絕對最大值皆正確")

    line = np.column_stack([np.arange(11.0), np.zeros(11), np.zeros(11)])
    assert abs(metrics.path_length(line) - 10.0) < 1e-12
    print("  路徑長度正確")


def test_sync_interp_and_gap():
    t_ref = np.arange(0, 10, 0.1)
    p_ref = np.column_stack([t_ref, 2 * t_ref, np.zeros_like(t_ref)])
    t_est = np.arange(0, 10, 0.037) + 0.4          # 不同取樣率 + 起訖不同
    p_est = np.column_stack([t_est, 2 * t_est, np.zeros_like(t_est)])
    t, rp, ep = sync.sync_pair(t_ref, p_ref, t_est, p_est)
    assert np.max(np.abs(rp - ep)) < 1e-9, "同一條線內插後應完全吻合"
    assert t[0] >= 0.4 - 1e-9 and t[-1] <= t_est[-1] + 1e-9, "應裁到重疊區間"
    print(f"  時間同步:{t.size} 對樣本,內插殘差 < 1e-9 m")

    # 資料空洞:est 缺 4~6 s,max_gap 應剔除該段
    keep = (t_est < 4.0) | (t_est > 6.0)
    t2, _, _ = sync.sync_pair(t_ref, p_ref, t_est[keep], p_est[keep], max_gap_s=0.2)
    assert not np.any((t2 > 4.3) & (t2 < 5.7)), "空洞區間應被剔除"
    print(f"  空洞保護:剔除後剩 {t2.size} 對(原 {t.size})")

    try:
        sync.sync_pair(t_ref, p_ref, t_est + 100, p_est)
    except ValueError:
        print("  無重疊時正確拋出例外")
    else:
        raise AssertionError("無重疊應拋出 ValueError")


def test_time_offset_search():
    logdata, _ = sim.make_sim_with_truth(seed=3)
    rt, rp, et, ep = pipeline.get_enu_pair(logdata, "GPS", "VISP")
    true_off = 0.35
    off, corr = sync.find_time_offset(rt, rp, et - true_off, ep, search_s=1.5)
    assert abs(off - true_off) < 0.08, f"估到 {off:.3f} s,應接近 {true_off} s"
    print(f"  時間偏移搜尋:注入 {true_off:.2f} s → 估得 {off:.3f} s (corr {corr:.3f})")


def test_pipeline_on_sim():
    logdata, truth = sim.make_sim_with_truth(seed=7)
    for name in ("GPS", "VISP", "POS"):
        assert name in logdata.sources
    print(f"  模擬來源:{ {k: f'{v.n}筆@{v.rate_hz:.0f}Hz' for k, v in logdata.sources.items()} }")

    res_none = pipeline.compute(logdata, "GPS", "VISP", method="none")
    res_se3 = pipeline.compute(logdata, "GPS", "VISP", method="se3")
    res_sim3 = pipeline.compute(logdata, "GPS", "VISP", method="sim3")
    r = [res_none.stats["3d"]["rmse"], res_se3.stats["3d"]["rmse"], res_sim3.stats["3d"]["rmse"]]
    print(f"  VISP vs GPS 3D RMSE:不對齊 {r[0]:.3f} → SE(3) {r[1]:.3f} → Sim(3) {r[2]:.3f} m")
    assert r[1] <= r[0] + 1e-9, "SE(3) 對齊不該讓誤差變大"
    assert r[2] <= r[1] + 1e-9, "Sim(3) 自由度更高,誤差不該比 SE(3) 大"

    # Sim(3) 應還原注入的尺度誤差(VISP 被放大 SCALE_ERR,對齊回 GPS 要縮回)
    got = res_sim3.align_info["scale"]
    assert abs(got - 1 / truth["scale"]) < 0.01, f"尺度還原 {got:.4f} vs 期望 {1 / truth['scale']:.4f}"
    print(f"  Sim(3) 尺度還原:{got:.4f}(注入 {truth['scale']},期望 ≈{1 / truth['scale']:.4f})")

    # VIO 在 NED 內被轉 +yaw_mis(等於 ENU 的 −yaw_mis),對齊回 GPS 需轉 +yaw_mis
    yaw = res_se3.align_info["yaw_deg"]
    exp = truth["yaw_mis_deg"]
    assert abs(yaw - exp) < 0.6, f"yaw 還原 {yaw:+.2f}° vs 期望 ≈{exp:+.1f}°"
    print(f"  SE(3) 偏航還原:{yaw:+.3f}°(注入 {exp:+.1f}°,誤差來自漂移)")

    # POS(EKF)應比 VISP(VIO)貼近 GPS
    res_pos = pipeline.compute(logdata, "GPS", "POS", method="se3")
    assert res_pos.stats["3d"]["rmse"] < res_se3.stats["3d"]["rmse"]
    print(f"  POS vs GPS 3D RMSE {res_pos.stats['3d']['rmse']:.3f} m < VISP 的 {r[1]:.3f} m ✓")

    # 時間裁切
    tb = logdata.t_base
    sub = pipeline.compute(logdata, "GPS", "VISP", t0=tb + 30, t1=tb + 60, method="se3")
    assert sub.n < res_se3.n and 29 < sub.duration < 31
    print(f"  時間裁切 30~60 s:{sub.n} 對樣本,時長 {sub.duration:.1f} s")

    try:
        pipeline.compute(logdata, "GPS", "GPS")
    except ValueError:
        print("  參考=估測時正確拋出例外")
    else:
        raise AssertionError("參考=估測應拋出 ValueError")


def test_report_and_csv():
    logdata = sim.make_sim_logdata()
    res = pipeline.compute(logdata, "GPS", "VISP", method="se3")
    txt = pipeline.format_report(res, logdata)
    for token in ("ATE RMSE", "RMSE", "P95", "空間對齊", "ENU 原點"):
        assert token in txt, f"報告缺少 {token}"
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ate.csv")
        pipeline.save_csv(res, logdata, p)
        rows = np.loadtxt(p, delimiter=",", skiprows=3, encoding="utf-8-sig")
        assert rows.shape == (res.n, 12), rows.shape
        # err_3d 欄位應等於重算的誤差長度
        assert np.max(np.abs(rows[:, 11] - res.e3)) < 1e-3
    print(f"  報告 {len(txt)} 字元、CSV {res.n} 列 × 12 欄,數值一致")


def test_bin_roundtrip():
    """寫出 .bin → 用 pymavlink 讀回 → ATE 結果應與原始模擬資料一致。"""
    logdata, _ = sim.make_sim_with_truth(seed=7)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sim.bin")
        fakebin.write_logdata_to_bin(logdata, p)
        size = os.path.getsize(p)
        loaded = logio.load_bin(p)
        print(f"  寫出 {size / 1024:.0f} KB .bin → 解析出來源 {sorted(loaded.sources)}")
        for name in ("GPS", "POS", "VISP"):
            assert name in loaded.sources, f"缺少 {name}(實得 {sorted(loaded.sources)})"
        assert loaded.origin is not None, "ORGN 未被解析"
        assert abs(loaded.origin[0] - sim.ORIGIN[0]) < 1e-6

        for name in ("GPS", "VISP", "POS"):
            a, b = logdata.sources[name], loaded.sources[name]
            assert a.n == b.n, f"{name} 樣本數 {a.n} → {b.n}"
            assert np.max(np.abs(a.t - b.t)) < 1e-5

        res_a = pipeline.compute(logdata, "GPS", "VISP", method="se3")
        res_b = pipeline.compute(loaded, "GPS", "VISP", method="se3")
        d_rmse = abs(res_a.stats["3d"]["rmse"] - res_b.stats["3d"]["rmse"])
        assert d_rmse < 0.02, f"RMSE 差異 {d_rmse:.4f} m 過大"
        print(f"  .bin 往返 RMSE:{res_a.stats['3d']['rmse']:.4f} → "
              f"{res_b.stats['3d']['rmse']:.4f} m(差 {d_rmse * 100:.2f} cm,經緯度量化所致)")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        print(f"\n[{fn.__name__}]")
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  !! 失敗:{type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{'=' * 60}\n{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
