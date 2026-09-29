# VIO / GPS ATE Studio — 絕對軌跡誤差分析

從 **ArduPilot `.bin` 記錄檔**讀出多條位置軌跡,做時間同步與空間對齊後計算
**ATE(Absolute Trajectory Error,絕對軌跡誤差)**,附 PyQt6 圖形介面。

主要用途:評估從 **ROS2 以 `VISION_POSITION_ESTIMATE` 餵進飛控的 VIO 定位**
(log 中的 `VISP` 訊息)相對 GPS 的偏差 —— 偏多少、往哪偏、何時開始漂、在場地哪裡漂。

---

## 安裝與啟動

```powershell
cd C:\Users\joy46\Documents\Projects\vio_ate_studio
pip install -r requirements.txt
python main.py
```

需求:Python ≥ 3.10、PyQt6、numpy、matplotlib、pymavlink。

沒有 log 也能先試跑 —— 按介面上的 **「模擬資料」**,會產生一段 30 m 方形任務
(GPS 5 Hz / VISP 30 Hz / POS 10 Hz),VIO 端注入了已知誤差:
偏航 1.5°、尺度 1.004、隨機漫步漂移約 0.9 m。可以用它確認對齊演算法有沒有把
這些誤差還原出來。

---

## 使用流程

1. **開啟 `.bin`**(或按「模擬資料」)。程式會掃出所有位置來源:
   `GPS`、`GPS[1]`、`POS`、`VISP`、`XKF1`、`AHR2` …,並自動抓 `ORGN` 當 ENU 原點。
2. **選兩條軌跡**:參考(基準)通常選 `GPS`,估測(受測)選 `VISP`。
3. **選空間對齊方式**(見下節)。
4. 兩邊時間戳若有系統性延遲,按 **「自動估」**,用速度剖面互相關找出偏移。
5. 起降前後停在地面的區段會稀釋統計,用 **分析起點 / 終點** 裁掉。
6. 看各分頁,或匯出 CSV / 報告 / PNG。

---

## 空間對齊為什麼必要

VIO 的座標原點與朝向通常跟 GPS 不同框(VIO 從開機位置起算、朝向由初始化決定),
不對齊就算出一堆「其實只是座標系差異」的誤差。

| 方式 | 自由度 | 什麼時候用 |
|---|---|---|
| **SE(3)**(預設) | 平移 + 旋轉 | VIO 與 GPS 不同框。最常用 |
| **Sim(3)** | 再加尺度 | 想量 VIO 的**尺度誤差** —— 看報告的「尺度」欄位 |
| **4DOF** | 偏航 + 平移 | 重力方向已對齊(只差水平旋轉) |
| **僅平移** | 平移 | 只想去掉原點差 |
| **不對齊** | — | 兩者本來就同框(例如都已是 EKF 原點座標) |

對齊採 Umeyama 最小平方解。注意:**任何含平移的對齊都會讓各軸平均誤差歸零**
(質心被對齊),所以「平均偏差」在對齊後沒有資訊量 —— 軸向誤差圖畫的是
**移動平均趨勢線**,那才看得出低頻漂移。

---

## 各分頁在回答什麼問題

| 分頁 | 回答 |
|---|---|
| 資料總覽 | log 裡有哪些來源、各自長什麼樣、時間涵蓋是否重疊(每個來源一張小圖) |
| 平面軌跡 | 兩條軌跡的整體對照,細線是每時刻的誤差連線 |
| **誤差熱點** | **在場地的哪個位置誤差變大**(誤差映成顏色畫在軌跡上) |
| 3D 軌跡 | 含高度的立體對照 |
| 誤差時序 | 誤差**何時**變大;VIO 漂移會看到單調上升 |
| 軸向誤差 | 是**哪個方向**在偏(東/北/天三軸小倍數 + 移動平均趨勢) |
| 誤差分佈 | 直方圖與累積分佈;P95 = 95% 的樣本落在多少誤差之內 |
| 統計表 | 全部數值的表格版 |
| 報告 | 可直接貼進文件的文字報告 |

軌跡尺度(數十~數百公尺)通常遠大於誤差(公尺級),兩條線疊圖幾乎看不出差別 ——
**要看誤差落在哪裡請用「誤差熱點」分頁**,那是為此設計的。

---

## 統計定義

同步後成對位置差 `Δ = 估測 − 參考`(ENU,公尺):

- **3D 誤差** = ‖Δ‖;**水平誤差** = ‖Δ_EN‖(只取東、北)
- **RMSE / 平均 / 中位數 / 標準差 / P90 / P95 / 最大**
- 軸向(東 E / 北 N / 天 U)的**平均與中位數保留正負號**(代表系統性偏差方向);
  最大 / P90 / P95 取絕對值

