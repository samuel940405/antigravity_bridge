# -*- coding: utf-8 -*-
"""
bridge.py — V4.0 反凍結強化版
==============================
對比 V3.6 的修改：
  1. 注入間隔從固定 1.0s 改為 0.8–1.3s 隨機（打破規律節拍）
  2. 心跳間隔從固定 30s 改為 8–14s 動態（短於 USB 省電週期）
  3. WinError 10053 加入指數退避重試（最多 3 次）
  4. 整合 nav_brain.FreezeDetector — 自動偵測並解凍
  5. 整合 nav_brain.BreathingPauseManager — 週期性停頓防 Watchdog
  6. navigation_worker 的 sleep 計算改用真實步長（來自點位 metadata）
"""
import asyncio
import random
import time
from typing import Optional

from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.cli.mounter import auto_mount
from pymobiledevice3.lockdown import create_using_usbmux

from nav_brain import FreezeDetector, BreathingPauseManager

# ─── 常數 ─────────────────────────────────────────────────────────────────
_JITTER_RATIO   = 0.12
_INJECT_INTERVAL_MIN = 0.80   # 秒（原 1.0 固定值）
_INJECT_INTERVAL_MAX = 1.30   # 秒
_HEARTBEAT_MIN  = 8.0         # 秒（原 30s 太長）
_HEARTBEAT_MAX  = 14.0        # 秒


def _get_stealth_jitter() -> float:
    """小幅方位角抖動（bridge 層，GPS 模型抖動已在 nav_brain 處理）。"""
    return random.uniform(4e-7, 1.2e-6) * random.choice([-1, 1])


