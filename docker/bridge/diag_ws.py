"""用 websockets 库做真实 WS 测试"""
import asyncio

async def test():
    try:
        import websockets
    except ImportError:
        print("Installing websockets...")
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
        import websockets
    
    print("[Test] Connecting ws://localhost:8000/ws ...")
    try:
        async with websockets.connect('ws://localhost:8000/ws', open_timeout=5) as ws:
            print("[PASS] WebSocket connected!")
            # send ping to keep alive
            await ws.send('{"type":"ping"}')
            print(f"[INFO] Sent ping, waiting for data...")
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            print(f"[INFO] Received: {msg[:200]}")
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")

asyncio.run(test())
