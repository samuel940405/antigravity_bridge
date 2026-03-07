# -*- coding: utf-8 -*-
"""
nav_brain.py — V2 導航大腦（最終重構版）
=========================================
核心算法：線性插值 P_new = P1 + t(P2 - P1)，步長 0.75m
路由策略：OSMnx Dijkstra
TSP：NetworkX Christofides 近似
"""
import math
import numpy as np
import networkx as nx
import osmnx as ox
from geopy.distance import geodesic
from typing import Optional, Union

# ─── OSMnx 全局設定 ───────────────────────────────────────────────────
ox.settings.use_cache   = True    # 啟用幾何/Overpass 結果快取
ox.settings.log_console = False   # 選擇性關閉 console log

# ─── 常數 ───────────────────────────────────────────────────────────────────────
DEFAULT_STEP_M  = 4.0
_TURN_DEG       = 35.0
_SPD_MIN        = 4.0
_SPD_MAX        = 7.0
_SPD_TURN       = 2.0
_JITTER_M       = 0.04
DEFAULT_RADIUS  = 2000  # OSMnx 路網下載半徑（公尺）


# ─── 雙向相容取值 ─────────────────────────────────────────────────────────────
def _lat(poi: Union[dict, list, tuple]) -> float:
    if isinstance(poi, dict):
        return float(poi.get('lat', poi.get('y', 0.0)))
    return float(poi[0])

def _lng(poi: Union[dict, list, tuple]) -> float:
    if isinstance(poi, dict):
        return float(poi.get('lon', poi.get('lng', poi.get('x', 0.0))))
    return float(poi[1])


# ─── 幾何工具 ─────────────────────────────────────────────────────────────────
def _haversine_m(p1: tuple, p2: tuple) -> float:
    """快速 Haversine 距離（公尺）。p1/p2 可為 (lat,lon) 或 dict。"""
    la1, lo1 = _lat(p1), _lng(p1)
    la2, lo2 = _lat(p2), _lng(p2)
    R = 6_371_000
    f1, f2 = math.radians(la1), math.radians(la2)
    df = math.radians(la2 - la1)
    dl = math.radians(lo2 - lo1)
    a  = math.sin(df/2)**2 + math.cos(f1)*math.cos(f2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(max(0, a)))

def _bearing(p1: tuple, p2: tuple) -> float:
    """方位角（度，正北=0，順時針）。"""
    la1, lo1 = math.radians(_lat(p1)), math.radians(_lng(p1))
    la2, lo2 = math.radians(_lat(p2)), math.radians(_lng(p2))
    dl = lo2 - lo1
    x  = math.sin(dl)*math.cos(la2)
    y  = math.cos(la1)*math.sin(la2) - math.sin(la1)*math.cos(la2)*math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def _turn_angle(b1: float, b2: float) -> float:
    d = abs(b2 - b1)
    return min(d, 360 - d)


