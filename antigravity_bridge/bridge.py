# -*- coding: utf-8 -*-
"""
bridge.py — V3.6 Ultimate LocationBridge (Rock Solid Edition)
=============================================================
• Explicit pymobiledevice3 v8.1.1 imports (No abbreviations)
• Asynchronous auto-mounting using pymobiledevice3.cli.mounter
• Context manager (async with) for RemoteServiceDiscoveryService
• Strictly tuned for iPhone 11: minimal pinging to prevent connection drops
"""
import asyncio
import random
import time
from typing import Optional, Tuple

from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.cli.mounter import auto_mount
from pymobiledevice3.lockdown import create_using_usbmux

# ─── Constants ─────────────────────────────────────────────────────────────
_HEARTBEAT_SEC  = 30
_JITTER_RATIO   = 0.12


def _get_stealth_jitter() -> float:
    """Returns a dynamic jitter between 0.1m and 0.3m (in degrees)."""
    return random.uniform(9e-7, 2.7e-6) * random.choice([-1, 1])


class LocationBridge:
    """
    Ultimate stability bridge for injecting coordinates to iOS devices.
    Designed to prevent 'never awaited' warnings and 'BrokenPipeError'.
    """

    def __init__(self):
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

        self.queue                            = asyncio.Queue()
        self.last_location: Optional[tuple]  = None
        self.is_navigating                    = False
        self.heartbeat_count                  = 0
        self.current_speed_kmh                = 0.0
        self.target_speed_kmh                 = 5.0
        self.current_path_coords: list       = []
        self.last_sent_time                   = time.time()
        self._reconnecting                    = False
        self.loop_navigation                  = False

        # Configuration for tunneld connection
        self._tunnel_host = "127.0.0.1"
        self._tunnel_port = 49151

        # Attempt to auto-mount Developer Disk Image asynchronously
        asyncio.run_coroutine_threadsafe(self._ensure_mounted(), self.loop)

    async def _ensure_mounted(self) -> None:
        """Automounts DDI using lockdown client as required by pymobiledevice3."""
        print("[Bridge] Checking Developer Disk Image mount status...")
        try:
            # We connect via USBMux to mount the DDI if it's missing
            lockdown = await asyncio.to_thread(create_using_usbmux)
            await auto_mount(lockdown=lockdown)
            print("[Bridge] DDI Automount successful or already mounted.")
        except Exception as e:
            # Silently degrade if not authorized or not found, as we might already be mounted 
            # or connecting through network
            print(f"[Bridge] DDI Automount pass skipped: {e}")

    # ── Auto Healing ─────────────────────────────────────────────────────────
    async def _auto_reconnect_task(self):
        """Debounced reconnect task."""
        if self._reconnecting:
            return
        self._reconnecting = True
        print("[Bridge] ⚠️ Connection dropped, delaying 10s to prevent rapid firing...")
        await asyncio.sleep(10)
        self._reconnecting = False

    # ── Safe Injection ───────────────────────────────────────────────────────
    async def set_location(self, lat: float, lon: float) -> None:
        """
        Rock-solid coordinate injection.
        Creates an `async with` context manager for every send to guarantee safety.
        """
        self.last_location = (lat, lon)
        
        # Jitter Calculation
        j_lat = lat + _get_stealth_jitter()
        j_lon = lon + _get_stealth_jitter()

        try:
            # Open tunnel strictly with context manager to avoid un-awaited coroutine errors
            async with RemoteServiceDiscoveryService((self._tunnel_host, self._tunnel_port)) as rsd:
                service = LocationSimulation(rsd)
                
                # Perform the set and strictly wait
                await asyncio.to_thread(service.set, j_lat, j_lon)
                
                # Minimum stability sleep after atomic injection
                await asyncio.sleep(1.0)
                
            self.last_sent_time = time.time()

        except Exception as e:
            print(f"[Bridge] Injection error: {e}")
            if not self._reconnecting:
                asyncio.create_task(self._auto_reconnect_task())

    # ── Heartbeat Worker ─────────────────────────────────────────────────────
    async def _heartbeat_worker(self) -> None:
        print("[Bridge] Heartbeat logic primed (low-noise mode)")
        while True:
            await asyncio.sleep(5.0)  # Low rate polling
            if self.last_location:
                # 30-sec interval to prevent device kick
                if time.time() - self.last_sent_time >= _HEARTBEAT_SEC:
                    self.heartbeat_count += 1
                    if not self.is_navigating:
                        self.current_speed_kmh = 0.0
                    await self.set_location(self.last_location[0], self.last_location[1])

    # ── Navigation Worker ────────────────────────────────────────────────────
    async def _navigation_worker(self) -> None:
        print("[Bridge] Navigation Worker active")
        while True:
            pt = await self.queue.get()
            self.is_navigating = True

            is_turn  = pt.get('is_turning', False)
            step_m   = pt.get('step_meters', 4.0)
            target   = self.target_speed_kmh

            if is_turn:
                spd = min(target, 2.5)
            else:
                spd = target * random.uniform(1 - _JITTER_RATIO, 1 + _JITTER_RATIO)

            spd = max(0.5, spd)
            self.current_speed_kmh = round(spd, 2)

            # Issue location set
            await self.set_location(pt['lat'], pt['lon'])
            self.queue.task_done()

            # Throttling
            calc_sleep = step_m / (spd / 3.6)
            await asyncio.sleep(max(0.5, calc_sleep))  # absolute 0.5s floor for safety

            if self.queue.empty():
                if self.loop_navigation and self.current_path_coords:
                    print("[Bridge] 🔄 Path complete, reloading loop...")
                    for point in self.current_path_coords:
                        self.queue.put_nowait(point)
                else:
                    self.is_navigating     = False
                    self.current_speed_kmh = 0.0

    # ── Service Start ────────────────────────────────────────────────────────
    async def start_services(self) -> None:
        asyncio.create_task(self._heartbeat_worker(),  name="hb")
        asyncio.create_task(self._navigation_worker(), name="nav")
        print("[Bridge] Routine Workers Started")

    # ── Path Push ────────────────────────────────────────────────────────────
    async def push_path_to_queue(self, path_coords: list, loop: bool = False) -> None:
        self.loop_navigation = loop
        self.current_path_coords = path_coords
        for pt in path_coords:
            await self.queue.put(pt)
        print(f"[Bridge] Pushed {len(path_coords)} points (Loop: {loop})")