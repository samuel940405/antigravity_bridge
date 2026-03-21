# -*- coding: utf-8 -*-
"""
pages/花朵收成.py — 疊圖花朵收成模式
逐點瞬間跳轉，每點等待5秒
"""
import json
import math
import pathlib
import time
import streamlit as st
from streamlit_image_coordinates import streamlit_image_coordinates
from PIL import Image

st.set_page_config(layout="wide", page_title="花朵收成模式", page_icon="🌸")

st.markdown("""
<style>
[data-testid="stAppViewContainer"],.main{background:#0b0b0f !important;}
[data-testid="stSidebar"]>div:first-child{background:#10101a !important;}
html,body,[class*="css"],p,span,label,div{color:#d4d4e0 !important;}
h1,h2,h3{color:#ffffff !important;}
div[data-testid="stButton"]>button{background:#181826 !important;color:#b0b0c8 !important;border:1px solid #2a2a40 !important;border-radius:9px !important;}
.stTextInput input,.stNumberInput input{background:#13131f !important;color:#e0e0f0 !important;border:1px solid #2a2a40 !important;border-radius:8px !important;}
</style>
""", unsafe_allow_html=True)

# ── 資料庫 ────────────────────────────────────────────────────────────────────
FLOWERS_FILE = pathlib.Path(__file__).parent.parent / "flowers.json"

def load_flowers():
    if FLOWERS_FILE.exists():
        return json.loads(FLOWERS_FILE.read_text(encoding='utf-8'))
    return []

def save_flowers(flowers):
    FLOWERS_FILE.write_text(json.dumps(flowers, ensure_ascii=False, indent=2), encoding='utf-8')

def push_one(lat, lon):
    """瞬間傳送到單一座標"""
    import urllib.request as _ur, json as _json
    payload = _json.dumps({"coords": [{"lat": lat, "lon": lon}], "loop": False}).encode()
    req = _ur.Request("http://127.0.0.1:7788/push", data=payload,
                      headers={"Content-Type": "application/json"})
    _ur.urlopen(req, timeout=5)

def parse_coord(text, default=(25.0339, 121.5644)):
    try:
        parts = [p.strip() for p in text.replace('，', ',').split(',')]
        if len(parts) == 2:
            return float(parts[0]), float(parts[1])
    except Exception:
        pass
    return default

def px_to_coord(px, py, cal):
    dx_px = cal['px2'] - cal['px1']
    dy_px = cal['py2'] - cal['py1']
    dlat  = cal['lat2'] - cal['lat1']
    dlon  = cal['lon2'] - cal['lon1']
    scale_lat = dlat / dy_px if dy_px != 0 else 0
    scale_lon = dlon / dx_px if dx_px != 0 else 0
    return (round(cal['lat1'] + (py - cal['py1']) * scale_lat, 6),
            round(cal['lon1'] + (px - cal['px1']) * scale_lon, 6))

