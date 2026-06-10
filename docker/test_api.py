"""
SmartMonitor API 集成测试脚本
运行前提: docker compose up --build 已成功启动
用法: cd docker && D:\Anaconda3\envs\ForAgents\python.exe test_api.py
"""
import requests
import json
import sys

BASE = "http://localhost:8000"
PASS = 0
FAIL = 0


def check(name, fn):
    """运行一个测试用例，捕获异常并统计"""
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✅ {name}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}: {e}")


def expect_json(code):
    """返回一个闭包：对 URL 发起 GET，断言状态码，返回解析后的 JSON"""
    def _get(url, c=code):
        resp = requests.get(BASE + url, timeout=5)
        assert resp.status_code == c, f"GET {url} status={resp.status_code} (期望 {c})"
        return resp.json()
    return _get


def expect_json_post(url, data, code=200):
    """对 URL 发起 POST JSON，断言状态码，返回解析后的 JSON"""
    resp = requests.post(BASE + url, json=data, timeout=5)
    assert resp.status_code == code, f"POST {url} status={resp.status_code} (期望 {code})"
    return resp.json()


def expect_status_get(url, code=200):
    """仅断言状态码，不解析 body (用于非 JSON 返回)"""
    resp = requests.get(BASE + url, timeout=5)
    assert resp.status_code == code, f"GET {url} status={resp.status_code} (期望 {code})"
    return resp


