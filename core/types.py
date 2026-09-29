"""資料模型:軌跡來源與載入結果。"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import frames

KIND_GLOBAL = "global"   # 經緯高 (GPS / POS / AHR2 ...)
KIND_NED = "ned"         # 局部 NED (VISP / XKF1 ...,相對 EKF 原點)

KIND_LABEL = {KIND_GLOBAL: "經緯度", KIND_NED: "局部 NED"}


@dataclass
class TrajSource:
    """單一軌跡來源(已按時間排序、去除無效樣本)。"""
    name: str
    kind: str
    t: np.ndarray                      # 秒(log 時基,TimeUS / 1e6)
    lat: np.ndarray | None = None      # kind == global
    lng: np.ndarray | None = None
    alt: np.ndarray | None = None
    ned: np.ndarray | None = None      # kind == ned, Nx3 (N, E, D) 公尺
    meta: str = ""

    @property
    def n(self) -> int:
        return int(len(self.t))

    @property
    def rate_hz(self) -> float:
        if self.n < 2:
            return 0.0
        dt = float(np.median(np.diff(self.t)))
        return 1.0 / dt if dt > 1e-9 else 0.0

    def enu(self, origin: tuple[float, float, float] | None) -> np.ndarray:
        """轉為局部 ENU (Nx3)。global 來源需要 origin。"""
        if self.kind == KIND_GLOBAL:
            if origin is None:
                raise ValueError(f"來源 {self.name} 為經緯度,需要 ENU 原點(log 無 ORGN 訊息)")
            return frames.geodetic_to_enu(self.lat, self.lng, self.alt, origin)
        return frames.ned_to_enu(self.ned)


@dataclass
class LogData:
    """一份 log(或模擬資料)解析後的全部軌跡來源。"""
    sources: dict[str, TrajSource]
    origin: tuple[float, float, float] | None = None   # EKF 原點 (lat, lng, alt)
    origin_desc: str = ""
    path: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def t_base(self) -> float:
        return min((float(s.t[0]) for s in self.sources.values() if s.n), default=0.0)

    @property
    def t_end(self) -> float:
        return max((float(s.t[-1]) for s in self.sources.values() if s.n), default=0.0)

    @property
    def duration(self) -> float:
        return max(0.0, self.t_end - self.t_base)
