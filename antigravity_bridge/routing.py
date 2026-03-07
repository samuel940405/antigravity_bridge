# -*- coding: utf-8 -*-
"""
routing.py — PathPlanner + TSP 最佳化

設計原則（OSMnx Best Practice）：
  * 所有幾何計算在「投影圖（G_proj, UTM CRS）」中進行
    - nearest_nodes 使用 KD-tree（不依賴 scikit-learn 近似）
    - shortest_path 在平面座標系運算，速度更快
  * lat/lon 輸出從「原始圖（G）」節點讀取 'y'/'x' 屬性

核心功能：
  1. PathPlanner                  — 懶載入投影、Dijkstra 路網尋路、插值補點
  2. optimize_tsp_route()         — 路網距離矩陣 + NetworkX Christofides TSP
"""
import math
import numpy as np
import networkx as nx
import osmnx as ox
from geopy.distance import geodesic

# ─── 速度常數 ─────────────────────────────────────────────────────────────────
_SPEED_STRAIGHT_MIN = 5.0   # km/h 直線最低
_SPEED_STRAIGHT_MAX = 8.0   # km/h 直線最高
_SPEED_TURNING      = 2.0   # km/h 過彎降速
_TURN_THRESHOLD     = 45.0  # 超過此角度（度）視為轉彎段 → 強制降速 2.5 km/h


# ─── 工具函式 ─────────────────────────────────────────────────────────────────
def _bearing_deg(p1: tuple, p2: tuple) -> float:
    """p1→p2 方位角（度，正北=0，順時針）。"""
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _turn_angle(b1: float, b2: float) -> float:
    """前後兩段方位角差（0–180 度）。"""
    d = abs(b2 - b1)
    return min(d, 360 - d)


# ─── PathPlanner ──────────────────────────────────────────────────────────────
class PathPlanner:
    """
    封裝投影路網的路徑規劃器。

    投影流程（一次性，在 __init__ 完成）：
      G_proj = ox.project_graph(G)          → UTM 平面座標
      _transformer = pyproj.Transformer     → WGS84 ↔ UTM

    nearest_nodes 查找：先以 Transformer 換算座標，再呼叫
      ox.distance.nearest_nodes(G_proj, X=x_utm, Y=y_utm)
    這比直接在球面圖查找快 3–10 倍。
    """

    def __init__(self, graph: nx.MultiDiGraph):
        self.G = graph                          # 原始圖（存 lat/lon）
        self.G_proj = ox.project_graph(graph)   # 投影圖（存 UTM x/y，用於計算）
        crs = self.G_proj.graph['crs']          # pyproj CRS 物件
        from pyproj import Transformer
        # always_xy=True：輸入/輸出均為 (經度, 緯度) 或 (x, y) 順序
        self._to_proj = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        print(f"[PathPlanner] 投影圖已建立，CRS={crs}")

    def _nearest_node(self, lat: float, lon: float) -> int:
        """將 WGS84 (lat, lon) 轉為投影座標，再查找最近路網節點。"""
        x_utm, y_utm = self._to_proj.transform(lon, lat)   # lon→X, lat→Y
        return ox.distance.nearest_nodes(self.G_proj, X=x_utm, Y=y_utm)

    def calculate_shortest_path(
        self,
        start: tuple[float, float],
        end:   tuple[float, float],
    ) -> list[tuple[float, float]]:
        """
        真實道路最短路徑（Dijkstra on 投影圖）。

        Args:
            start: (lat, lon)
            end:   (lat, lon)
        Returns:
            [(lat, lon), ...] 路網節點座標序列（已沿街道，不穿建築）
        """
        orig = self._nearest_node(start[0], start[1])
        dest = self._nearest_node(end[0],   end[1])

        route_nodes = nx.shortest_path(self.G_proj, orig, dest, weight='length')

        # 從原始圖讀取 lat/lon（G_proj 節點存的是 UTM x/y）
        return [
            (self.G.nodes[n]['y'], self.G.nodes[n]['x'])
            for n in route_nodes
        ]

    def interpolate_path(
        self,
        path: list[tuple[float, float]],
        step_meters: float = 0.75,
    ) -> list[dict]:
        """
        在路網節點之間線性補點，附帶速度後設資料。

        速度規則：
          - 直線段：均勻隨機 [5, 8] km/h
          - 轉彎（夾角 > 30°）：降至 2 km/h
        Returns:
            [{'lat', 'lon', 'speed_kmh', 'is_turning'}, ...]
        """
        if len(path) < 2:
            return [{'lat': path[0][0], 'lon': path[0][1],
                     'speed_kmh': 4.0, 'is_turning': False}] if path else []

        bearings = [_bearing_deg(path[i], path[i+1]) for i in range(len(path)-1)]
        result: list[dict] = []

        for seg_idx in range(len(path) - 1):
            p1, p2 = path[seg_idx], path[seg_idx + 1]
            dist = geodesic(p1, p2).meters
            n_steps = max(1, int(dist / step_meters))

            turning = seg_idx > 0 and _turn_angle(bearings[seg_idx-1], bearings[seg_idx]) > _TURN_THRESHOLD
            speed = _SPEED_TURNING if turning else float(np.random.uniform(_SPEED_STRAIGHT_MIN, _SPEED_STRAIGHT_MAX))

            lats = np.linspace(p1[0], p2[0], n_steps, endpoint=False)
            lons = np.linspace(p1[1], p2[1], n_steps, endpoint=False)

            for lat, lon in zip(lats, lons):
                result.append({
                    'lat':        float(lat + np.random.normal(0, 8e-8)),
                    'lon':        float(lon + np.random.normal(0, 8e-8)),
                    'speed_kmh':  round(speed, 2),
                    'is_turning': turning,
                })

        last = path[-1]
        result.append({'lat': float(last[0]), 'lon': float(last[1]),
                       'speed_kmh': _SPEED_TURNING, 'is_turning': False})
        return result