# ─── 核心函式：interpolate_path ───────────────────────────────────────────────
def interpolate_path(
    path: list,
    step_m: float = DEFAULT_STEP_M,
) -> list[dict]:
    """
    線性插值：P_new = P1 + t * (P2 - P1)，t ∈ [0, 1)，步長 step_m。

    每兩個路網節點之間：
      n     = max(1, ceil(dist / step_m))
      t_k   = k / n，k = 0, 1, ..., n-1
      lat_k = lat1 + t_k * (lat2 - lat1)
      lon_k = lon1 + t_k * (lon2 - lon1)

    附帶速度 metadata：
      直線段：uniformly random [_SPD_MIN, _SPD_MAX] km/h
      轉彎段：固定 _SPD_TURN km/h

    Returns:
      [{'lat', 'lon', 'speed_kmh', 'step_meters', 'is_turning'}, ...]
    """
    if not path:
        return []
    if len(path) < 2:
        return [{'lat': _lat(path[0]), 'lon': _lng(path[0]),
                 'speed_kmh': 4.0, 'step_meters': step_m, 'is_turning': False}]

    # ── 預計算方位角 ───────────────────────────────────────────────────────
    bearings = [_bearing(path[i], path[i+1]) for i in range(len(path)-1)]

    # ── 緯度抖動換算 ────────────────────────────────────────────────────────
    j_lat_deg = _JITTER_M / 111_320

    result: list[dict] = []

    for seg in range(len(path) - 1):
        p1, p2 = path[seg], path[seg+1]
        lat1, lon1 = _lat(p1), _lng(p1)
        lat2, lon2 = _lat(p2), _lng(p2)
        dist   = _haversine_m(p1, p2)
        n      = max(1, math.ceil(dist / step_m))
        actual = dist / n  # 每步實際距離

        turning = (
            seg > 0 and
            _turn_angle(bearings[seg - 1], bearings[seg]) > _TURN_DEG
        )
        speed = (_SPD_TURN if turning
                 else float(np.random.uniform(_SPD_MIN, _SPD_MAX)))

        j_lon_deg = _JITTER_M / (111_320 * max(math.cos(math.radians(lat1)), 1e-9))

        for k in range(n):
            t = k / n           # t ∈ [0, 1)
            lat = lat1 + t * (lat2 - lat1)
            lon = lon1 + t * (lon2 - lon1)
            result.append({
                'lat':         lat + np.random.normal(0, j_lat_deg),
                'lon':         lon + np.random.normal(0, j_lon_deg),
                'speed_kmh':   round(speed, 2),
                'step_meters': round(actual, 3),
                'is_turning':  turning,
            })

    # 終點
    last = path[-1]
    result.append({
        'lat':         _lat(last),
        'lon':         _lng(last),
        'speed_kmh':   _SPD_TURN,
        'step_meters': step_m,
        'is_turning':  False,
    })
    return result


def smooth_path(
    path: list,
    step_m: float = DEFAULT_STEP_M,
    closed: bool = True,
) -> list[dict]:
    """
    封閉路徑版插值（循環平滑）。

    將 path 自動閉合：在最後一個點之後附加起點，
    形成 path[0] → path[1] → ... → path[-1] → path[0] 的環形路徑，
    再以 0.75m 步長執行線性插值，確保 GPX 檔案絲滑無斷點。

    Args:
        path:   路網節點序列 [(lat,lon),...] 或 [dict,...]，至少 2 個點
        step_m: 插值步長（公尺），預設 0.75
        closed: True = 自動封閉環路（終點→起點連接段也插值）

    Returns:
        [{'lat','lon','speed_kmh','step_meters','is_turning'}, ...]
    """
    if not path or len(path) < 2:
        return interpolate_path(path, step_m=step_m)

    if closed:
        # 封閉路徑：複製起點加到最後，形成完整環
        closed_path = list(path) + [path[0]]
    else:
        closed_path = list(path)

    return interpolate_path(closed_path, step_m=step_m)




# ─── RoutePlanner ─────────────────────────────────────────────────────────────
class RoutePlanner:
    """
    統一路由介面：BRouter 優先，OSMnx 備援。
    支援任意格式 waypoints（dict 或 tuple）。
    """

    def __init__(self):
        self.G: Optional[nx.MultiDiGraph]      = None
        self.G_proj: Optional[nx.MultiDiGraph] = None
        self._to_proj = None

    def load_graph(self, center_lat: float, center_lon: float, radius_m: int = DEFAULT_RADIUS):
        """下載 OSMnx 步行路網（2 km 半徑，啟用快取）。"""
        print(f"[RoutePlanner] 下載 OSMnx 步行路網 r={radius_m}m（快取模式）...")
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
        """
        純 OSMnx Dijkstra 路由，逐段拼接。
        waypoints: list of (lat,lon) or dict.
        """
        if len(waypoints) < 2:
            return [(_lat(w), _lng(w)) for w in waypoints]
        if self.G is None:
            raise RuntimeError("路網未載入，請先呼叫 load_graph()")

        full: list[tuple] = []
        for i in range(len(waypoints) - 1):
            seg = self._osmnx_segment(waypoints[i], waypoints[i+1])
            if i > 0 and full and seg:
                seg = seg[1:]   # 去除重複的接縫節點
            full.extend(seg)
        return full


# ─── TSP ─────────────────────────────────────────────────────────────────────
def optimize_tsp(
    poi_list: list,
    planner: Optional[RoutePlanner] = None,
) -> list:
    """
    Christofides TSP 近似（NetworkX）。
    相容 dict 或 tuple POI。
    """
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
