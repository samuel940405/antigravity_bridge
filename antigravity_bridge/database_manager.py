# -*- coding: utf-8 -*-
"""
database_manager.py — SQLite POI 資料庫管理器
===============================================
資料表：pois
欄位：id (TEXT), name (TEXT), lat (REAL), lng (REAL), type (TEXT)

公開 API：
  init_db()              — 建立表格（若不存在）
  save_pois(poi_list)    — 批量寫入（UPSERT，自動雙向相容 dict/list 格式）
  query_pois(lat, lng)   — 查詢以 (lat, lng) 為圓心 2km 內的 POI
  load_all_pois()        — 讀出全部 POI，轉為標準 dict 格式
"""
import math
import sqlite3
import pathlib
from typing import Union

# ─── 資料庫路徑 ──────────────────────────────────────────────────────────────
DB_PATH = pathlib.Path(__file__).parent / "pois.db"

# ─── Schema ──────────────────────────────────────────────────────────────────
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pois (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lat  REAL NOT NULL,
    lng  REAL NOT NULL,
    type TEXT DEFAULT 'unknown'
);
"""

# ─── 雙向相容取值輔助 ─────────────────────────────────────────────────────────
def _get_lat(poi: Union[dict, list, tuple]) -> float:
    """相容 dict {'lat':...} 或 list/tuple [lat, lon]。"""
    if isinstance(poi, dict):
        return float(poi.get('lat', poi.get('y', 0.0)))
    return float(poi[0])

def _get_lng(poi: Union[dict, list, tuple]) -> float:
    """相容 dict {'lon'/'lng':...} 或 list/tuple [lat, lon]。"""
    if isinstance(poi, dict):
        return float(poi.get('lon', poi.get('lng', poi.get('x', 0.0))))
    return float(poi[1])

def _get_str(poi: Union[dict, list, tuple], key: str, default: str = '') -> str:
    if isinstance(poi, dict):
        return str(poi.get(key, default))
    return default


# ─── 公開 API ────────────────────────────────────────────────────────────────
def init_db(db_path: pathlib.Path = DB_PATH) -> None:
    """建立 pois 表格（冪等）。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(_CREATE_TABLE)
        conn.commit()
    print(f"[DB] 資料庫就緒：{db_path}")


def save_pois(
    poi_list: list,
    db_path: pathlib.Path = DB_PATH,
) -> int:
    """
    批量 UPSERT POI。
    相容格式：
      - dict  {'id', 'name', 'lat', 'lon'/'lng', 'category'/'type'}
      - list  [lat, lon]（自動產生 id）

    Returns:
        實際寫入筆數
    """
    import hashlib
    init_db(db_path)
    rows = []
    for poi in poi_list:
        lat = _get_lat(poi)
        lng = _get_lng(poi)
        if isinstance(poi, dict):
            poi_id   = str(poi.get('id',
                hashlib.md5(f"{lat:.5f},{lng:.5f}".encode()).hexdigest()))
            name     = str(poi.get('name', '未命名'))
            poi_type = str(poi.get('type', poi.get('category', 'unknown')))
        else:
            poi_id   = hashlib.md5(f"{lat:.5f},{lng:.5f}".encode()).hexdigest()
            name     = '未命名'
            poi_type = 'unknown'
        rows.append((poi_id, name, lat, lng, poi_type))

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO pois (id, name, lat, lng, type) VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
    print(f"[DB] UPSERT {len(rows)} 筆 POI")
    return len(rows)


def query_pois(
    lat: float,
    lng: float,
    radius_m: float = 2000.0,
    db_path: pathlib.Path = DB_PATH,
) -> list[dict]:
    """
    查詢以 (lat, lng) 為圓心、radius_m 公尺以內的 POI。
    使用快速矩形預篩 + Haversine 精確篩選。

    Returns:
        [{'id', 'name', 'lat', 'lng', 'type'}, ...] 按距離排序
    """
    init_db(db_path)
    # 矩形預篩（deg_per_m）
    dlat = radius_m / 111_320
    dlng = radius_m / (111_320 * max(math.cos(math.radians(lat)), 1e-9))

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, name, lat, lng, type FROM pois
               WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?""",
            (lat - dlat, lat + dlat, lng - dlng, lng + dlng),
        ).fetchall()

    def _haversine(a_lat, a_lng, b_lat, b_lng) -> float:
        R = 6_371_000
        phi1, phi2 = math.radians(a_lat), math.radians(b_lat)
        dphi = math.radians(b_lat - a_lat)
        dlambda = math.radians(b_lng - a_lng)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        return 2*R*math.asin(math.sqrt(a))

    result = []
    for r in rows:
        d = _haversine(lat, lng, r['lat'], r['lng'])
        if d <= radius_m:
            result.append({
                'id':       r['id'],
                'name':     r['name'],
                'lat':      r['lat'],
                'lon':      r['lng'],   # 保持與 poi_manager 一致的 'lon' key
                'lng':      r['lng'],
                'type':     r['type'],
                'category': r['type'],  # 相容舊 'category' key
                '_dist_m':  round(d, 1),
            })

    result.sort(key=lambda x: x['_dist_m'])
    return result


def load_all_pois(db_path: pathlib.Path = DB_PATH) -> list[dict]:
    """讀出全部 POI（標準 dict 格式）。"""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, name, lat, lng, type FROM pois").fetchall()
    return [
        {'id': r['id'], 'name': r['name'],
         'lat': r['lat'], 'lon': r['lng'], 'lng': r['lng'],
         'type': r['type'], 'category': r['type']}
        for r in rows
    ]