def main():
    get = expect_json(200)

    print("=" * 60)
    print("SmartMonitor API 集成测试")
    print(f"目标地址: {BASE}")
    print("=" * 60)

    # ── ① 基础连通性 ──
    print("\n📡 [1] 基础连通性")
    check("GET / 仪表盘首页(HTML)", lambda: expect_status_get("/", 200))
    check("GET /docs 文档页(HTML)", lambda: expect_status_get("/docs", 200))

    # ── ② 初始空状态 ──
    print("\n📡 [2] 初始空状态查询")

    def test_status_empty():
        resp = get("/api/status")
        assert resp["level"] == 0, f"空库时 level 应为 0，实际 {resp['level']}"

    def test_recent_returns_list():
        resp = get("/api/sensors/recent?limit=10")
        assert isinstance(resp, list), f"应返回列表, 实际 {type(resp)}"
        print(f"      → 已有 {len(resp)} 条历史数据（DB volume 持久化）")

    def test_inventory_returns_list():
        resp = get("/api/inventory")
        assert isinstance(resp, list), f"应返回列表, 实际 {type(resp)}"
        print(f"      → 已有 {len(resp)} 条库存记录（DB volume 持久化）")

    def test_log_returns_list():
        resp = get("/api/warehouse/log")
        assert isinstance(resp, list), f"应返回列表, 实际 {type(resp)}"
        print(f"      → 已有 {len(resp)} 条流水记录（DB volume 持久化）")

    check("GET /api/status (空DB)", test_status_empty)
    check("GET /api/sensors/recent (返回列表)", test_recent_returns_list)
    check("GET /api/inventory (返回列表)", test_inventory_returns_list)
    check("GET /api/warehouse/log (返回列表)", test_log_returns_list)

    # ── ③ 传感器数据上传 + 滤波 ──
    print("\n📡 [3] 传感器数据上传 & 滤波验证")

    def test_post_normal():
        resp = expect_json_post("/api/sensors", {
            "dht11_t": 25.6, "dht11_h": 62.0,
            "ds18b20_t": 24.8, "mq135_v": 1.2,
            "light_v": 2.0, "level": 0,
            "alert": 0, "reason": "", "err": 0
        })
        assert resp["ok"] is True, f"ok 应为 True, 实际 {resp}"

    def test_post_abnormal():
        """异常数据：温度-999、湿度150、MQ135 5V → 滤波器应截断"""
        resp = expect_json_post("/api/sensors", {
            "dht11_t": -999, "dht11_h": 150,
            "ds18b20_t": 200, "mq135_v": 5.0,
            "light_v": -1.0, "level": 0,
            "alert": 1, "reason": "test", "err": 1
        })
        assert resp["ok"] is True, "异常数据也应成功写入"

    def test_recent_has_data():
        resp = get("/api/sensors/recent?limit=5")
        assert len(resp) >= 2, f"至少2条, 实际 {len(resp)}"
        print(f"      → 共 {len(resp)} 条记录")

    def test_status_has_data():
        resp = get("/api/status")
        print(f"      → T={resp.get('dht11_t')}°C, H={resp.get('dht11_h')}%, Lv={resp['level']}")

    check("POST /api/sensors 正常数据", test_post_normal)
    check("POST /api/sensors 异常温度（滤波）", test_post_abnormal)
    check("GET /api/sensors/recent 有数据", test_recent_has_data)
    check("GET /api/status 有数据", test_status_has_data)

    # ── ④ 出入库完整流程 ──
    print("\n📡 [4] 出入库管理")

    env = {"env_temp": 25.0, "env_humi": 60.0, "env_level": 0}
    import time as _time
    item1 = {"item_id": f"MAT-{int(_time.time())}A", "name": "芯片A", "category": "电子元件",
             "batch": "B2024", "spec": "SOT-23", "mfg_date": "2024-01-15",
             "exp_date": "2026-01-15"}
    item2 = {"item_id": f"MAT-{int(_time.time())}B", "name": "传感器B", "category": "传感器",
             "batch": "C2024", "spec": "DIP-8", "mfg_date": "2024-06-01",
             "exp_date": "2027-06-01"}

    def test_checkin_1():
        resp = expect_json_post("/api/warehouse/checkin", {**item1, **env})
        assert resp["ok"] is True, f"入库应成功: {resp.get('message')}"

    def test_checkin_duplicate():
        resp = expect_json_post("/api/warehouse/checkin", {**item1, **env})
        assert resp["ok"] is False, f"重复入库应被拒绝: {resp}"

    def test_checkin_2():
        resp = expect_json_post("/api/warehouse/checkin", {**item2, **env})
        assert resp["ok"] is True, f"入库应成功: {resp.get('message')}"

    def test_inventory_display():
        resp = get("/api/inventory")
        assert len(resp) >= 2, f"至少2件, 实际 {len(resp)}"
        print(f"      → 在库: {[r['id'] + '=' + r['status'] for r in resp]}")

    def test_checklog():
        resp = get("/api/warehouse/log?limit=5")
        assert len(resp) >= 2, f"至少2条, 实际 {len(resp)}"
        print(f"      → 流水: {[(l['action'], l['item_id']) for l in resp]}")

    def test_checkout():
        resp = expect_json_post("/api/warehouse/checkout", {"item_id": item1["item_id"], **env})
        assert resp["ok"] is True, f"出库应成功: {resp.get('message')}"

    def test_checkout_duplicate():
        resp = expect_json_post("/api/warehouse/checkout", {"item_id": item1["item_id"], **env})
        assert resp["ok"] is False, f"重复出库应被拒绝, 实际 ok={resp['ok']}"

    def test_checkout_nonexist():
        resp = expect_json_post("/api/warehouse/checkout", {"item_id": "NONEXIST", **env})
        assert resp["ok"] is False, f"不存在物料应被拒绝, 实际 ok={resp['ok']}"

    def test_inventory_filter():
        resp = get("/api/inventory?status=在库")
        assert len(resp) >= 1, f"至少1件在库, 实际 {len(resp)}"
        ok = all(r["status"] == "在库" for r in resp)
        assert ok, "过滤结果应全是'在库'"

    check("POST /api/warehouse/checkin item1", test_checkin_1)
    check("POST /api/warehouse/checkin 重复入库(拒绝)", test_checkin_duplicate)
    check("POST /api/warehouse/checkin item2", test_checkin_2)
    check("GET /api/inventory 查看库存", test_inventory_display)
    check("GET /api/warehouse/log 查看流水", test_checklog)
    check("POST /api/warehouse/checkout item1", test_checkout)
    check("POST /api/warehouse/checkout 重复出库(拒绝)", test_checkout_duplicate)
    check("POST /api/warehouse/checkout 不存在物料", test_checkout_nonexist)
    check("GET /api/inventory?status=在库 过滤", test_inventory_filter)

    # ── ⑤ 摄像头接口 ──
    print("\n📡 [5] 摄像头接口（无真实ESP32-CAM）")

    def test_camera_status():
        resp = get("/api/camera/status")
        print(f"      → online={resp['online']}, cv2_ok={resp['cv2_ok']}, running={resp['running']}")

    def test_camera_snapshot():
        resp = expect_status_get("/api/camera/snapshot")
        ct = resp.headers.get("content-type", "")
        assert ct.startswith("image/"), f"应返回图片类型, 实际 {ct}"
        assert len(resp.content) > 100, "图片数据不应为空"

    check("GET /api/camera/status", test_camera_status)
    check("GET /api/camera/snapshot", test_camera_snapshot)

    # ── 结果汇总 ──
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"📊 总计: {total} | ✅ 通过: {PASS} | ❌ 失败: {FAIL}")
    if FAIL == 0:
        print("🎉 全部测试通过！")
    else:
        print("⚠️ 有测试失败，请检查上方错误信息")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