# ── Session State ─────────────────────────────────────────────────────────────
for k, v in {
    'calib':        None,
    'calib_step':   0,
    'calib_p1':     None,
    'calib_p2':     None,
    'flower_list':  [],
    'last_flower':  None,
    'harvesting':   False,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ══════════════════════════════════════════════════════════════════════════════
st.markdown("# 🌸 花朵收成模式")
st.caption("上傳截圖 → 點兩個地標校正 → 點花朵 → 一鍵逐點跳轉收成")
st.divider()

uploaded = st.file_uploader("上傳皮克敏截圖", type=["png","jpg","jpeg"])
if not uploaded:
    st.info("請先上傳皮克敏地圖截圖")
    st.stop()

img = Image.open(uploaded)

col_img, col_ctrl = st.columns([1.5, 1], gap="large")

with col_img:
    step = st.session_state.calib_step
    calib = st.session_state.calib

    if step == 0:
        st.markdown("### 第一步：點截圖上的地標 1")
        st.caption("找一個你認識的地點點擊（路口、建築入口）")
        coords = streamlit_image_coordinates(img, key="c1")
        if coords:
            st.session_state.calib_p1 = (coords['x'], coords['y'])
            st.session_state.calib_step = 1
            st.rerun()

    elif step == 1:
        st.markdown("### 第二步：輸入地標 1 的真實座標")
        p1 = st.session_state.calib_p1
        st.caption(f"你點的位置：X={p1[0]}, Y={p1[1]}")
        # 顯示截圖讓使用者對照
        st.image(img, use_column_width=True)

    elif step == 2:
        st.markdown("### 第三步：點截圖上的地標 2")
        st.caption("再點另一個地標，兩點距離越遠越準確")
        coords = streamlit_image_coordinates(img, key="c2")
        if coords:
            st.session_state.calib_p2 = (coords['x'], coords['y'])
            st.session_state.calib_step = 3
            st.rerun()

    elif step == 3 and calib and calib.get('px2') is None:
        st.markdown("### 第四步：輸入地標 2 的真實座標")
        p2 = st.session_state.calib_p2
        st.caption(f"你點的位置：X={p2[0]}, Y={p2[1]}")
        st.image(img, use_column_width=True)

    else:
        # 校正完成，進入點花朵模式
        st.markdown("### 🌸 點截圖上的花朵位置")
        st.caption("每次點擊自動換算座標並加入清單，不會重新整理截圖")
        coords = streamlit_image_coordinates(img, key="flower_click")
        if coords:
            px, py = coords['x'], coords['y']
            flower_key = f"{px}_{py}"
            if flower_key != st.session_state.last_flower:
                st.session_state.last_flower = flower_key
                real_lat, real_lon = px_to_coord(px, py, st.session_state.calib)
                new_flower = {
                    'lat': real_lat, 'lon': real_lon,
                    'name': f"花朵({real_lat:.4f},{real_lon:.4f})"
                }
                st.session_state.flower_list.append(new_flower)
                st.toast(f"✅ 已加入 ({real_lat:.4f}, {real_lon:.4f})")

with col_ctrl:
    # ── 校正步驟控制 ──────────────────────────────────────────────────────────
    if st.session_state.calib_step == 1:
        st.markdown("### 📍 地標 1 真實座標")
        coord1 = st.text_input("座標（緯度, 經度）", value="24.9580, 121.2410",
                               placeholder="24.9580, 121.2410", key="coord1_input")
        if st.button("✅ 確認，繼續點地標 2", use_container_width=True):
            lat1, lon1 = parse_coord(coord1)
            p1 = st.session_state.calib_p1
            st.session_state.calib = {
                'px1': p1[0], 'py1': p1[1], 'lat1': lat1, 'lon1': lon1,
                'px2': None,  'py2': None,  'lat2': None, 'lon2': None,
            }
            st.session_state.calib_step = 2
            st.rerun()

    elif st.session_state.calib_step == 3 and st.session_state.calib and st.session_state.calib.get('px2') is None:
        st.markdown("### 📍 地標 2 真實座標")
        coord2 = st.text_input("座標（緯度, 經度）", value="24.9600, 121.2430",
                               placeholder="24.9600, 121.2430", key="coord2_input")
        if st.button("✅ 完成校正！開始點花朵", use_container_width=True):
            lat2, lon2 = parse_coord(coord2)
            p2 = st.session_state.calib_p2
            st.session_state.calib['px2'] = p2[0]
            st.session_state.calib['py2'] = p2[1]
            st.session_state.calib['lat2'] = lat2
            st.session_state.calib['lon2'] = lon2
            st.rerun()

    elif st.session_state.calib_step == 0 or (
        st.session_state.calib_step == 3 and st.session_state.calib and st.session_state.calib.get('px2') is not None
    ):
        # ── 花朵清單 ──────────────────────────────────────────────────────────
        flowers = st.session_state.flower_list
        st.markdown(f"### 🌸 花朵清單（{len(flowers)} 朵）")

        if flowers:
            for i, f in enumerate(flowers):
                c1, c2 = st.columns([3, 1])
                c1.caption(f"{i+1}. {f['name']}")
                if c2.button("✕", key=f"del_{i}"):
                    st.session_state.flower_list.pop(i)
                    st.rerun()

            st.divider()

            wait_sec = st.slider("每個花點停留秒數", min_value=3, max_value=15, value=5, step=1)

            if st.button("📡 一鍵逐點跳轉收成", use_container_width=True):
                progress_bar = st.progress(0, text="開始收成...")
                status_text  = st.empty()
                total = len(flowers)

                # TSP 最近鄰排序
                ordered = [flowers[0]]
                remaining = list(flowers[1:])
                while remaining:
                    last = ordered[-1]
                    nearest = min(remaining,
                                  key=lambda p: (p['lat']-last['lat'])**2 + (p['lon']-last['lon'])**2)
                    ordered.append(nearest)
                    remaining.remove(nearest)

                for i, f in enumerate(ordered):
                    status_text.markdown(f"🌸 正在收成第 **{i+1}/{total}** 朵：{f['name']}")
                    try:
                        push_one(f['lat'], f['lon'])
                    except Exception as e:
                        st.error(f"第 {i+1} 朵傳送失敗：{e}")
                    progress_bar.progress((i+1)/total, text=f"{i+1}/{total} 完成")
                    time.sleep(wait_sec)

                status_text.markdown("✅ **全部收成完畢！**")
                progress_bar.progress(1.0, text="完成！")

            st.divider()

            if st.button("💾 存入資料庫", use_container_width=True):
                existing = load_flowers()
                existing.extend(flowers)
                save_flowers(existing)
                st.success(f"✅ 已存入 {len(flowers)} 朵到資料庫")

            if st.button("🗑️ 清空清單", use_container_width=True):
                st.session_state.flower_list = []
                st.session_state.last_flower = None
                st.rerun()

        else:
            if st.session_state.calib_step > 0:
                st.caption("👈 點截圖上的花朵位置來加入清單")

        st.divider()

        # ── 資料庫 ────────────────────────────────────────────────────────────
        db_flowers = load_flowers()
        st.markdown(f"### 🗃️ 資料庫（{len(db_flowers)} 朵）")
        if db_flowers:
            wait_db = st.slider("停留秒數", min_value=3, max_value=15, value=5,
                                step=1, key="wait_db")
            if st.button("📡 從資料庫逐點收成", use_container_width=True):
                progress_bar = st.progress(0)
                status_text  = st.empty()
                total = len(db_flowers)
                for i, f in enumerate(db_flowers):
                    status_text.markdown(f"🌸 第 **{i+1}/{total}** 朵")
                    try:
                        push_one(f['lat'], f['lon'])
                    except Exception as e:
                        st.error(f"失敗：{e}")
                    progress_bar.progress((i+1)/total)
                    time.sleep(wait_db)
                status_text.markdown("✅ **完成！**")

            if st.button("🗑️ 清空資料庫", use_container_width=True):
                save_flowers([])
                st.rerun()
        else:
            st.caption("資料庫是空的")

        if st.session_state.calib_step > 0:
            st.divider()
            if st.button("🔄 重新校正", use_container_width=True):
                st.session_state.calib      = None
                st.session_state.calib_step = 0
                st.session_state.calib_p1   = None
                st.session_state.calib_p2   = None
                st.rerun()