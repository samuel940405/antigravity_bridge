# -*- coding: utf-8 -*-
"""
nav_brain.py — V3 導航大腦（反凍結強化版）
=========================================
核心修改（對比 V2）：
  1. WalkingSpeedModel     — Ornstein-Uhlenbeck 速度模型，消除加速度不連續性
  2. _realistic_gps_jitter — 重尾分佈取代高斯，模擬真實多路徑 GPS 誤差
  3. generate_footstep_modulated_path — 步態週期調製，對齊 iOS CMPedometer 驗證
  4. FreezeDetector        — 凍結偵測 + 強制喚醒機制
  5. BreathingPauseManager — 每 3-6 分鐘插入停頓，防止 Watchdog 觸發

不變部分：
  - RoutePlanner / optimize_tsp / smooth_path 介面完全相容 V2
  - interpolate_path 仍可單獨使用（但建議改用 generate_footstep_modulated_path）
"""
import math
import random
import time
import asyncio
import numpy as np
import networkx as nx
import osmnx as ox
from geopy.distance import geodesic
from typing import Optional, Union

# ─── OSMnx 全局設定 ────────────────────────────────────────────────────────
ox.settings.use_cache   = True
ox.settings.log_console = False

# ─── 常數 ──────────────────────────────────────────────────────────────────
DEFAULT_STEP_M  = 0.80   # 步長縮短至接近真實步距（原 4.0m 過大）
_TURN_DEG       = 35.0
_SPD_MIN        = 4.0
_SPD_MAX        = 7.0
_SPD_TURN       = 2.0
DEFAULT_RADIUS  = 2000


# ─── 雙向相容取值 ───────────────────────────────────────────────────────────
def _lat(poi: Union[dict, list, tuple]) -> float:
    if isinstance(poi, dict):
        return float(poi.get('lat', poi.get('y', 0.0)))
    return float(poi[0])

def _lng(poi: Union[dict, list, tuple]) -> float:
    if isinstance(poi, dict):
        return float(poi.get('lon', poi.get('lng', poi.get('x', 0.0))))
    return float(poi[1])


# ─── 幾何工具 ───────────────────────────────────────────────────────────────
def _haversine_m(p1, p2) -> float:
    la1, lo1 = _lat(p1), _lng(p1)
    la2, lo2 = _lat(p2), _lng(p2)
    R = 6_371_000
    f1, f2 = math.radians(la1), math.radians(la2)
    df = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a  = math.sin(df/2)**2 + math.cos(f1)*math.cos(f2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(max(0, a)))

def _bearing(p1, p2) -> float:
    la1, lo1 = math.radians(_lat(p1)), math.radians(_lng(p1))
    la2, lo2 = math.radians(_lat(p2)), math.radians(_lng(p2))
    dl = lo2 - lo1
    x  = math.sin(dl)*math.cos(la2)
    y  = math.cos(la1)*math.sin(la2) - math.sin(la1)*math.cos(la2)*math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def _turn_angle(b1: float, b2: float) -> float:
    d = abs(b2 - b1)
    return min(d, 360 - d)


# ═══════════════════════════════════════════════════════════════════════════
# ① WalkingSpeedModel — Ornstein-Uhlenbeck 速度過程
# ═══════════════════════════════════════════════════════════════════════════
class WalkingSpeedModel:
    """
    以 Ornstein-Uhlenbeck 隨機過程模擬人類步行速度。

    物理意義：
      速度有慣性（不會瞬間突變），圍繞目標速度做均值回歸，
      疊加隨機擾動。這是消除 iOS 加速度不連續偵測的關鍵。

    參數（預設為中原大學校園步行場景）：
      mu    = 5.2  km/h  — 均值回歸目標（校園平均步行速度）
      theta = 0.25       — 回歸強度（越大越快回到均值）
      sigma = 0.35       — 波動幅度（越大速度變化越劇烈）
    """

    def __init__(self, target_kmh: float = 5.2, theta: float = 0.25, sigma: float = 0.35):
        self.v     = target_kmh
        self.mu    = target_kmh
        self.theta = theta
        self.sigma = sigma

    def set_target(self, kmh: float):
        """動態調整目標速度（轉彎時呼叫）。"""
        self.mu = kmh

    def next_speed(self, dt: float = 1.0) -> float:
        """
        更新並回傳下一步速度（km/h）。
        dt: 時間步長（秒），對應每個插值點的時間間隔。
        """
        dv = (self.theta * (self.mu - self.v) * dt
              + self.sigma * math.sqrt(dt) * random.gauss(0, 1))
        self.v = max(0.8, min(9.5, self.v + dv))
        return round(self.v, 3)


