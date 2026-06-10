"""SmartMonitor 端到端诊断"""
import requests
import json
import asyncio

BASE = "http://localhost:8000"
PASS = 0
FAIL = 0

def ok(name, msg=None):
    global PASS
    PASS += 1
    s = f"  [PASS] {name}"
    if msg: s += f"  {msg}"
    print(s)

def fail(name, msg):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}: {msg}")

# ====== Layer 1: 容器可达性 ======
print("=" * 50)
print("LAYER 1: Docker 容器可达性")
print("=" * 50)

try:
    r = requests.get(f"{BASE}/", timeout=5)
    if r.status_code == 200:
        ok("GET / HTTP 200")
    else:
        fail("GET /", f"status={r.status_code}")
    html = r.text
except Exception as e:
    fail("GET /", str(e))
    html = ""

try:
    r2 = requests.get(f"{BASE}/api/status", timeout=5)
    s = r2.json()
    ok("GET /api/status", f"T={s.get('dht11_t')} H={s.get('dht11_h')} Lv={s.get('level')}")
except Exception as e:
    fail("GET /api/status", str(e))

# ====== Layer 2: HTML 完整性 ======
print("\nLAYER 2: HTML 文件完整性")
if len(html) < 500:
    fail("HTML 大小", f"只有 {len(html)}B，可能是重定向或错误页面")
else:
    ok("HTML 大小", f"{len(html)} bytes")

    # 检查 TLi=[] vs TLi[]
    has_fix = "TLi=[]" in html
    has_bug = "TLi[]" in html and "TLi=[]" not in html
    if has_fix:
        ok("TLi=[] 语法已修复", "正确")
    elif has_bug:
        # count occurrences of TLi
        idx = html.find("TLi")
        snippet = html[idx:idx+15] if idx >= 0 else "TLi NOT FOUND"
        fail("TLi[] 旧BUG仍存在", f"snippet: [{snippet}]")
    else:
        fail("TLi 变量声明", "找不到 TLi 声明")

    # 检查关键函数
    for fn in ["connectWS", "loadInitialData", "handleSensorData", "renderInventory"]:
        if fn in html:
            ok(f"包含 {fn}()")
        else:
            fail(f"缺少 {fn}()", "")

    # 检查轮询降级
    for fn in ["startPolling", "stopPolling", "pollLatest"]:
        if fn in html:
            ok(f"包含 {fn}() (轮询降级)")
        else:
            fail(f"缺少 {fn}() (轮询降级)", "应在浏览器 Ctrl+F5 刷新后看到")

# ====== Layer 3: API 数据 ======
print("\nLAYER 3: API 数据链路")
try:
    r = requests.get(f"{BASE}/api/sensors/recent?limit=3", timeout=5)
    data = r.json()
    if isinstance(data, list) and len(data) > 0:
        last = data[-1]
        ok(f"GET /api/sensors/recent", f"{len(data)} 条, latest id={last.get('id')}, T={last.get('dht11_t')}")
    elif isinstance(data, list):
        fail("GET /api/sensors/recent", "0 条数据! 请确保 udp_to_web.py 在运行")
    else:
        fail("GET /api/sensors/recent", f"返回类型: {type(data)}")
except Exception as e:
    fail("GET /api/sensors/recent", str(e))

# ====== Layer 4: WebSocket ======
print("\nLAYER 4: WebSocket 连通性")
try:
    import websockets
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
    import websockets

async def ws_test():
    try:
        async with websockets.connect("ws://localhost:8000/ws", open_timeout=5) as ws_conn:
            await ws_conn.send('{"type":"ping"}')
            msg = await asyncio.wait_for(ws_conn.recv(), timeout=8)
            d = json.loads(msg)
            keys = list(d.get("data", {}).keys())
            print(f"    WS 数据: type={d['type']}, fields={keys[:5]}")
            return True
    except Exception as e:
        print(f"    WS 错误: {e}")
        return False

ws_ok = asyncio.run(ws_test())
if ws_ok:
    ok("WS 连接成功 + 收到广播数据")
else:
    fail("WS 连接失败", "后端 WebSocket 不通，检查 Docker 日志")

# ====== Layer 5: inventory/log ======
print("\nLAYER 5: 出入库 API")
try:
    r = requests.get(f"{BASE}/api/inventory", timeout=5)
    inv = r.json()
    if isinstance(inv, list):
        ok(f"GET /api/inventory", f"{len(inv)} 条")
    else:
        fail("GET /api/inventory", f"返回 {type(inv)}")
except Exception as e:
    fail("GET /api/inventory", str(e))

try:
    r = requests.get(f"{BASE}/api/warehouse/log?limit=3", timeout=5)
    log = r.json()
    if isinstance(log, list):
        ok(f"GET /api/warehouse/log", f"{len(log)} 条")
    else:
        fail("GET /api/warehouse/log", f"返回 {type(log)}")
except Exception as e:
    fail("GET /api/warehouse/log", str(e))

# ====== 总结 ======
print("\n" + "=" * 50)
print(f"SUMMARY: PASS={PASS} FAIL={FAIL} / {PASS+FAIL}")
if FAIL == 0:
    print(">> 后端全部正常！问题在浏览器端：")
    print("   1. F12 打开 DevTools -> Console 看红字报错")
    print("   2. -> Network 看 /ws 请求状态")
    print("   3. Ctrl+Shift+Del 清除缓存，然后 Ctrl+F5 强刷")
    print("   4. 检查浏览器扩展是否拦截了 WebSocket")
elif FAIL <= 2:
    print(">> 有少量后端问题，修复后重试")
else:
    print(">> 问题较多，建议 docker compose down && docker compose up --build 重建")
print("=" * 50)
