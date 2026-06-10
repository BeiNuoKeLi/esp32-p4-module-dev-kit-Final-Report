"""快速诊断 API + WebSocket"""
import requests
import socket
import base64
import uuid

BASE = "http://localhost:8000"

print("=" * 50)
print("SmartMonitor 诊断")
print("=" * 50)

# 1. API 数据
print("\n[1] /api/sensors/recent")
try:
    r = requests.get(f"{BASE}/api/sensors/recent?limit=5", timeout=5)
    print(f"  Status: {r.status_code}")
    data = r.json()
    print(f"  Records: {len(data)}")
    if data:
        last = data[-1]
        print(f"  Last: dht11_t={last.get('dht11_t')}, dht11_h={last.get('dht11_h')}")
    else:
        print("  (empty)")
except Exception as e:
    print(f"  Error: {e}")

# 2. WebSocket 握手
print("\n[2] WebSocket Upgrade Test")
key = base64.b64encode(uuid.uuid4().bytes).decode().strip("=")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(5)
try:
    sock.connect(("localhost", 8000))
    req = (
        f"GET /ws HTTP/1.1\r\n"
        f"Host: localhost:8000\r\n"
        f"Connection: Upgrade\r\n"
        f"Upgrade: websocket\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"\r\n"
    ).encode()
    sock.send(req)
    resp = sock.recv(1024)
    text = resp.decode(errors="replace")[:300]
    print(f"  Response ({len(resp)} bytes):")
    for line in text.split("\r\n"):
        if line.strip():
            print(f"    {line}")
    if b"101" in resp or b"Switching" in resp:
        print("  >>> [PASS] WebSocket OK!")
    else:
        print("  >>> [FAIL] WebSocket FAILED")
except Exception as e:
    print(f"  [FAIL] Error: {e}")
finally:
    sock.close()

# 3. /api/status
print("\n[3] /api/status")
try:
    r = requests.get(f"{BASE}/api/status", timeout=5)
    s = r.json()
    print(f"  T={s.get('dht11_t')}°C H={s.get('dht11_h')}% Lv={s.get('level')}")
except Exception as e:
    print(f"  Error: {e}")