# ═══════════════════════════════════════════════════════════════════════════
# ② GPS 誤差模型 — 重尾分佈
# ═══════════════════════════════════════════════════════════════════════════
def _realistic_gps_jitter(lat_ref: float = 25.0) -> tuple[float, float]:
    """
    模擬真實 GPS 誤差的重尾特性。

    分佈設計（中原大學建築密集校園場景）：
      70% — 正常誤差：0–1.5m（高斯核心）
      25% — 一般多路徑：1.5–4m（建築反射）
       5% — 嚴重多路徑：4–10m（峽谷效應，如工學院中庭）

    相比原版高斯 0.04m，這更接近 iPhone GPS 在校園環境的真實表現，
    iOS CoreLocation 的統計驗證器不會將其標記為「人工注入特徵」。
    """
    r = random.random()
    if r < 0.70:
        magnitude = abs(random.gauss(0, 6e-6))        # ~0–1.5m
    elif r < 0.95:
        magnitude = random.uniform(1.3e-5, 3.6e-5)    # ~1.5–4m
    else:
        magnitude = random.uniform(3.6e-5, 9e-5)      # ~4–10m

    angle = random.uniform(0, 2 * math.pi)
    j_lat = magnitude * math.cos(angle)
    # 經度補償緯度壓縮
    cos_lat = max(math.cos(math.radians(lat_ref)), 1e-9)
    j_lon = (magnitude * math.sin(angle)) / cos_lat
    return j_lat, j_lon


# ═══════════════════════════════════════════════════════════════════════════
# ③ 核心插值：步態週期調製版（取代原 interpolate_path 的主要用途）
# ═══════════════════════════════════════════════════════════════════════════
def generate_footstep_modulated_path(
    path: list,
    speed_model: Optional[WalkingSpeedModel] = None,
    step_m: float = DEFAULT_STEP_M,
) -> list[dict]:
    """
    在基礎路徑上疊加步態週期速度調製，生成對 iOS CMPedometer 友好的座標流。

    步態模型：
      - 步態週期：0.92–1.08 秒/步（真實人類範圍）
      - 每步速度：正弦調製 ±15%（加速踏步 → 減速著地）
      - 步長：0.68–0.88m + 5% 高斯噪聲

    Args:
        path:        路網節點序列
        speed_model: WalkingSpeedModel 實例（None 則自動建立）
        step_m:      基礎插值步長（公尺）

    Returns:
        [{'lat', 'lon', 'speed_kmh', 'step_meters', 'is_turning'}, ...]
    """
    if not path:
        return []
    if len(path) < 2:
        return [{'lat': _lat(path[0]), 'lon': _lng(path[0]),
                 'speed_kmh': 4.0, 'step_meters': step_m, 'is_turning': False}]

    if speed_model is None:
        speed_model = WalkingSpeedModel()

    # 步態參數（每次呼叫重新採樣，模擬不同的行走節奏）
    step_period = random.uniform(0.92, 1.08)   # 秒/步
    step_length = random.uniform(0.68, 0.88)   # 公尺/步
    phase_amp   = random.uniform(0.10, 0.18)   # 步態調製幅度

    bearings = [_bearing(path[i], path[i+1]) for i in range(len(path)-1)]
    result: list[dict] = []
    step_phase = 0.0
    lat_ref = _lat(path[0])

    for seg in range(len(path) - 1):
        p1, p2   = path[seg], path[seg+1]
        lat1, lon1 = _lat(p1), _lng(p1)
        lat2, lon2 = _lat(p2), _lng(p2)
        dist = _haversine_m(p1, p2)
        n    = max(1, math.ceil(dist / step_m))
        actual_step = dist / n

        turning = (seg > 0 and
                   _turn_angle(bearings[seg - 1], bearings[seg]) > _TURN_DEG)

        # 轉彎時降低目標速度
        if turning:
            speed_model.set_target(_SPD_TURN)
        else:
            speed_model.set_target(random.uniform(_SPD_MIN, _SPD_MAX))

        for k in range(n):
            t = k / n
            base_lat = lat1 + t * (lat2 - lat1)
            base_lon = lon1 + t * (lon2 - lon1)

            # 步態相位調製（正弦速度波動）
            phase_mod = phase_amp * math.sin(2 * math.pi * step_phase)
            dt = actual_step / max(speed_model.v / 3.6, 0.1)
            speed = speed_model.next_speed(dt=dt) * (1.0 + phase_mod)
            speed = max(0.5, speed)

            # 重尾 GPS 抖動
            j_lat, j_lon = _realistic_gps_jitter(lat_ref)

            result.append({
                'lat':         base_lat + j_lat,
                'lon':         base_lon + j_lon,
                'speed_kmh':   round(speed, 3),
                'step_meters': round(step_length * (1 + random.gauss(0, 0.05)), 3),
                'is_turning':  turning,
            })

            # 更新步態相位
            step_phase += actual_step / (step_length * step_period)

    # 終點
    last = path[-1]
    j_lat, j_lon = _realistic_gps_jitter(lat_ref)
    result.append({
        'lat':         _lat(last) + j_lat,
        'lon':         _lng(last) + j_lon,
        'speed_kmh':   _SPD_TURN,
        'step_meters': step_m,
        'is_turning':  False,
    })
    return result


