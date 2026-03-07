# -*- coding: utf-8 -*-
import osmnx as ox
import networkx as nx
from geopy.distance import geodesic
import folium
from poi_manager import render_pois_on_map

def get_graph_for_route(origin: tuple[float, float], destination: tuple[float, float], padding_m: int = 500):
    """
    下載 OSM 地圖資料，範圍包含 origin 與 destination 加上 padding_m 的緩衝距離。
    origin: (lat, lon)
    destination: (lat, lon)
    """
    # 計算中點
    mid_lat = (origin[0] + destination[0]) / 2.0
    mid_lon = (origin[1] + destination[1]) / 2.0
    
    # 計算起終點距離，以此決定半徑
    distance_m = geodesic(origin, destination).meters
    radius = (distance_m / 2.0) + padding_m
    
    print(f"[MapEngine] 正在下載 OSM 路網資料 (中心點: {mid_lat:.5f}, {mid_lon:.5f}, 半徑: {radius:.1f}m)...")
    
    # 皮克敏遊戲適合使用步行路徑 (walk network)
    G = ox.graph_from_point((mid_lat, mid_lon), dist=radius, network_type='walk')
    
    # 加入邊線的速度屬性與估計時間（非必須，但對擴展有用）
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)
    
    print(f"[MapEngine] 圖論模型建立完成: 節點數 {len(G.nodes)}, 邊數 {len(G.edges)}")
    return G

def render_map(center_lat, center_lon, display_pois, selected_pois, road_polyline=None, current_loc=None, current_speed=0.0):
    """建立並渲染黑色主題的地圖，包含 POI、路徑與當前裝置位置"""
    m = folium.Map(location=[center_lat, center_lon], zoom_start=15, tiles="CartoDB dark_matter")

    selected_ids = {p['id'] for p in selected_pois}
    render_pois_on_map(m, display_pois, selected_ids=selected_ids)

    # 繪製路徑
    if road_polyline:
        folium.PolyLine(
            [[lat, lon] for lat, lon in road_polyline],
            color="#0055ff", weight=4, opacity=0.88,
            tooltip="🗺️ TSP 路網巡迴路徑",
        ).add_to(m)
    elif len(selected_pois) > 1:
        folium.PolyLine(
            [[p['lat'], p['lon']] for p in selected_pois],
            color="orange", weight=2, opacity=0.45, dash_array="8",
            tooltip="直線示意（請先計算 TSP）",
        ).add_to(m)

    # 裝置位置
    if current_loc:
        folium.Marker(
            location=[current_loc[0], current_loc[1]],
            popup=f"📱 iPhone\n({current_loc[0]:.6f}, {current_loc[1]:.6f})\n時速 {current_speed:.1f} km/h",
            tooltip=f"📱 當前位置 {current_speed:.1f} km/h",
            icon=folium.Icon(color='darkblue', icon='phone', prefix='fa'),
        ).add_to(m)

    return m