時間同步:把估測軌跡線性內插到參考軌跡的時間戳上,只取兩者重疊的區間。
估測軌跡若有資料空洞,超過「內插間隙上限」的區段會被剔除,不會跨洞內插出假資料。

> ⚠ **GPS 本身有 0.3~2 m 誤差。** 拿 GPS 當參考時,算出來的是「VIO 與 GPS 的差異」,
> 不是 VIO 的絕對精度。要真值請用 RTK 或動作捕捉(OptiTrack)。

---

## 命令列(headless)

```powershell
python main.py --cli info <log.bin>                      # 列出 log 裡的位置來源
python main.py --cli ate <log.bin> --ref GPS --est VISP  # 算 ATE
python main.py --cli ate <log.bin> --ref GPS --est VISP --align sim3 --auto-offset
python main.py --cli ate <log.bin> --csv out.csv --report out.txt --t0 20 --t1 180
python main.py --cli ate --sim --align se3               # 用模擬資料
```

---

## 支援的 log 訊息

| 訊息 | 座標框 | 說明 |
|---|---|---|
| `GPS` / `GPS2` / `GPSB` | 經緯高 | 衛星定位。VIO 若以 `GPS_INPUT` 注入會出現在 instance 1(顯示為 `GPS[1]`) |
| `POS` | 經緯高 | EKF 融合後的位置 |
| `VISP` | 局部 NED | `VISION_POSITION_ESTIMATE` —— ROS2 餵進來的 vision position |
| `XKF1` / `NKF1` | 局部 NED | EKF3 / EKF2 內部位置估計 |
| `AHR2` | 經緯高 | 次要 AHRS 解 |
| `ORGN` | — | EKF 原點,用來把經緯度換算成局部 ENU |

`GPS` 會濾掉非 3D-fix 的樣本。若 log 沒有 `ORGN`,程式改用第一個經緯度來源的
第一點當原點(只影響絕對座標,不影響 ATE 數值)。

找不到位置訊息時,錯誤訊息會列出這份 log **實際包含的所有訊息型別**,
方便判斷 VIO 是不是記在別的名稱下。要加新來源:改 `core/logio.py` 的 `CANDIDATE_TYPES`。

---

## 專案結構

```
core/
  frames.py    WGS84 ↔ 局部 ENU、NED ↔ ENU 轉換
  types.py     TrajSource / LogData 資料模型
  logio.py     .bin 解析(pymavlink DFReader)
  sync.py      時間同步、時間偏移自動搜尋
  align.py     SE(3) / Sim(3) / 4DOF / 平移 對齊(Umeyama)
  metrics.py   ATE 統計
  pipeline.py  完整計算管線 + 報告 / CSV 匯出
  sim.py       模擬資料(含已知注入誤差)
  fakebin.py   把模擬資料寫成 .bin(供測試解析路徑)
ui/
  theme.py     深色主題、色彩角色、matplotlib rcParams
  plots.py     所有圖表
  widgets.py   卡片、統計磚、統計表
  main_window.py
cli.py         命令列
tests/
  test_core.py 核心驗證(11 項)
  test_gui.py  GUI 煙霧測試(不需人工點擊)
  shot.py      離線把每張圖存成 PNG(不需開 GUI)
output/
  sample_sim.bin  模擬資料寫成的 .bin,可用來試「開啟 .bin」流程
  shots/          tests\shot.py 產生的圖表範例
```

## 測試

```powershell
python tests\test_core.py     # 核心 11 項驗證
python tests\test_gui.py      # GUI 煙霧測試(會短暫開關視窗)
python tests\shot.py          # 把每張圖存成 PNG 到 output/shots
```

`test_core.py` 涵蓋:座標轉換往返精度(< 1e-6 m)、對齊演算法對已知變換的還原
(殘差 < 1e-8 m、尺度與偏航參數可逆推)、ATE 統計(3-4-5 三角、帶號偏差)、
時間同步與空洞保護、時間偏移搜尋、`.bin` 寫出→解析往返一致性。

`test_gui.py` 涵蓋:主視窗建立、**背景執行緒解析 `.bin`**、每張圖都畫得出來、
切換對齊方式時結果跟著更新(並驗證 Sim(3) ≤ SE(3) ≤ 不對齊)、時間裁切、
自動估時間偏移、換來源、參考=估測時不當機、CSV/報告/PNG 匯出、關閉時執行緒收乾淨。

---

## 色彩

介面與圖表用固定的**角色配色**(顏色跟著實體走,不跟著排序):
參考=藍、估測=橘、誤差=洋紅、誤差(水平)=紫;誤差大小用單一藍色相的連續色階。
所有配色組合都通過 OKLab 色覺缺陷分離度(protan / deutan ΔE)與表面對比度檢查;
序列身分一律同時有圖例與文字標籤,不單靠顏色傳達。