# ─── 向後相容：保留原 interpolate_path（內部改用新模型）─────────────────────
def interpolate_path(
    path: list,
    step_m: float = DEFAULT_STEP_M,
) -> list[dict]:
    """
    向後相容介面。
    內部已改用 generate_footstep_modulated_path，行為與 V2 API 相同。
    """
    return generate_footstep_modulated_path(path, step_m=step_m)


def smooth_path(
    path: list,
    step_m: float = DEFAULT_STEP_M,
    closed: bool = True,
) -> list[dict]:
    """封閉路徑版（向後相容）。"""
    if not path or len(path) < 2:
        return interpolate_path(path, step_m=step_m)
    closed_path = list(path) + [path[0]] if closed else list(path)
    return generate_footstep_modulated_path(closed_path, step_m=step_m)


# ═══════════════════════════════════════════════════════════════════════════
# ④ FreezeDetector — 凍結偵測與強制喚醒
# ═══════════════════════════════════════════════════════════════════════════
class FreezeDetector:
    """
    偵測 iOS 端座標凍結並觸發反制。

    凍結判斷：連續 threshold_sec 秒內，PC 端注入座標變化但
    last_location 不再被更新（由外部呼叫 update() 傳入實際注入值）。

    反制策略：
      注入一個距離約 40–60m 的偏移座標，強制 CoreLocation 重新評估，
      再立即拉回正確位置。
    """

    def __init__(self, threshold_sec: float = 25.0):
        self.threshold    = threshold_sec
        self.freeze_count = 0
        self._last_coord  = None
        self._last_change = time.time()

    def update(self, lat: float, lon: float) -> bool:
        """
        傳入最新注入座標。
        Returns: True = 偵測到凍結，應立即呼叫 unfreeze()
        """
        rounded = (round(lat, 5), round(lon, 5))
        now = time.time()
        if rounded != self._last_coord:
            self._last_coord  = rounded
            self._last_change = now
            return False
        return (now - self._last_change) > self.threshold

    async def unfreeze(self, bridge) -> None:
        """
        向 bridge 注入「震盪-復位」序列以解除 CoreLocation 凍結。
        bridge: LocationBridge 實例
        """
        if bridge.last_location is None:
            return

        self.freeze_count += 1
        lat, lon = bridge.last_location
        print(f"[FreezeDetector] ⚠️ 凍結 #{self.freeze_count}，執行解凍序列...")

        # 偏移約 45m（4.05e-4 度 ≈ 45m）
        offset = random.choice([-4.05e-4, 4.05e-4])
        await bridge.set_location(lat + offset, lon)
        await asyncio.sleep(1.5)
        # 拉回
        await bridge.set_location(lat, lon)
        await asyncio.sleep(1.0)
        print("[FreezeDetector] ✅ 解凍序列完成")


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ BreathingPauseManager — 週期性停頓防 Watchdog
# ═══════════════════════════════════════════════════════════════════════════
class BreathingPauseManager:
    """
    每隔 walk_interval_sec 秒插入一次停頓（15–45 秒）。

    停頓期間：
      - 以 2.5–3.5 秒間隔持續注入「靜止漂移」座標
      - 漂移幅度 ~0–2m（符合真實靜止時的 GPS 誤差）
      - is_navigating 標誌由外部控制

    用法（在 bridge.py 的 start_services 中加入）：
        pause_mgr = BreathingPauseManager(bridge)
        asyncio.create_task(pause_mgr.run(), name="breathing")
    """

    def __init__(self, bridge, walk_interval_sec: tuple = (200, 380)):
        self.bridge   = bridge
        self.interval = walk_interval_sec  # (min, max) 行走秒數

    async def run(self) -> None:
        print("[BreathingPause] 週期性停頓管理器已啟動")
        while True:
            walk_sec = random.uniform(*self.interval)
            await asyncio.sleep(walk_sec)

            if not self.bridge.is_navigating:
                continue   # 已靜止，不需要額外停頓

            pause_sec = random.uniform(15, 45)
            print(f"[BreathingPause] 🧘 模擬停頓 {pause_sec:.0f}s（防 Watchdog）")

            # 暫停導航
            was_navigating = self.bridge.is_navigating
            self.bridge.is_navigating = False

            elapsed = 0.0
            while elapsed < pause_sec:
                if self.bridge.last_location:
                    lat, lon = self.bridge.last_location
                    # 靜止漂移：重尾小幅抖動
                    j_lat, j_lon = _realistic_gps_jitter(lat)
                    j_lat *= 0.3   # 靜止時誤差更小
                    j_lon *= 0.3
                    await self.bridge.set_location(lat + j_lat, lon + j_lon)
                interval = random.uniform(2.5, 3.8)
                await asyncio.sleep(interval)
                elapsed += interval

            # 恢復導航
            self.bridge.is_navigating = was_navigating
            print("[BreathingPause] ▶️ 恢復行走")


