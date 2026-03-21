#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bridge_server.py — 獨立 Bridge Server
======================================
用法：
  1. 系統管理員 PowerShell 啟動 tunneld
  2. 另開視窗跑此 server：py bridge_server.py
  3. 啟動 Streamlit：python -m streamlit run app.py

Server 監聽 http://127.0.0.1:7788
API：
  POST /push   body: {"coords": [{"lat":...,"lon":...,...}], "loop": true}
  POST /stop   停止導航
  GET  /status 狀態查詢
"""
import asyncio
import json
import sys
from aiohttp import web

sys.path.insert(0, '.')
from bridge import LocationBridge

bridge = LocationBridge()

async def handle_push(request):
    data = await request.json()
    coords = data.get('coords', [])
    loop   = data.get('loop', True)
    await bridge.push_path_to_queue(coords, loop=loop)
    return web.json_response({'ok': True, 'pts': len(coords)})

async def handle_stop(request):
    bridge.is_navigating = False
    bridge.loop_navigation = False
    bridge.loop_path = False
    # 清空 queue
    while not bridge.queue.empty():
        try: bridge.queue.get_nowait()
        except: break
    # 重置 session，讓下次注入重新建立連線
    bridge._reconnecting = False
    return web.json_response({'ok': True})

async def handle_status(request):
    return web.json_response({
        'navigating':  bridge.is_navigating,
        'queue':       bridge.queue.qsize(),
        'speed_kmh':   bridge.current_speed_kmh,
        'heartbeat':   bridge.heartbeat_count,
        'last_location': list(bridge.last_location) if bridge.last_location else None,
    })

async def main():
    await bridge.start_services()
    app = web.Application()
    app.router.add_post('/push',   handle_push)
    app.router.add_post('/stop',   handle_stop)
    app.router.add_get('/status',  handle_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 7788)
    await site.start()
    print('[BridgeServer] 監聽 http://127.0.0.1:7788')
    print('[BridgeServer] 就緒！等待 Streamlit 推送路徑...')
    while True:
        await asyncio.sleep(1)

if __name__ == '__main__':
    asyncio.run(main())