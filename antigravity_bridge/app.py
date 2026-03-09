# -*- coding: utf-8 -*-
"""
app.py — Antigravity 導航大腦 最終版
======================================
• 全黑專業主題（無直接 pymobiledevice3 導入）
• 一鍵同步按鈕：計算路徑 → 1.2m 平滑 → 存檔 → RPA 注入
• 雙向相容 POI 格式（dict / list / tuple）
"""
import asyncio
import sys
import threading
import pathlib
import random

import folium
import streamlit as st
from streamlit_folium import st_folium

# ── 安全導入：bridge 內部已有 LIB_AVAILABLE 靜態防護 ─────────────────────────
from bridge import LocationBridge

# ── 功能模組 ─────────────────────────────────────────────────────────────────
from nav_brain    import RoutePlanner, optimize_tsp, interpolate_path, smooth_path, _lat, _lng, DEFAULT_RADIUS
from poi_manager  import fetch_and_save_pois, load_pois, render_pois_on_map

# ─── 系統設定 ────────────────────────────────────────────────────────────────
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ─── 頁面設定 ────────────────────────────────────────────────────────────────
st.set_page_config(
    layout="wide",
    page_title="Antigravity 導航大腦",
    page_icon="🛸",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════════════
#   全黑專業主題 CSS
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
/* ─── 全域背景 ─── */
[data-testid="stAppViewContainer"],
[data-testid="stApp"],
.main { background: #0b0b0f !important; }

/* ─── 側欄 ─── */
[data-testid="stSidebar"] > div:first-child {
    background: #10101a !important;
    border-right: 1px solid #1f1f2e !important;
}

/* ─── 文字 ─── */
html, body, [class*="css"], p, span, label, div {
    color: #d4d4e0 !important;
    font-family: 'Inter', 'SF Pro Display', 'Segoe UI', sans-serif !important;
}
h1 { font-size: 1.9rem !important; font-weight: 800 !important;
     color: #ffffff !important; letter-spacing: -1px; }
h2, h3 { color: #c0c0d8 !important; font-weight: 700 !important; }

/* ─── 主操作按鈕（🚀 一鍵同步） ─── */
div[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #6d28d9 0%, #2563eb 100%) !important;
    color: #fff !important;
    border: none !important;
    border-radius: 12px !important;
    font-size: 1.08rem !important;
    font-weight: 800 !important;
    letter-spacing: 0.4px !important;
    padding: 0.75rem 1.5rem !important;
    box-shadow: 0 6px 24px rgba(109,40,217,.40) !important;
    transition: transform 0.15s ease, opacity 0.15s ease !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    transform: translateY(-2px) !important;
    opacity: 0.92 !important;
}
div[data-testid="stButton"] > button[kind="primary"]:disabled {
    background: #1f1f2e !important;
    color: #444 !important;
    box-shadow: none !important;
}

/* ─── 普通按鈕 ─── */
div[data-testid="stButton"] > button {
    background: #181826 !important;
    color: #b0b0c8 !important;
    border: 1px solid #2a2a40 !important;
    border-radius: 9px !important;
}
div[data-testid="stButton"] > button:hover {
    background: #1e1e30 !important;
    border-color: #4040a0 !important;
}

/* ─── 輸入框 ─── */
.stTextInput input, .stNumberInput input {
    background: #13131f !important;
    color: #e0e0f0 !important;
    border: 1px solid #2a2a40 !important;
    border-radius: 8px !important;
}

/* ─── Slider ─── */
.stSlider [class*="thumb"] { background: #7c3aed !important; }
.stSlider [class*="track"] { background: #2a2a40 !important; }

/* ─── Metric ─── */
[data-testid="stMetricValue"] { color: #a78bfa !important; font-weight: 800; font-size: 1.3rem; }
[data-testid="stMetricLabel"] { color: #5a5a7a !important; font-size: 0.75rem; }
[data-testid="stMetricDelta"] { color: #6ee7b7 !important; }

/* ─── 進度條 ─── */
.stProgress > div > div { background: linear-gradient(90deg,#7c3aed,#3b82f6) !important; }

/* ─── 분리선、expander ─── */
hr { border-color: #1f1f2e !important; }
.stExpander > summary { color: #888 !important; }

/* ─── 下載按鈕 ─── */
.stDownloadButton > button {
    background: #0f2318 !important;
    color: #6ee47a !important;
    border: 1px solid #1a4028 !important;
    border-radius: 9px !important;
}

/* ─── Notification / Toast ─── */
.stAlert { background: #111120 !important; border-radius: 10px !important; border: 1px solid #2a2a40 !important; }

/* ─── 浮動標題卡 ─── */
.stat-card {
    background: #12121e;
    border: 1px solid #1f1f30;
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 8px;
}
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
#   工具函式
# ═══════════════════════════════════════════════════════════════════════════════
def _parse_coord(text: str, default=(25.03390, 121.56440)):
    try:
        parts = [p.strip() for p in text.replace('，', ',').split(',')]
        if len(parts) == 2:
            return float(parts[0]), float(parts[1])
    except Exception:
        pass
    return default


def _poi_compat(poi) -> dict:
    """確保 POI 一律以 dict 格式存取，防止 KeyError。"""
    if isinstance(poi, dict):
        lat = float(poi.get('lat', poi.get('y', 0.0)))
        lon = float(poi.get('lon', poi.get('lng', poi.get('x', 0.0))))
        return {
            'id':       poi.get('id', f"{lat:.5f},{lon:.5f}"),
            'name':     poi.get('name', '未命名'),
            'lat':      lat,
            'lon':      lon,
            'category': poi.get('category', poi.get('type', 'unknown')),
        }
    # list/tuple [lat, lon]
    lat, lon = float(poi[0]), float(poi[1])
    return {'id': f"{lat:.5f},{lon:.5f}", 'name': '未命名',
            'lat': lat, 'lon': lon, 'category': 'unknown'}


# ═══════════════════════════════════════════════════════════════════════════════
#   Session State 初始化（@cache_resource 確保全局只建一次）
# ═══════════════════════════════════════════════════════════════════════════════
@st.cache_resource
def _init_resources():
    loop = asyncio.new_event_loop()
    threading.Thread(
        target=lambda lp: (asyncio.set_event_loop(lp), lp.run_forever()),
        args=(loop,), daemon=True,
    ).start()
    bridge = LocationBridge()
    bridge.loop = loop
    asyncio.run_coroutine_threadsafe(bridge.start_services(), loop)
    
    # In V8.1.1 Ultimate Edition, connection is established on-the-fly during injection.
    connected = True
    return bridge, loop, connected


bridge, bg_loop, _init_connected = _init_resources()

_DEFAULTS = {
    'connected':      _init_connected,
    'pois':           [_poi_compat(p) for p in load_pois()],
    'selected_pois':  [],
    'smooth_path':    [],
    'road_polyline':  [],
    'center_text':    "25.03390, 121.56440",
    'planner':        None,
    'is_computing':   False,   # 防止重複觸發 pipeline
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ═══════════════════════════════════════════════════════════════════════════════
#   核心計算（executor 中執行）
# ═══════════════════════════════════════════════════════════════════════════════
def _full_pipeline(selected_pois, center_lat, center_lon, step_m=1.2):
    """
    完整流程：TSP 排序 → OSMnx 路由 → 4.0m 循環插值。
    在 ThreadPoolExecutor 中非同步執行。
    Returns: (smooth_path, road_nodes, planner)
    """
    planner = st.session_state.planner
    if planner is None:
        planner = RoutePlanner()
        planner.load_graph(center_lat, center_lon, radius_m=DEFAULT_RADIUS)

    # 1. TSP
    tsp_ordered = optimize_tsp(selected_pois, planner=planner)

    # 2. OSMnx Dijkstra 路由
    waypoints  = [(_lat(p), _lng(p)) for p in tsp_ordered]
    road_nodes = planner.route(waypoints)

    # 3. 4.0m 封閉循環插值
    smooth = smooth_path(road_nodes, step_m=step_m, closed=True)
    return smooth, road_nodes, planner


def _generate_gpx_xml(loops=100, use_jitter=True):
    """將座標陣列轉換為 GeoPort 可用的帶抖動無限循環 GPX XML。"""
    import xml.etree.ElementTree as ET
    from xml.dom import minidom
    from datetime import datetime, timedelta, timezone
    import random

    # 動態獲取最新路徑
    points_list = st.session_state.smooth_path
    if not points_list:
        return ""

    gpx = ET.Element("gpx", version="1.1", creator="Antigravity Factory")
    trk = ET.SubElement(gpx, "trk")
    ET.SubElement(trk, "name").text = f"Antigravity x{loops}"
    trkseg = ET.SubElement(trk, "trkseg")

    # Base time: Now, in UTC
    current_time = datetime.now(timezone.utc)

    for _ in range(loops):
        for pt in points_list:
            lat = float(pt.get('lat', 0.0))
            lon = float(pt.get('lon', 0.0))
            if use_jitter:
                lat += random.uniform(-0.000005, 0.000005)
                lon += random.uniform(-0.000005, 0.000005)

            trkpt = ET.SubElement(trkseg, "trkpt", lat=f"{lat:.6f}", lon=f"{lon:.6f}")
            # Add strict 1-second intervals to prevent GeoPort buffer overflow
            ET.SubElement(trkpt, "time").text = current_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            current_time += timedelta(seconds=1)

    xml_str = ET.tostring(gpx, encoding='utf-8')
    return minidom.parseString(xml_str).toprettyxml(indent="  ")


# ═══════════════════════════════════════════════════════════════════════════════
#   地圖渲染
# ═══════════════════════════════════════════════════════════════════════════════
def _render_map(center_lat, center_lon):
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=15,
        tiles="CartoDB dark_matter",
    )
    sel_ids = {p['id'] for p in st.session_state.selected_pois}
    render_pois_on_map(m, st.session_state.pois, selected_ids=sel_ids)

    if st.session_state.road_polyline:
        folium.PolyLine(
            [[n[0], n[1]] if isinstance(n, (list, tuple)) else [n['lat'], n['lon']]
             for n in st.session_state.road_polyline],
            color="#8b5cf6", weight=4.5, opacity=0.88,
            tooltip="🛣️ TSP 最佳路徑（4.0m 插值）",
        ).add_to(m)
    elif len(st.session_state.selected_pois) > 1:
        folium.PolyLine(
            [[_lat(p), _lng(p)] for p in st.session_state.selected_pois],
            color="#334155", weight=2, opacity=0.5, dash_array="6",
            tooltip="直線示意，請先計算路徑",
        ).add_to(m)

    if bridge.last_location:
        lat, lon = bridge.last_location
        folium.Marker(
            location=[lat, lon],
            popup=f"📱 {lat:.6f}, {lon:.6f}<br>{bridge.current_speed_kmh:.1f} km/h",
            tooltip=f"📱 {bridge.current_speed_kmh:.1f} km/h",
            icon=folium.Icon(color='purple', icon='phone', prefix='fa'),
        ).add_to(m)
    return m


# ═══════════════════════════════════════════════════════════════════════════════
#   UI 佈局
# ═══════════════════════════════════════════════════════════════════════════════
# ── 頂部標題列 ──────────────────────────────────────────────────────────────
col_title, col_status = st.columns([4, 1])
with col_title:
    st.markdown(
        "<h1>🛸 Antigravity &nbsp;"
        "<span style='font-size:.5em;color:#5a5a7a;font-weight:400'>"
        "導航大腦 · 最終版</span></h1>",
        unsafe_allow_html=True,
    )
with col_status:
    dot  = "🟢" if st.session_state.connected else "🔴"
    stat = "連線中" if st.session_state.connected else "未連接"
    st.metric("裝置", f"{dot} {stat}")

st.divider()

# ── 側欄 ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ 控制台")

    # 連線資訊

    c1, c2, c3 = st.columns(3)
    c1.metric("佇列", f"{bridge.queue.qsize()}")
    c2.metric("心跳", f"{bridge.heartbeat_count}")
    c3.metric("km/h", f"{bridge.current_speed_kmh:.1f}")

    if st.button("🔄 狀態重置", use_container_width=True):
        st.session_state.connected = True
        st.toast("🟢 系統已就緒（V8.1.1 將於注入瞬間自動建立隧道）")
        st.rerun()

    st.divider()

    # 中心座標
    st.markdown("**📍 探索中心座標**")
    coord_text = st.text_input(
        "座標", value=st.session_state.center_text,
        placeholder="25.0339, 121.5644",
        label_visibility="collapsed",
    )
    st.session_state.center_text = coord_text
    center_lat, center_lon = _parse_coord(coord_text)

    poi_limit = st.slider("最多探測點位數", 5, 30, 15, step=5)

    if st.button("🔍 重新探測 POI", use_container_width=True):
        with st.spinner("Overpass 爬取中..."):
            raw = fetch_and_save_pois(
                center_point=(center_lat, center_lon), max_count=poi_limit)
            pois = [_poi_compat(p) for p in raw]
            pois = [_poi_compat(p) for p in raw]
            st.session_state.pois           = pois
            st.session_state.selected_pois  = []
            st.session_state.smooth_path    = []
            st.session_state.road_polyline  = []
            st.session_state.planner        = None
        st.success(f"更新 {len(pois)} 筆景點")
        st.rerun()

    st.divider()

    # 時速 Slider
    st.markdown("**🚶 模擬移動時速**")
    target_speed = st.slider(
        "km/h", min_value=1, max_value=20, value=10, step=1,
        label_visibility="collapsed",
    )
    bridge.target_speed_kmh = target_speed

    loop_nav = st.checkbox("🔄 開啟無限循環巡航", value=True)

    st.divider()

    # 📍 內建中原大學巡迴
    st.markdown("**🎒 特殊路線 (V3.6)**")
    if st.button("📍 一鍵載入中原大學", use_container_width=True):
        cycu_pois = [
            {'id': 'cycu_gate',    'name': '中原大學-校門口',     'lat': 24.95760, 'lon': 121.24080, 'category': 'amenity'},
            {'id': 'cycu_lib',     'name': '中原大學-圖書館',     'lat': 24.95840, 'lon': 121.24100, 'category': 'amenity'},
            {'id': 'cycu_dorm1',   'name': '中原大學-力行宿舍',   'lat': 24.95937, 'lon': 121.24093, 'category': 'amenity'},
            {'id': 'cycu_student', 'name': '中原大學-學生活動中心','lat': 24.95900, 'lon': 121.24020, 'category': 'amenity'},
            {'id': 'cycu_chapel',  'name': '中原大學-懷恩堂',     'lat': 24.95830, 'lon': 121.23980, 'category': 'amenity'},
        ]
        # 更新中心位址，並取代現有選擇
        st.session_state.center_text = "24.95820, 121.24130"
        st.session_state.pois = cycu_pois
        st.session_state.selected_pois = list(cycu_pois)
        st.session_state.smooth_path = []
        st.session_state.road_polyline = []
        st.session_state.planner = None
        st.success("✅ 已載入中原大學 5 個核心地標！")
        st.rerun()

    if st.button("⬜ 一鍵載入簡單矩形路線", use_container_width=True):
        # 台北大安區標準街廓，四個正常十字路口，無髮夾彎
        rect_pois = [
            {'id': 'rect_a', 'name': '矩形-北西', 'lat': 25.02700, 'lon': 121.54300, 'category': 'amenity'},
            {'id': 'rect_b', 'name': '矩形-北東', 'lat': 25.02700, 'lon': 121.54500, 'category': 'amenity'},
            {'id': 'rect_c', 'name': '矩形-南東', 'lat': 25.02500, 'lon': 121.54500, 'category': 'amenity'},
            {'id': 'rect_d', 'name': '矩形-南西', 'lat': 25.02500, 'lon': 121.54300, 'category': 'amenity'},
        ]
        st.session_state.center_text = "25.02600, 121.54400"
        st.session_state.pois = rect_pois
        st.session_state.selected_pois = list(rect_pois)
        st.session_state.smooth_path = []
        st.session_state.road_polyline = []
        st.session_state.planner = None
        st.success("✅ 已載入簡單矩形路線（台北大安區）！")
        st.rerun()

    st.divider()

    # 路徑統計
    sp = st.session_state.smooth_path
    if sp:
        # 總距離：采用插分點數×平均步長估算
        total_m = sum(
            pt.get('step_meters', 3)
            for pt in sp
            if isinstance(pt, dict)
        )
        total_km   = total_m / 1000
        speed_ms   = max(bridge.target_speed_kmh, 1.0) / 3.6
        walk_min   = (total_m / speed_ms) / 60
        walk_h     = int(walk_min // 60)
        walk_m_rem = int(walk_min % 60)

        st.markdown("**📊 路徑統計**")
        s1, s2 = st.columns(2)
        s1.metric("📐 總距離", f"{total_km:.2f} km")
        if walk_h > 0:
            s2.metric("⏱️ 預計步行", f"{walk_h}h {walk_m_rem}m")
        else:
            s2.metric("⏱️ 預計步行", f"{walk_m_rem} 分鐘")
        st.caption(f"{len(sp):,} 個插値點 · 循環封閉路徑")

    st.divider()

    # ── GPX 循環工廠 ───────────────────────────────────────────────────────────
    st.markdown("**🏭 GPX 循環加工廠**")
    gpx_loops = st.slider("🔄 循環次數", min_value=1, max_value=500, value=100, step=10)
    gpx_jitter = st.checkbox("🎲 開啟隨機擾動 (防偵測)", value=True)

    if st.session_state.smooth_path:
        gpx_data = _generate_gpx_xml(
            loops=gpx_loops, 
            use_jitter=gpx_jitter
        )
        st.download_button(
            label=f"💾 導出循環 GPX ({gpx_loops} 圈)",
            data=gpx_data,
            file_name=f"antigravity_x{gpx_loops}.gpx",
            mime="application/gpx+xml",
            use_container_width=True
        )
    else:
        st.download_button(
            label="💾 導出循環 GPX",
            data="",
            disabled=True,
            use_container_width=True,
            help="請先在地圖上選擇點位並計算路徑！"
        )


# ── 主區域 ──────────────────────────────────────────────────────────────────
col_ctrl, col_map = st.columns([1, 2.8], gap="medium")

with col_ctrl:
    st.markdown("### 📍 點位選擇")

    _CAT = {
        'historic': '🏛️ 歷史', 'tourism':  '📷 觀光',
        'amenity':  '🎭 藝文', 'leisure':  '🌳 休閒', 'unknown': '❓ 其他',
    }
    all_cats  = sorted({p.get('category', 'unknown') for p in st.session_state.pois})
    chosen = st.multiselect(
        "類別篩選", all_cats, default=all_cats,
        format_func=lambda c: _CAT.get(c, c),
    )
    disp_pois = [p for p in st.session_state.pois
                 if p.get('category', 'unknown') in chosen]

    n_disp = len(disp_pois)
    n_sel  = len(st.session_state.selected_pois)
    st.caption(f"可選 **{n_disp}** 個  ·  已選 **{n_sel}** 個")

    ba, bb = st.columns(2)
    with ba:
        if st.button("☑️ 全選", use_container_width=True):
            st.session_state.selected_pois = list(disp_pois)
            st.session_state.smooth_path   = []
            st.session_state.road_polyline = []
            st.rerun()
    with bb:
        if st.button("🗑️ 清空", use_container_width=True):
            st.session_state.selected_pois = []
            st.session_state.smooth_path   = []
            st.session_state.road_polyline = []
            st.rerun()

    with st.expander(f"巡迴清單（{n_sel} 點）", expanded=False):
        if st.session_state.selected_pois:
            for i, sp in enumerate(st.session_state.selected_pois):
                st.write(f"{i+1}. {_poi_compat(sp)['name']}")
        else:
            st.caption("點擊地圖標記加入點位")

    st.divider()

    # ── 路徑計算 ──────────────────────────────────────────────────────────
    st.markdown("### 🧭 路徑計算")
    if n_sel >= 2:
        if st.button(f"計算 TSP 最佳路徑（{n_sel} 點）",
                     use_container_width=True,
                     disabled=st.session_state.is_computing):
            st.session_state.is_computing = True
            prog = st.progress(0, text="初始化路網…")
            try:
                prog.progress(10, text="下載 OSMnx 路網（2 km，快取優先）…")
                smooth, road_nodes, planner = _full_pipeline(
                    list(st.session_state.selected_pois),
                    center_lat, center_lon,
                )
                prog.progress(90, text="4.0 m 封閉插值中…")
                st.session_state.smooth_path   = smooth
                st.session_state.road_polyline = road_nodes
                st.session_state.planner       = planner
                prog.progress(100, text="完成！")
                st.success(f"✅ {len(smooth):,} 個插值點（步長 4.0 m，封閉路徑）")
            except Exception as e:
                prog.empty()
                st.exception(e)
            finally:
                st.session_state.is_computing = False

    else:
        st.caption("請選取 ≥2 個點位")

    st.divider()

    has_path = bool(st.session_state.smooth_path)
    # 注入 iOS（需已連線）
    nav_disabled = not st.session_state.connected or not has_path
    if st.button("⏹ 停止導航", use_container_width=True):
        import urllib.request as _ur2
        try:
            _ur2.urlopen(_ur2.Request("http://127.0.0.1:7788/stop", data=b'{}', headers={"Content-Type": "application/json"}), timeout=3)
            st.success("已停止導航")
        except Exception as e:
            st.error(str(e))

    if st.button("📡 注入至 iOS 裝置", disabled=nav_disabled, use_container_width=True):
        import urllib.request as _ur, json as _json
        payload = _json.dumps({
            "coords": st.session_state.smooth_path,
            "loop":   loop_nav
        }).encode()
        try:
            req = _ur.Request("http://127.0.0.1:7788/push",
                              data=payload,
                              headers={"Content-Type": "application/json"})
            res = _json.loads(_ur.urlopen(req, timeout=5).read())
            st.success(f"✅ 已推送 {res.get('pts', 0):,} pts (無限循環: {loop_nav})")
        except Exception as e:
            st.error(str(e))




# ── 地圖 ─────────────────────────────────────────────────────────────────────
with col_map:
    st.markdown("### 🗺️ 互動地圖")
    if has_path:
        pt_count = len(st.session_state.smooth_path)
        st.caption(f"🔵 **{pt_count:,}** 個插值點 &nbsp;·&nbsp; 步長 4.0 m")

    m = _render_map(center_lat, center_lon)
    st_map = st_folium(m, height=690, width="100%",
                       returned_objects=["last_object_clicked"])

    # 點擊地圖標記 → 加入選取清單
    if st_map and st_map.get("last_object_clicked"):
        clat = st_map["last_object_clicked"]["lat"]
        clng = st_map["last_object_clicked"]["lng"]
        for poi in disp_pois:
            if (abs(_lat(poi) - clat) < 0.0001 and
                    abs(_lng(poi) - clng) < 0.0001):
                if not any(_poi_compat(p)['id'] == _poi_compat(poi)['id']
                           for p in st.session_state.selected_pois):
                    st.session_state.selected_pois.append(poi)
                    st.session_state.smooth_path   = []
                    st.session_state.road_polyline = []
                    st.rerun()
                break