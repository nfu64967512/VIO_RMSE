"""產生最小可用的 ArduPilot DataFlash .bin(供自我測試 pymavlink 解析路徑)。

格式:每筆訊息 = 0xA3 0x95 <msgid> <payload>;msgid 128 (FMT) 定義其他訊息的
名稱 / 格式字元 / 欄位。格式字元沿用 ArduPilot 慣例:
  B=uint8, I=uint32, Q=uint64, f=float32,
  L=int32(度×1e7, 讀取時自動 ÷1e7), e=int32(×100, 讀取時 ÷100)
"""
from __future__ import annotations

import struct

from .types import KIND_GLOBAL, KIND_NED, LogData

_HEAD = b"\xa3\x95"
_FMT_ID = 0x80

# 格式字元 → (struct 碼, 位元組數, 寫入前乘數)
_PACK = {
    "B": ("B", 1, None),
    "H": ("H", 2, None),
    "I": ("I", 4, None),
    "Q": ("Q", 8, None),
    "f": ("f", 4, None),
    "L": ("i", 4, 1e7),
    "e": ("i", 4, 100.0),
    "n": ("4s", 4, None),
    "N": ("16s", 16, None),
    "Z": ("64s", 64, None),
}


def _payload_size(fmt: str) -> int:
    return sum(_PACK[ch][1] for ch in fmt)


class DFBinWriter:
    def __init__(self):
        self.buf = bytearray()
        self.defs: dict[str, tuple[int, str]] = {}
        self.next_id = 129
        # FMT 自我定義要放最前面;Length 欄位 = 3 位元組 header + payload
        self._emit(_FMT_ID, "BB4s16s64s",
                   [_FMT_ID, 3 + _payload_size("BBnNZ"), b"FMT", b"BBnNZ",
                    b"Type,Length,Name,Format,Columns"])

    def _emit(self, msg_id: int, struct_fmt: str, values: list):
        self.buf += _HEAD + bytes([msg_id]) + struct.pack("<" + struct_fmt, *values)

    def add_message_type(self, name: str, fmt: str, columns: str) -> None:
        msg_id = self.next_id
        self.next_id += 1
        self.defs[name] = (msg_id, fmt)
        self._emit(_FMT_ID, "BB4s16s64s",
                   [msg_id, 3 + _payload_size(fmt),
                    name.encode()[:4], fmt.encode()[:16], columns.encode()[:64]])

    def write(self, name: str, values: list) -> None:
        msg_id, fmt = self.defs[name]
        packed, codes = [], []
        for ch, v in zip(fmt, values):
            code, _, mult = _PACK[ch]
            codes.append(code)
            packed.append(int(round(v * mult)) if mult is not None else v)
        self._emit(msg_id, "".join(codes), packed)

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(bytes(self.buf))


def write_logdata_to_bin(logdata: LogData, path: str) -> None:
    """把(模擬)LogData 寫成 dataflash .bin:ORGN + GPS + POS + VISP。"""
    w = DFBinWriter()
    w.add_message_type("ORGN", "QBLLe", "TimeUS,Type,Lat,Lng,Alt")
    # GPS 需帶 GWk/GMS:DFReader 靠這兩欄建立 log 時基,缺了會逐筆解析失敗
    w.add_message_type("GPS", "QBBHILLe", "TimeUS,I,Status,GWk,GMS,Lat,Lng,Alt")
    w.add_message_type("POS", "QLLf", "TimeUS,Lat,Lng,Alt")
    w.add_message_type("VISP", "QfffB", "TimeUS,PX,PY,PZ,ResetCnt")

    records: list[tuple[int, str, list]] = []
    t0 = logdata.t_base
    gps_week, gps_ms0 = 2320, 300_000  # 任意有效 GPS 週/週內毫秒
    if logdata.origin is not None:
        lat0, lng0, alt0 = logdata.origin
        records.append((int(t0 * 1e6), "ORGN", [int(t0 * 1e6), 0, lat0, lng0, alt0]))

    for src in logdata.sources.values():
        if src.kind == KIND_GLOBAL:
            name = "GPS" if src.name.upper().startswith("GPS") else "POS"
            for k in range(src.n):
                us = int(src.t[k] * 1e6)
                if name == "GPS":
                    gms = gps_ms0 + int(round((src.t[k] - t0) * 1000))
                    records.append((us, "GPS",
                                    [us, 0, 3, gps_week, gms,
                                     src.lat[k], src.lng[k], src.alt[k]]))
                else:
                    records.append((us, "POS", [us, src.lat[k], src.lng[k], src.alt[k]]))
        elif src.kind == KIND_NED:
            for k in range(src.n):
                us = int(src.t[k] * 1e6)
                records.append((us, "VISP",
                                [us, src.ned[k, 0], src.ned[k, 1], src.ned[k, 2], 0]))

    records.sort(key=lambda r: r[0])
    for _, name, vals in records:
        w.write(name, vals)
    w.save(path)