# ─── TSP 最佳化 ──────────────────────────────────────────────────────────────
def optimize_tsp_route(
    poi_list: list[dict],
    planner: 'PathPlanner | None' = None,
) -> list[dict]:
    """
    計算 POI 巡迴的最短路徑排列。

    Args:
        poi_list: 含 lat/lon 的 POI 列表（≥ 2 筆）
        planner:  PathPlanner 實例；提供時以路網距離建立距離矩陣，
                  否則 fallback 至 geodesic 直線距離。
    Returns:
        重排後的 poi_list（TSP 近似最短巡迴，開放路徑不回頭）
    """
    n = len(poi_list)
    if n < 2:
        return poi_list

    G_tsp = nx.Graph()

    if planner is not None:
        # ── 路網距離矩陣（對角線遍歷） ───────────────────────────────────
        # 預先查找所有 POI 的最近路網節點（投影座標，快速 KD-tree）
        osm_nodes = [
            planner._nearest_node(poi['lat'], poi['lon'])
            for poi in poi_list
        ]

        for i in range(n):
            for j in range(i + 1, n):
                try:
                    dist = nx.shortest_path_length(
                        planner.G_proj, osm_nodes[i], osm_nodes[j], weight='length'
                    )
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    # 無連通：用 geodesic × 1.5 懲罰係數
                    p_i = (poi_list[i]['lat'], poi_list[i]['lon'])
                    p_j = (poi_list[j]['lat'], poi_list[j]['lon'])
                    dist = geodesic(p_i, p_j).meters * 1.5
                G_tsp.add_edge(i, j, weight=dist)
    else:
        # ── Geodesic 備援 ─────────────────────────────────────────────────
        for i in range(n):
            for j in range(i + 1, n):
                p1 = (poi_list[i]['lat'], poi_list[i]['lon'])
                p2 = (poi_list[j]['lat'], poi_list[j]['lon'])
                G_tsp.add_edge(i, j, weight=geodesic(p1, p2).meters)

    # ── NetworkX Christofides-greedy TSP ─────────────────────────────────
    try:
        tsp_nodes = nx.approximation.traveling_salesman_problem(
            G_tsp, weight='weight', cycle=False
        )
    except Exception:
        tsp_nodes = _greedy_fallback(poi_list)
        return [poi_list[i] for i in tsp_nodes]

    seen: list[int] = []
    for idx in tsp_nodes:
        if idx not in seen:
            seen.append(idx)
    return [poi_list[i] for i in seen]


def _greedy_fallback(poi_list: list[dict]) -> list[int]:
    """逐步選最近未訪節點（O(n²) 備援）。"""
    unvisited = list(range(len(poi_list)))
    order = [unvisited.pop(0)]
    while unvisited:
        curr = order[-1]
        p = (poi_list[curr]['lat'], poi_list[curr]['lon'])
        nxt = min(unvisited, key=lambda j: geodesic(p, (poi_list[j]['lat'], poi_list[j]['lon'])).meters)
        unvisited.remove(nxt)
        order.append(nxt)
    return order