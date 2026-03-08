# -*- coding: utf-8 -*-
"""
bridge.py — V5.0 DVT 持久連線版
================================
V5 核心改動：
  1. 建立一個持久的 DVT session，不再每次注入都重新連線
  2. 動態從 tunneld 讀取 IPv6 位址
  3. session 斷線時自動重建
  4. 修正 service.set 為正確的 async DVT API
"""
import asyncio
import random
import time
import urllib.request
import json
from typing import Optional

from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.services.dvt.dvt_secure_socket_proxy import DvtSecureSocketProxyService
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

from nav_brain import FreezeDetector, BreathingPauseManager

_INJECT_INTERVAL_MIN = 0.80
_INJECT_INTERVAL_MAX = 1.30
_HEARTBEAT_MIN       = 8.0
_HEARTBEAT_MAX       = 14.0
DEFAULT_STEP_M       = 0.80


def _get_stealth_jitter():
    return random.uniform(4e-7, 1.2e-6) * random.choice([-1, 1])


def _get_tunnel_address():
    try:
        r = urllib.request.urlopen("http://127.0.0.1:49151/", timeout=3)
        data = json.loads(r.read())
        for udid, tunnels in data.items():
            if tunnels:
                addr = tunnels[0]["tunnel-address"]
                port = tunnels[0]["tunnel-port"]
                print(f"[Bridge] 找到隧道: {addr}:{port}")
                return addr, port
    except Exception as e:
        print(f"[Bridge] 無法讀取 tunneld: {e}")
    return None, None


class LocationBridge:

    def __init__(self):
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

        self.queue                           = asyncio.Queue()
        self.last_location                   = None
        self.is_navigating                   = False
        self.heartbeat_count                 = 0
        self.current_speed_kmh               = 0.0
        self.target_speed_kmh                = 5.0
        self.current_path_coords             = []
        self.last_sent_time                  = time.time()
        self.loop_navigation                 = False
        self._service                        = None
        self._freeze_detector = FreezeDetector(threshold_sec=25.0)
        self._breathing_mgr   = BreathingPauseManager(self, walk_interval_sec=(200, 380))

    async def _build_session(self):
        addr, port = _get_tunnel_address()
        if not addr:
            print("[Bridge] 找不到隧道，請確認 tunneld 已用管理員身份啟動")
            return False
        try:
            print(f"[Bridge] 連線 DVT session: {addr}:{port}")
            self._rsd_ctx = RemoteServiceDiscoveryService((addr, port))
            rsd = await self._rsd_ctx.__aenter__()
            self._dvt_ctx = DvtSecureSocketProxyService(lockdown=rsd)
            dvt = await self._dvt_ctx.__aenter__()
            self._service = LocationSimulation(dvt)
            print("[Bridge] DVT session 建立成功")
            return True
        except Exception as e:
            print(f"[Bridge] DVT session 建立失敗: {e}")
            self._service = None
            return False

    async def _ensure_session(self):
        if self._service is not None:
            return True
        return await self._build_session()

    async def set_location(self, lat, lon):
        self.last_location = (lat, lon)
        j_lat = lat + _get_stealth_jitter()
        j_lon = lon + _get_stealth_jitter()
        for attempt in range(3):
            try:
                if not await self._ensure_session():
                    await asyncio.sleep(3)
                    continue
                await self._service.set(j_lat, j_lon)
                self.last_sent_time = time.time()
                if self._freeze_detector.update(lat, lon):
                    print("[Bridge] 凍結偵測！啟動解凍序列...")
                    asyncio.create_task(self._freeze_detector.unfreeze(self))
                return
            except Exception as e:
                print(f"[Bridge] 注入失敗（第{attempt+1}次）: {e}，重建 session...")
                self._service = None
                await asyncio.sleep(2.0 * (attempt + 1))

    async def _heartbeat_worker(self):
        print("[Bridge] 心跳 Worker 啟動")
        while True:
            await asyncio.sleep(random.uniform(3.0, 6.0))
            if self.last_location and not self.is_navigating:
                elapsed = time.time() - self.last_sent_time
                threshold = random.uniform(_HEARTBEAT_MIN, _HEARTBEAT_MAX)
                if elapsed >= threshold:
                    self.heartbeat_count += 1
                    self.current_speed_kmh = 0.0
                    await self.set_location(*self.last_location)

    async def _navigation_worker(self):
        print("[Bridge] 導航 Worker 啟動")
        while True:
            pt = await self.queue.get()
            self.is_navigating = True
            step_m = pt.get("step_meters", DEFAULT_STEP_M)
            speed  = pt.get("speed_kmh", self.target_speed_kmh)
            self.current_speed_kmh = round(speed, 2)
            await self.set_location(pt["lat"], pt["lon"])
            self.queue.task_done()
            calc_sleep = step_m / max(speed / 3.6, 0.1)
            await asyncio.sleep(max(0.5, calc_sleep))
            if self.queue.empty():
                if self.loop_navigation and self.current_path_coords:
                    print("[Bridge] 路徑完成，重新載入循環...")
                    for point in self.current_path_coords:
                        self.queue.put_nowait(point)
                else:
                    self.is_navigating     = False
                    self.current_speed_kmh = 0.0

    async def start_services(self):
        ok = await self._build_session()
        if not ok:
            print("[Bridge] 初始 session 失敗，將在注入時重試")
        asyncio.create_task(self._heartbeat_worker(),  name="hb")
        asyncio.create_task(self._navigation_worker(), name="nav")
        asyncio.create_task(self._breathing_mgr.run(), name="breathing")
        print("[Bridge] 所有 Worker 已啟動")

    async def push_path_to_queue(self, path_coords, loop=False):
        self.loop_navigation     = loop
        self.current_path_coords = path_coords
        for pt in path_coords:
            await self.queue.put(pt)
        print(f"[Bridge] 已推送 {len(path_coords)} 個點位（循環: {loop}）")