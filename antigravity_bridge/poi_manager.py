# -*- coding: utf-8 -*-
"""
poi_manager.py — Overpass/OSMnx POI 管理器
功能：
  1. fetch_and_save_pois() — 以 osmnx 從 Overpass 取得 POI，儲存為 pois.json（utf-8）
  2. load_pois()           — 從 pois.json 讀取快取
  3. render_pois_on_map()  — 將 POI 列表一鍵標註於 folium.Map 物件
"""
import json
import hashlib
import math
import folium
import osmnx as ox

# ─── 常數 ────────────────────────────────────────────────────────────────────
_DEFAULT_CENTER = (25.0339, 121.5644)   # 台北市政府附近
_DEFAULT_RADIUS = 2000                  # 公尺
_DEFAULT_FILE   = 'pois.json'

# ─── 取得並儲存 POI ───────────────────────────────────────────────────────────
def fetch_and_save_pois(
    center_point: tuple[float, float] = _DEFAULT_CENTER,
    radius: int = _DEFAULT_RADIUS,
    output_file: str = _DEFAULT_FILE,
    max_count: int = 20,
) -> list[dict]:
    """
    透過 Overpass（osmnx.features_from_point）取得景點等 POI。
    去重、依距中心點遠近排序，只保留前 max_count 個高品質點位。
    """
    print(f"[POIManager] 正在獲取以 {center_point} 為圓心、方圓 {radius}m 的重點 POI...")
    tags = {
        'historic': True,
        'tourism': ['attraction', 'artwork', 'museum', 'gallery'],
        'amenity': ['arts_centre', 'library', 'theatre', 'community_centre'],
        'leisure': ['park', 'garden', 'nature_reserve'],
    }

    try:
        gdf = ox.features_from_point(center_point, tags=tags, dist=radius)
        if gdf.empty:
            print("[POIManager] 查詢範圍內未找到符合條件的 POI。")
            return []

        poi_list: list[dict] = []
        seen_hashes: set[str] = set()

        for _, row in gdf.iterrows():
            try:
                geom_center = row.geometry.centroid
                lat, lon = float(geom_center.y), float(geom_center.x)
            except Exception:
                continue  # 幾何資料異常直接略過

            # 取名稱 & 分類標籤
            name: str = row.get('name', None)
            category: str = 'unknown'
            for cat_key in ('historic', 'tourism', 'amenity', 'leisure'):
                val = row.get(cat_key, None)
                if isinstance(val, (str, bool)) and val not in (False, None):
                    category = cat_key
                    if not isinstance(name, str):
                        name = val if isinstance(val, str) else cat_key
                    break
            if not isinstance(name, str):
                name = '未命名地標'

            # MD5 去重（依四捨五入至 5 位數的座標）
            hash_str = hashlib.md5(f"{lat:.5f},{lon:.5f}".encode('utf-8')).hexdigest()
            if hash_str not in seen_hashes:
                seen_hashes.add(hash_str)
                poi_list.append({
                    "id":       hash_str,
                    "name":     name,
                    "lat":      lat,
                    "lon":      lon,
                    "category": category,
                })

        # 依距中心點距離排序，取最近 max_count 個
        def _dist_to_center(p):
            dlat = p['lat'] - center_point[0]
            dlon = (p['lon'] - center_point[1]) * math.cos(math.radians(center_point[0]))
            return math.sqrt(dlat**2 + dlon**2)

        poi_list.sort(key=_dist_to_center)
        poi_list = poi_list[:max_count]

        # 強制 utf-8 寫入
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(poi_list, f, ensure_ascii=False, indent=4)

        print(f"[POIManager] 成功截取並儲存 {len(poi_list)} 筆資料 → {output_file}")
        return poi_list

    except Exception as e:
        print(f"[POIManager] 截取 POI 失敗: {type(e).__name__}: {e}")
        return []


# ─── 讀取快取 ────────────────────────────────────────────────────────────────
def load_pois(input_file: str = _DEFAULT_FILE) -> list[dict]:
    """從快取 JSON 讀取 POI，找不到檔案時回傳空列表。"""
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"[POIManager] 從快取載入 {len(data)} 筆 POI。")
        return data
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as e:
        print(f"[POIManager] 讀取快取失敗: {e}")
        return []


# ─── Folium 地圖整合 ─────────────────────────────────────────────────────────
def render_pois_on_map(
    m: folium.Map,
    pois: list[dict],
    selected_ids: set[str] | None = None,
    selected_color: str = 'red',
    default_color: str = 'blue',
    icon_name: str = 'info-sign',
) -> folium.Map:
    """
    一鍵將 POI 列表標註於既有 folium.Map 物件。

    Args:
        m:              folium.Map 目標地圖
        pois:           fetch_and_save_pois() / load_pois() 傳回的清單
        selected_ids:   已選取的 POI id 集合（顯示為 selected_color）
        selected_color: 已選取 Marker 顏色（預設紅色）
        default_color:  未選取 Marker 顏色（預設藍色）
        icon_name:      folium Icon 圖示名稱

    Returns:
        修改後的 folium.Map（同一物件，方便方法鏈）
    """
    if selected_ids is None:
        selected_ids = set()

    for poi in pois:
        is_selected = poi['id'] in selected_ids
        color = selected_color if is_selected else default_color
        status_hint = "✅ 已加入巡迴" if is_selected else "點擊地圖 Marker 加入巡迴"

        folium.Marker(
            location=[poi['lat'], poi['lon']],
            popup=folium.Popup(
                f"<b>{poi['name']}</b><br/>"
                f"({poi['lat']:.5f}, {poi['lon']:.5f})<br/>"
                f"{status_hint}",
                max_width=220
            ),
            tooltip=f"{'⭐ ' if is_selected else ''}{poi['name']}",
            icon=folium.Icon(color=color, icon=icon_name),
        ).add_to(m)

    return m


if __name__ == '__main__':
    pois = fetch_and_save_pois()
    print(f"共取得 {len(pois)} 筆 POI，範例：{pois[:2] if pois else '（無）'}")