class LocationBridge:
    """
    V4.0 反凍結強化版 LocationBridge。
    主要改動見模組文件。
    """

    def __init__(self):
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

        self.queue                           = asyncio.Queue()
        self.last_location: Optional[tuple] = None
        self.is_navigating                   = False
        self.heartbeat_count                 = 0
        self.current_speed_kmh               = 0.0
        self.target_speed_kmh                = 5.0
        self.current_path_coords: list      = []
        self.last_sent_time                  = time.time()
        self._reconnecting                   = False
        self.loop_navigation                 = False

        self._tunnel_host = "127.0.0.1"
        self._tunnel_port = 49151

        # ── 反凍結元件 ────────────────────────────────────────────────────
        self._freeze_detector = FreezeDetector(threshold_sec=25.0)
        self._breathing_mgr   = BreathingPauseManager(self, walk_interval_sec=(200, 380))

        asyncio.run_coroutine_threadsafe(self._ensure_mounted(), self.loop)

    # ── DDI 掛載 ──────────────────────────────────────────────────────────
    async def _ensure_mounted(self) -> None:
        print("[Bridge] 檢查 DDI 掛載狀態...")
        try:
            lockdown = await asyncio.to_thread(create_using_usbmux)
            await auto_mount(lockdown=lockdown)
            print("[Bridge] DDI 掛載成功（或已掛載）。")
        except Exception as e:
            print(f"[Bridge] DDI 掛載略過: {e}")

    # ── 斷線自癒 ──────────────────────────────────────────────────────────
    async def _auto_reconnect_task(self):
        if self._reconnecting:
            return
        self._reconnecting = True
        print("[Bridge] ⚠️ 連線中斷，等待 10s 後重試...")
        await asyncio.sleep(10)
        self._reconnecting = False

    # ── 座標注入（核心） ──────────────────────────────────────────────────
    async def set_location(self, lat: float, lon: float) -> None:
        """
        帶指數退避重試的座標注入。
        注入間隔改為隨機 0.8–1.3s（打破固定 1Hz 節拍）。
        """
        self.last_location = (lat, lon)

        # Bridge 層輕微抖動（GPS 主要抖動已在 nav_brain 完成）
        j_lat = lat + _get_stealth_jitter()
        j_lon = lon + _get_stealth_jitter()

        for attempt in range(3):
            try:
                async with RemoteServiceDiscoveryService(
                    (self._tunnel_host, self._tunnel_port)
                ) as rsd:
                    service = LocationSimulation(rsd)
                    await asyncio.to_thread(service.set, j_lat, j_lon)
                    # 隨機注入間隔（替代固定 1.0s）
                    await asyncio.sleep(random.uniform(_INJECT_INTERVAL_MIN,
                                                       _INJECT_INTERVAL_MAX))

                self.last_sent_time = time.time()

                # 凍結偵測更新
                if self._freeze_detector.update(lat, lon):
                    print("[Bridge] 🧊 CoreLocation 凍結偵測！啟動解凍序列...")
                    asyncio.create_task(self._freeze_detector.unfreeze(self))

                return  # 成功，跳出重試迴圈

            except OSError as e:
                # WinError 10053：指數退避
                win_err = getattr(e, 'winerror', None)
                if win_err == 10053 and attempt < 2:
                    wait = 2.0 * (attempt + 1)
                    print(f"[Bridge] WinError 10053（第 {attempt+1} 次），{wait}s 後重試...")
                    await asyncio.sleep(wait)
                else:
                    print(f"[Bridge] 注入失敗: {e}")
                    if not self._reconnecting:
                        asyncio.create_task(self._auto_reconnect_task())
                    return

            except Exception as e:
                print(f"[Bridge] 注入錯誤: {e}")
                if not self._reconnecting:
                    asyncio.create_task(self._auto_reconnect_task())
                return

    # ── 心跳 ──────────────────────────────────────────────────────────────
    async def _heartbeat_worker(self) -> None:
        print("[Bridge] 心跳 Worker 啟動（動態間隔模式）")
        while True:
            # 輪詢頻率 3–6s
            await asyncio.sleep(random.uniform(3.0, 6.0))
            if self.last_location and not self.is_navigating:
                elapsed = time.time() - self.last_sent_time
                # 動態心跳閾值（8–14s）
                threshold = random.uniform(_HEARTBEAT_MIN, _HEARTBEAT_MAX)
                if elapsed >= threshold:
                    self.heartbeat_count += 1
                    self.current_speed_kmh = 0.0
                    await self.set_location(
                        self.last_location[0], self.last_location[1]
                    )

    # ── 導航 ──────────────────────────────────────────────────────────────
    async def _navigation_worker(self) -> None:
        print("[Bridge] 導航 Worker 啟動")
        while True:
            pt = await self.queue.get()
            self.is_navigating = True

            is_turn  = pt.get('is_turning', False)
            step_m   = pt.get('step_meters', DEFAULT_STEP_M)
            speed    = pt.get('speed_kmh', self.target_speed_kmh)

            # nav_brain V3 已附帶 speed_kmh，直接使用
            self.current_speed_kmh = round(speed, 2)

            await self.set_location(pt['lat'], pt['lon'])
            self.queue.task_done()

            # 以實際步長與速度計算 sleep（保留 0.5s 安全下限）
            calc_sleep = step_m / max(speed / 3.6, 0.1)
            await asyncio.sleep(max(0.5, calc_sleep))

            if self.queue.empty():
                if self.loop_navigation and self.current_path_coords:
                    print("[Bridge] 🔄 路徑完成，重新載入循環...")
                    for point in self.current_path_coords:
                        self.queue.put_nowait(point)
                else:
                    self.is_navigating     = False
                    self.current_speed_kmh = 0.0

    # ── 服務啟動 ──────────────────────────────────────────────────────────
    async def start_services(self) -> None:
        asyncio.create_task(self._heartbeat_worker(),      name="hb")
        asyncio.create_task(self._navigation_worker(),     name="nav")
        asyncio.create_task(self._breathing_mgr.run(),     name="breathing")
        print("[Bridge] 所有 Worker 已啟動（含 BreathingPause）")

    # ── 路徑推送 ──────────────────────────────────────────────────────────
    async def push_path_to_queue(self, path_coords: list, loop: bool = False) -> None:
        self.loop_navigation     = loop
        self.current_path_coords = path_coords
        for pt in path_coords:
            await self.queue.put(pt)
        print(f"[Bridge] 已推送 {len(path_coords)} 個點位（循環: {loop}）")


# 供 navigation_worker 使用的預設步長（對應 nav_brain DEFAULT_STEP_M）
DEFAULT_STEP_M = 0.80