# ═══════════════════════════════════════════════════════════════════════════
# RoutePlanner（與 V2 完全相容）
# ═══════════════════════════════════════════════════════════════════════════
class RoutePlanner:
    """
    統一路由介面：OSMnx Dijkstra。
    支援任意格式 waypoints（dict 或 tuple）。
    """

    def __init__(self):
        self.G: Optional[nx.MultiDiGraph]      = None
        self.G_proj: Optional[nx.MultiDiGraph] = None
        self._to_proj = None

    def load_graph(self, center_lat: float, center_lon: float, radius_m: int = DEFAULT_RADIUS):
        print(f"[RoutePlanner] 下載 OSMnx 步行路網 r={radius_m}m...")
        self.G      = ox.graph_from_point((center_lat, center_lon),
                                          dist=radius_m, network_type='walk')
        self.G      = ox.add_edge_speeds(self.G)
        self.G      = ox.add_edge_travel_times(self.G)
        self.G_proj = ox.project_graph(self.G)
        from pyproj import Transformer
        self._to_proj = Transformer.from_crs(
            "EPSG:4326", self.G_proj.graph['crs'], always_xy=True
        )
        print(f"[RoutePlanner] 路網就緒：{len(self.G.nodes)} 節點")

    def _nearest_node(self, lat: float, lon: float) -> int:
        x, y = self._to_proj.transform(lon, lat)
        return ox.distance.nearest_nodes(self.G_proj, X=x, Y=y)

    def _osmnx_segment(self, p1, p2) -> list[tuple]:
        orig = self._nearest_node(_lat(p1), _lng(p1))
        dest = self._nearest_node(_lat(p2), _lng(p2))
        nodes = nx.shortest_path(self.G_proj, orig, dest, weight='length')
        return [(self.G.nodes[n]['y'], self.G.nodes[n]['x']) for n in nodes]

    def route(self, waypoints: list) -> list[tuple]:
        if len(waypoints) < 2:
            return [(_lat(w), _lng(w)) for w in waypoints]
        if self.G is None:
            raise RuntimeError("路網未載入，請先呼叫 load_graph()")
        full: list[tuple] = []
        for i in range(len(waypoints) - 1):
            seg = self._osmnx_segment(waypoints[i], waypoints[i+1])
            if i > 0 and full and seg:
                seg = seg[1:]
            full.extend(seg)
        return full


# ─── TSP（與 V2 完全相容）──────────────────────────────────────────────────
def optimize_tsp(
    poi_list: list,
    planner: Optional[RoutePlanner] = None,
) -> list:
    n = len(poi_list)
    if n < 2:
        return poi_list

    G_tsp = nx.Graph()

    if planner and planner.G_proj is not None:
        osm_nodes = [planner._nearest_node(_lat(p), _lng(p)) for p in poi_list]
        for i in range(n):
            for j in range(i+1, n):
                try:
                    d = nx.shortest_path_length(
                        planner.G_proj, osm_nodes[i], osm_nodes[j], weight='length')
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    d = geodesic((_lat(poi_list[i]), _lng(poi_list[i])),
                                 (_lat(poi_list[j]), _lng(poi_list[j]))).meters * 1.5
                G_tsp.add_edge(i, j, weight=d)
    else:
        for i in range(n):
            for j in range(i+1, n):
                d = geodesic((_lat(poi_list[i]), _lng(poi_list[i])),
                             (_lat(poi_list[j]), _lng(poi_list[j]))).meters
                G_tsp.add_edge(i, j, weight=d)

    try:
        tsp_nodes = nx.approximation.traveling_salesman_problem(
            G_tsp, weight='weight', cycle=False)
    except Exception:
        tsp_nodes = _greedy_tsp(poi_list)
        return [poi_list[i] for i in tsp_nodes]

    seen: list[int] = []
    for idx in tsp_nodes:
        if idx not in seen:
            seen.append(idx)
    return [poi_list[i] for i in seen]


def _greedy_tsp(poi_list: list) -> list[int]:
    unvisited = list(range(len(poi_list)))
    order = [unvisited.pop(0)]
    while unvisited:
        curr = order[-1]
        p = (_lat(poi_list[curr]), _lng(poi_list[curr]))
        nxt = min(unvisited,
                  key=lambda j: geodesic(p, (_lat(poi_list[j]),
                                              _lng(poi_list[j]))).meters)
        unvisited.remove(nxt)
        order.append(nxt)
    return order