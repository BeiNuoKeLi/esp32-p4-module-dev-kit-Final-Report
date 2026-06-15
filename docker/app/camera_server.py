"""
摄像头服务 — MJPEG HTTP 流输出

支持多种数据源模式 (环境变量 CAMERA_MODE):

  http (默认): Docker 直接 HTTP GET ESP32-CAM /capture → 零丢包
      ESP32-CAM ──HTTP/TCP──► Docker camera_server.py
      绕过 P4 UDP 中继，TCP 保证完整送达，无分片/丢包问题

  tcp: ESP32-CAM 二进制 TCP 推流 (旧方案, 受 RTT 限制)
      ESP32-CAM ──TCP :8003──► Docker camera_server.py
      帧格式 [2B big-endian len][JPEG]

  udp_esp32 (推荐): ESP32-CAM UDP 直连推流 — 无窗口限制，跨海 119x 吞吐
      ESP32-CAM ──UDP :8003──► Docker camera_server.py
      协议格式 [Magic(0xAA55)+FrameID+ChunkIdx+TotalChunks][JPEG分片]

  udp: ESP32-P4 UDP 中继转发 (旧方案)
      ESP32-CAM → ESP32-P4 → UDP :8082 → Docker camera_server.py

  push: 外部 HTTP POST 推送 (push_jpeg 端点)

  mjpeg_stream: Docker 直接拉 ESP32-CAM /stream MJPEG 流

环境变量:
  CAMERA_MODE         = http | tcp | udp_esp32 | udp | push (默认 http)
  ESP32_CAM_URL       = http://10.16.234.23/capture (HTTP 模式)
  CAMERA_HTTP_FPS     = 5  (HTTP 拉流帧率, 默认 5)
  CAMERA_TCP_PORT     = 8003  (UDP/TCP 推流端口)

提供接口:
  1. GET /api/camera/mjpeg   — multipart/x-mixed-replace 实时视频流
  2. GET /api/camera/snapshot — 最新一帧 JPEG bytes

降级策略:
  - 若容器内无 OpenCV → 自动进入 offline 模式，MJPEG 返回黑色占位图
  - 无数据到达时 → MJPEG 保持最后一帧，snapshot 返回空
"""
import os
import socket
import threading
import time
import urllib.request
from collections import OrderedDict
from typing import Optional

CAMERA_MODE = os.getenv("CAMERA_MODE", "http")
ESP32_CAM_URL = os.getenv("ESP32_CAM_URL", "http://10.16.234.23/capture")
# MJPEG 流地址: CameraWebServer 在 port 81 提供 /stream 端点
ESP32_CAM_STREAM_URL = os.getenv("ESP32_CAM_STREAM_URL", "http://10.16.234.23:81/stream")
CAMERA_HTTP_FPS_LIMIT = int(os.getenv("CAMERA_HTTP_FPS", "3"))  # 仅 /capture 轮询模式使用

# 项目内协议模块 (camera_protocol.py 在 docker/app/ 同目录)
from . import camera_protocol as proto

CAMERA_PORT = 8082
CAMERA_BUF_SIZE = 65536
SOCK_TIMEOUT = 0.002
FRAME_TIMEOUT = 3.0
FRAME_STALE_MS = 0.35
MAX_FRAME_CACHE = 5
MJPEG_FPS_LIMIT = 10
JPEG_QUALITY = 85
CAMERA_TCP_PORT = int(os.getenv("CAMERA_TCP_PORT", "8003"))  # ★ TCP 二进制推流端口


class CameraServer:
    """
    后台线程驱动的摄像头数据接收器。

    用法:
        cs = CameraServer()
        cs.start()          # 启动 UDP 监听线程
        frame = cs.latest_frame      # 最新 numpy BGR 帧 或 None
        jpeg_bytes = cs.latest_jpeg  # 最新 JPEG bytes 或 None
        cs.stop()           # 停止
    """

    def __init__(self):
        self.running = False
        self.sock: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None

        # 帧缓存 {frame_id: {chunks[], total, received:set, start_time}}
        self.frame_cache: OrderedDict[int, dict] = OrderedDict()

        # 最新解码结果
        self._latest_jpeg: Optional[bytes] = None
        self._latest_frame = None          # numpy BGR array (需要 cv2)
        self._frame_lock = threading.Lock()

        # MJPEG 输出帧同步 — 解决浏览器缓冲导致 7s 延迟
        self._mjpeg_new_frame = threading.Event()
        self._mjpeg_frame_seq = 0

        # 统计
        self.total_frames = 0
        self.fps_history: list[float] = []
        self.chunk_count = 0          # 收到的 UDP 分片总数
        self.timeout_count = 0        # 超时丢弃的帧数
        self._last_debug_ts = 0.0

        # 可用性标记
        self.cv2_ok = False
        self._try_import_cv2()

        # 占位 JPEG (黑色 320x240)
        self.placeholder_jpeg = self._make_placeholder()

        # 视频流开关（默认开启，线程安全）
        self._stream_enabled = True
        # UDP 接收暂停标志（关闭视频流时暂停接收，节省带宽/CPU）
        self._paused = False

    @property
    def should_push(self) -> bool:
        """ESP32-CAM 是否需要继续推送帧（有观看者时为 True）"""
        return self.stream_enabled and self.running

    @property
    def stream_enabled(self) -> bool:
        with self._frame_lock:
            return self._stream_enabled

    @stream_enabled.setter
    def stream_enabled(self, val: bool):
        with self._frame_lock:
            self._stream_enabled = val

    @property
    def paused(self) -> bool:
        with self._frame_lock:
            return self._paused

    @paused.setter
    def paused(self, val: bool):
        with self._frame_lock:
            self._paused = val

    @property
    def online(self) -> bool:
        """摄像头是否有真实数据流入"""
        return self.running and self._latest_jpeg is not None

    @property
    def latest_jpeg(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._latest_jpeg

    @latest_jpeg.setter
    def latest_jpeg(self, val: Optional[bytes]):
        with self._frame_lock:
            self._latest_jpeg = val

    @property
    def latest_frame(self):
        with self._frame_lock:
            return self._latest_frame

    @latest_frame.setter
    def latest_frame(self, val):
        with self._frame_lock:
            self._latest_frame = val

    # ─── 初始化 ──────────────────────────────────────────

    def _try_import_cv2(self):
        try:
            import cv2
            self._cv2 = cv2
            self.cv2_ok = True
        except ImportError:
            self._cv2 = None
            print("[Camera] ⚠️ OpenCV 未安装，摄像头功能降级为离线模式")

    def _make_placeholder(self) -> bytes:
        """生成诊断占位 JPEG (红色=占位图bug, 黑色=摄像头暗帧)"""
        if self.cv2_ok:
            import numpy as np
            red = np.zeros((240, 320, 3), dtype=np.uint8)
            red[:, :, 2] = 255  # BGR红色通道
            _, buf = self._cv2.imencode('.jpg', red,
                                         [self._cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            return buf.tobytes()
        else:
            # 极简 JPEG (最小有效文件 ~107 bytes)
            return bytes([
                0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46,
                0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
                0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
                0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08,
                0x07, 0x07, 0x07, 0x09, 0x09, 0x08, 0x0A, 0x0C,
                0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
                0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D,
                0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20,
                0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
                0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27,
                0x39, 0x3D, 0x38, 0x32, 0x3C, 0x2E, 0x33, 0x34,
                0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0xF0,
                0x01, 0x40, 0x03, 0x01, 0x11, 0x00, 0xFF, 0xC4,
                0x00, 0x1F, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
                0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
                0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0xFF,
                0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
                0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04,
                0x00, 0x00, 0x01, 0x7D, 0x01, 0x02, 0x03, 0x00,
                0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
                0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32,
                0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1,
                0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
                0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A,
                0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35,
                0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
                0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55,
                0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65,
                0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
                0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85,
                0x86, 0x87, 0x88, 0x89, 0x8A, 0x92, 0x93, 0x94,
                0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
                0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2,
                0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA,
                0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
                0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8,
                0xD9, 0xDA, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6,
                0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
                0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA,
                0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F, 0x00,
                0x7B, 0x94, 0x11, 0x00, 0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                0x00, 0x00, 0x00, 0xFF, 0xD9,
            ])

    # ─── 生命周期 ──────────────────────────────────────────

    def _try_set_jpeg(self, jpeg_data: bytes) -> bool:
        """保存 JPEG 原始字节并通知 MJPEG 输出端（轻量路径，不做 cv2.imdecode）。

        imdecode 是 CPU 密集操作（50-200ms），在 VPS 弱 CPU 上会阻塞接收线程，
        导致后续帧积压 → 延迟雪崩。改为仅存储原始字节，latest_frame 按需在
        try_decode_frame() 中解码（扫码等低频场景使用）。
        """
        if not jpeg_data:
            return False
        self.latest_jpeg = jpeg_data
        self._mjpeg_frame_seq += 1
        self._mjpeg_new_frame.set()
        return True

    def push_jpeg(self, jpeg_data: bytes):
        """外部推送 JPEG 帧 (ESP32-CAM 直推模式) — ★ 轻量路径，不阻塞事件循环

        关键优化：不做 cv2.imdecode（50-200ms CPU 密集），只保存原始 JPEG bytes。
        latest_frame 按需在 scan 端点解码，不在热路径执行。
        """
        # 直接保存 JPEG bytes — 加锁保护（与其他线程竞争）
        self.latest_jpeg = jpeg_data
        # 通知 MJPEG 输出端有新帧
        self._mjpeg_frame_seq += 1
        self._mjpeg_new_frame.set()
        self.total_frames += 1
        # 更新 FPS 统计
        t0 = time.time()
        self.fps_history.append(t0)
        if len(self.fps_history) > 30:
            self.fps_history.pop(0)

    def try_decode_frame(self) -> bool:
        """按需解码 latest_jpeg → latest_frame（用于 QR 扫码等场景）。
        返回 True 表示解码成功。此调用是同步 CPU 密集操作，仅应在低频场景（按需）调用。"""
        jpeg_data = self.latest_jpeg
        if not jpeg_data or not self.cv2_ok:
            return False
        try:
            import numpy as np
            arr = np.frombuffer(jpeg_data, dtype=np.uint8)
            frame = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
            if frame is not None:
                self.latest_frame = frame
                return True
        except Exception:
            pass
        return False

    def start(self):
        """启动后台接收线程（根据 CAMERA_MODE 选择 udp_esp32 / tcp / push / MJPEG流 / UDP 中继）"""
        if self.running:
            return
        self.running = True
        if CAMERA_MODE == "tcp":
            self.thread = threading.Thread(target=self._tcp_stream_loop, daemon=True, name="CameraTCP")
            self.thread.start()
            print(f"[Camera] ✅ TCP 二进制推流模式 监听 :{CAMERA_TCP_PORT}")
        elif CAMERA_MODE == "udp_esp32":
            self.thread = threading.Thread(target=self._udp_esp32_loop, daemon=True, name="CameraESP32UDP")
            self.thread.start()
            print(f"[Camera] ✅ ESP32-CAM UDP 直连模式 监听 :{CAMERA_TCP_PORT}")
        elif CAMERA_MODE == "push":
            self.thread = threading.Thread(target=self._push_dummy_loop, daemon=True, name="CameraPush")
            self.thread.start()
            print(f"[Camera] ✅ Push 模式 (等待 ESP32-CAM 直推) 端点 POST /api/camera/push")
        elif CAMERA_MODE == "udp":
            self.thread = threading.Thread(target=self._recv_loop, daemon=True, name="CameraUDP")
            self.thread.start()
            print(f"[Camera] ✅ UDP 中继模式 监听 :{CAMERA_PORT}")
        else:
            self.thread = threading.Thread(target=self._mjpeg_stream_loop, daemon=True, name="CameraMJPEG")
            self.thread.start()
            print(f"[Camera] ✅ MJPEG 流模式 → {ESP32_CAM_STREAM_URL}")

    def _push_dummy_loop(self):
        """push 模式占位线程 — 保持 self.running=True, 帧由外部 push_jpeg() 注入"""
        while self.running:
            time.sleep(5)

    # ─── TCP 二进制推流接收 (推荐, 零 HTTP 开销) ────────────

    def _tcp_stream_loop(self):
        """监听 TCP 端口，接收 ESP32-CAM 二进制推流

        帧格式: [2-byte big-endian length][JPEG data]
        一条 TCP 长连接持续接收所有帧，无 HTTP 逐帧握手开销。
        """
        import struct

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("0.0.0.0", CAMERA_TCP_PORT))
        except OSError as e:
            print(f"[Camera] ❌ 无法绑定 TCP 端口 {CAMERA_TCP_PORT}: {e}")
            self.running = False
            return
        server.listen(1)
        server.settimeout(1.0)  # 1s 超时以检查 self.running
        print(f"[Camera] 🔌 TCP 推流监听 :{CAMERA_TCP_PORT} (等待 ESP32-CAM 连接)")

        while self.running:
            client = None
            try:
                client, addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            print(f"[Camera] 🔗 ESP32-CAM 已连接 ({addr[0]}:{addr[1]})")
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client.settimeout(5.0)
            buffer = b""
            timeout_count = 0

            try:
                while self.running:
                    if self.paused:
                        time.sleep(0.1)
                        continue

                    try:
                        data = client.recv(65536)
                    except socket.timeout:
                        timeout_count += 1
                        if timeout_count > 3:
                            print("[Camera] ⚠️ TCP 读取超时, 断开")
                            break
                        continue

                    if not data:
                        print("[Camera] ⚠️ ESP32-CAM 断开 (EOF)")
                        break

                    timeout_count = 0
                    buffer += data

                    # 解析帧: [2B big-endian len][JPEG]
                    while len(buffer) >= 2:
                        frame_len = struct.unpack(">H", buffer[:2])[0]
                        if frame_len == 0:          # 心跳/空帧, 跳过
                            buffer = buffer[2:]
                            continue
                        total_needed = 2 + frame_len
                        if len(buffer) >= total_needed:
                            jpeg = buffer[2:total_needed]
                            if len(jpeg) > 500:
                                t0 = time.time()
                                self._try_set_jpeg(jpeg)
                                self.total_frames += 1
                                self.fps_history.append(t0)
                                if len(self.fps_history) > 30:
                                    self.fps_history.pop(0)
                            buffer = buffer[total_needed:]
                        else:
                            break  # 等下一个 recv

                    # 周期诊断
                    tnow = time.time()
                    if tnow - self._last_debug_ts > 15:
                        rate = self.fps
                        print(f"[Camera] 📊 TCP推流 | fps={rate:.1f} | 总帧={self.total_frames} | buf={len(buffer)}B")
                        self._last_debug_ts = tnow

            except (ConnectionError, OSError) as e:
                print(f"[Camera] ❌ TCP 异常: {e}")
            finally:
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass

            if self.running:
                print("[Camera] 🔄 等待 ESP32-CAM 重连...")

        try:
            server.close()
        except Exception:
            pass


    def stop(self):
        """停止接收线程并释放资源"""
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.frame_cache.clear()
        print("[Camera] 已停止")

    def pause(self):
        """暂停接收（关闭视频流时节省带宽和 CPU）"""
        self.paused = True
        print("[Camera] ⏸️ 接收已暂停")

    def resume(self):
        """恢复接收"""
        self.paused = False
        print("[Camera] ▶️ 接收已恢复")

    # ─── MJPEG 流拉取 (后台线程, 推荐) ──────────────────────

    def _mjpeg_stream_loop(self):
        """连接 ESP32-CAM /stream 端点，持续解析 MJPEG 帧

        数据流:
          ESP32-CAM :81/stream → multipart/x-mixed-replace → 提取 JPEG → frame_cache

        CameraWebServer 的 /stream 端口是 HTTP + 1 = 81 (见 app_httpd.cpp)
        边界分隔符固定: --123456789000000000000987654321
        """
        import http.client

        # 解析 stream URL 的 host 和路径
        stream_url = ESP32_CAM_STREAM_URL
        # e.g. http://10.16.234.23:81/stream → host="10.16.234.23:81"
        if stream_url.startswith("http://"):
            url_no_scheme = stream_url[7:]
        elif stream_url.startswith("https://"):
            url_no_scheme = stream_url[8:]
        else:
            url_no_scheme = stream_url
        if "/" in url_no_scheme:
            host, path = url_no_scheme.split("/", 1)
            path = "/" + path
        else:
            host, path = url_no_scheme, "/"

        reconnect_delay = 1.0  # 初始重连间隔

        while self.running:
            if self.paused:
                time.sleep(0.5)
                continue

            conn = None
            try:
                print(f"[Camera] 🔌 连接 MJPEG 流 → {stream_url}")
                t_connect = time.time()

                if ":" in host:
                    h, p = host.split(":", 1)
                    conn = http.client.HTTPConnection(h, int(p), timeout=10)
                else:
                    conn = http.client.HTTPConnection(host, 80, timeout=10)

                conn.request("GET", path, headers={
                    "User-Agent": "SmartMonitor/3.0",
                    "Accept": "multipart/x-mixed-replace",
                })
                # ★ 关闭 Nagle 算法：MJPEG 流场景下不让 TCP 等待凑满 MSS 再发送，
                #    每个 JPEG 帧产出后立即 push 到网络，减少数十 ms 的累积延迟
                try:
                    conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass
                resp = conn.getresponse()

                if resp.status != 200:
                    body = resp.read(512)
                    print(f"[Camera] ❌ MJPEG 流 {resp.status}: {body[:200]}")
                    conn.close()
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, 15)
                    continue

                # 解析 Content-Type 获取 boundary
                content_type = resp.getheader("Content-Type", "")
                boundary = None
                if "boundary=" in content_type:
                    boundary = content_type.split("boundary=")[1].strip()
                    # 去引号
                    if boundary.startswith('"') and boundary.endswith('"'):
                        boundary = boundary[1:-1]
                if not boundary:
                    # CameraWebServer 固定边界值
                    boundary = "123456789000000000000987654321"

                boundary_bytes = f"--{boundary}".encode()
                boundary_end_bytes = f"--{boundary}--".encode()

                print(f"[Camera] ✅ MJPEG 流已连接 | boundary={boundary} | 耗时{time.time()-t_connect:.1f}s")
                reconnect_delay = 1.0  # 连接成功重置退避

                # 读取流式数据并解析帧
                buffer = b""
                while self.running and not self.paused:
                    chunk = resp.read(8192)
                    if not chunk:
                        print("[Camera] ⚠️ MJPEG 流断开 (EOF)")
                        break
                    buffer += chunk

                    # 在 buffer 中查找完整的帧 (两个 boundary 之间)
                    while True:
                        # 找下一个 boundary
                        idx = buffer.find(boundary_bytes)
                        if idx < 0:
                            break

                        # 找 boundary 后面的 \r\n (跳过 header)
                        header_start = idx + len(boundary_bytes)
                        if header_start + 2 > len(buffer):
                            break  # 数据不完整, 等下个 chunk

                        # 检查是否是结束 boundary (--boundary--)
                        if buffer[idx:idx + len(boundary_end_bytes)] == boundary_end_bytes:
                            buffer = buffer[idx + len(boundary_end_bytes):]
                            print("[Camera] ⚠️ MJPEG 流结束 (收到结束标记)")
                            break

                        # body 在 \r\n\r\n 之后
                        body_start = buffer.find(b"\r\n\r\n", header_start)
                        if body_start < 0:
                            # 缓冲区不够看完整 header
                            if len(buffer) > 131072:  # >128KB 还没找到 header 结束, 丢弃
                                print("[Camera] ⚠️ 缓冲区溢出, 丢弃")
                                buffer = b""
                            break

                        body_start += 4  # 跳过 \r\n\r\n

                        # 找下一个 boundary 确定 body 结束位置
                        next_boundary = buffer.find(boundary_bytes, body_start)
                        if next_boundary < 0:
                            # 还没收到下一个 boundary, 等下个 chunk
                            if len(buffer) > 524288:  # >512KB 还没下个帧, 丢弃
                                print("[Camera] ⚠️ 超大缓冲区, 丢弃")
                                buffer = b""
                            break

                        # 提取 JPEG 帧
                        jpeg_data = buffer[body_start:next_boundary]
                        # 去除末尾可能的 \r\n
                        jpeg_data = jpeg_data.rstrip(b"\r\n")

                        if jpeg_data and len(jpeg_data) > 500:
                            t0 = time.time()
                            self._try_set_jpeg(jpeg_data)
                            self.total_frames += 1
                            self.fps_history.append(t0)
                            if len(self.fps_history) > 30:
                                self.fps_history.pop(0)

                        # 移动 buffer 指针到下一个 boundary
                        buffer = buffer[next_boundary:]

                    # 周期诊断
                    tnow = time.time()
                    if tnow - self._last_debug_ts > 15:
                        rate = self.fps
                        print(f"[Camera] 📊 MJPEG流 | fps={rate:.1f} | 总帧={self.total_frames}")
                        self._last_debug_ts = tnow

            except (http.client.HTTPException, ConnectionError,
                    TimeoutError, OSError) as e:
                print(f"[Camera] ❌ MJPEG 连接异常: {e}")
            except Exception as e:
                print(f"[Camera] ❌ MJPEG 未知异常: {e}")
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

            if self.running:
                print(f"[Camera] 🔄 {reconnect_delay:.0f}s 后重连...")
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 15)

    # ─── HTTP 逐帧拉流 (旧方案, 保留) ───────────────────────

    def _http_fetch_loop(self):
        """[旧] HTTP GET /capture 逐帧轮询 (已被 MJPEG 流替代)"""
        import urllib.error
        interval = 1.0 / CAMERA_HTTP_FPS_LIMIT
        self._consecutive_errors = 0

        while self.running:
            if self.paused:
                time.sleep(0.1)
                continue

            t0 = time.time()
            if self._consecutive_errors > 3:
                backoff = min(self._consecutive_errors * 0.5, 5.0)
                time.sleep(backoff)
                t0 = time.time()

            jpeg_data = None
            resp = None
            try:
                req = urllib.request.Request(ESP32_CAM_URL)
                req.add_header("User-Agent", "SmartMonitor/3.0")
                req.add_header("Connection", "close")
                resp = urllib.request.urlopen(req, timeout=8)
                status = resp.getcode()

                if status == 200:
                    jpeg_data = resp.read()
                    if jpeg_data and len(jpeg_data) > 500:
                        self._try_set_jpeg(jpeg_data)
                        self.total_frames += 1
                        self.fps_history.append(t0)
                        if len(self.fps_history) > 30:
                            self.fps_history.pop(0)
                        self._consecutive_errors = 0
                    else:
                        self._consecutive_errors += 1
                elif status == 418:
                    self._consecutive_errors += 1
                else:
                    print(f"[Camera] HTTP ⚠️ 状态码 {status}")
                    self._consecutive_errors += 1
            except urllib.error.URLError as e:
                print(f"[Camera] HTTP ❌ 连接失败: {e.reason}")
                self._consecutive_errors += 1
            except (TimeoutError, OSError) as e:
                print(f"[Camera] HTTP ❌ 超时/IO: {e}")
                self._consecutive_errors += 1
            except Exception as e:
                print(f"[Camera] HTTP ❌ 异常: {e}")
                self._consecutive_errors += 1
            finally:
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass

            elapsed = time.time() - t0
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

            tnow = time.time()
            if tnow - self._last_debug_ts > 15:
                rate = self.fps
                print(f"[Camera] 📊 HTTP逐帧 | fps={rate:.1f} | 总帧={self.total_frames}")
                self._last_debug_ts = tnow

    # ─── UDP 接收主循环 (后台线程) ─────────────────────────

    def _recv_loop(self):
        """持续接收 UDP 分片 → 重组完整 JPEG 帧 → 缓存"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            self.sock.bind(("0.0.0.0", CAMERA_PORT))
            self.sock.settimeout(SOCK_TIMEOUT)
        except OSError as e:
            print(f"[Camera] ❌ 无法绑定端口 {CAMERA_PORT}: {e}")
            self.running = False
            return

        while self.running:
            # 暂停状态：不接收 UDP 数据，低开销轮询
            if self.paused:
                time.sleep(0.1)
                continue

            try:
                data, _ = self.sock.recvfrom(CAMERA_BUF_SIZE)
            except socket.timeout:
                continue
            except OSError:
                if self.running:
                    print("[Camera] ⚠️ Socket 异常")
                break

            # 解析协议头
            result = proto.unpack_header(data)
            if result is None:
                continue

            frame_id, chunk_idx, total_chunks = result
            jpeg_chunk = data[proto.HEADER_SIZE:]
            self.chunk_count += 1

            # 放入帧缓存
            if frame_id not in self.frame_cache:
                if len(self.frame_cache) >= MAX_FRAME_CACHE:
                    # 优先驱逐不完整的帧，避免丢失可用帧
                    to_evict = None
                    for fid in self.frame_cache:
                        fc = self.frame_cache[fid]
                        if len(fc["received"]) < fc["total"]:
                            to_evict = fid
                            break
                    if to_evict is None:
                        self.frame_cache.popitem(last=False)
                    else:
                        del self.frame_cache[to_evict]
                self.frame_cache[frame_id] = {
                    "chunks": [b""] * total_chunks,
                    "total": total_chunks,
                    "received": set(),
                    "start_time": time.time(),
                    "last_chunk_time": time.time(),
                }
            cache = self.frame_cache[frame_id]
            if chunk_idx not in cache["received"]:
                cache["chunks"][chunk_idx] = jpeg_chunk
                cache["received"].add(chunk_idx)
                cache["last_chunk_time"] = time.time()

            # 收集完整帧
            completed_fids = [
                fid for fid, c in self.frame_cache.items()
                if len(c["received"]) == c["total"]
            ]
            for fid in completed_fids:
                cache_entry = self.frame_cache.pop(fid)
                jpeg_data = b"".join(cache_entry["chunks"])
                if self._try_set_jpeg(jpeg_data):
                    self.total_frames += 1
                    now = time.time()
                    self.fps_history.append(now)
                    if len(self.fps_history) > 30:
                        self.fps_history.pop(0)

            # 清理超时未完成的帧
            tnow = time.time()
            stale = [
                fid for fid, fc in self.frame_cache.items()
                if tnow - fc["start_time"] > FRAME_TIMEOUT
            ]
            for fid in stale:
                c = self.frame_cache.pop(fid)
                received = len(c["received"])
                total = c["total"]
                # 收到 >= 半数分片时尝试残缺解码
                if received > total // 2:
                    partial = b"".join(c["chunks"])
                    ok = self._try_set_jpeg(partial)
                    self.total_frames += 1
                    self.fps_history.append(tnow)
                    if len(self.fps_history) > 30:
                        self.fps_history.pop(0)
                    print(f"[Camera] 🔧 帧 {fid} 超时渲染 | 已收 {received}/{total} 分片 | {'✅完整' if ok else '⚠️残缺→浏览器容错'}")
                else:
                    self.timeout_count += 1
                    print(f"[Camera] ⚠️ 帧 {fid} 超时丢弃 | 已收 {received}/{total} 分片")

            # 提前渲染：无新分片超过 FRAME_STALE_MS 且收到足够的包
            early_render = [
                fid for fid, fc in self.frame_cache.items()
                if tnow - fc["last_chunk_time"] > FRAME_STALE_MS
                and len(fc["received"]) > fc["total"] // 2
            ]
            for fid in early_render:
                c = self.frame_cache.pop(fid)
                received = len(c["received"])
                total = c["total"]
                partial = b"".join(c["chunks"])
                ok = self._try_set_jpeg(partial)
                self.total_frames += 1
                self.fps_history.append(tnow)
                if len(self.fps_history) > 30:
                    self.fps_history.pop(0)
                print(f"[Camera] ⚡ 帧 {fid} 提前渲染 | 已收 {received}/{total} 分片 | {'✅完整' if ok else '⚠️残缺→浏览器容错'}")

            # 周期输出诊断信息（每 15 秒）
            if tnow - self._last_debug_ts > 15:
                rate = self.fps
                print(f"[Camera] 📊 诊断 | fps={rate:.1f} | 完成帧={self.total_frames} | "
                      f"收到分片={self.chunk_count} | 超时丢弃={self.timeout_count} | "
                      f"缓存帧={len(self.frame_cache)}")
                self._last_debug_ts = tnow

        # 退出清理
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # ─── ESP32-CAM 直连 UDP 接收 (互联网路径, 推荐) ──────────

    def _udp_esp32_loop(self):
        """ESP32-CAM 直连 UDP 接收 — 互联网分片重组 + 超时容错

        与 _recv_loop (P4 LAN 中继) 的主要区别:
        - 绑定 CAMERA_TCP_PORT (8003) 而非 CAMERA_PORT (8082)
        - Socket 超时 10ms (互联网延迟更高)
        - 帧超时 5s + 提前渲染 1.0s (给丢包重排更多时间)
        """
        listen_port = CAMERA_TCP_PORT
        sock_timeout = 0.01          # 10ms socket 超时
        frame_timeout = 5.0          # 互联网帧超时
        early_render_stale = 1.0     # 等待 1s 后提前渲染

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            self.sock.bind(("0.0.0.0", listen_port))
            self.sock.settimeout(sock_timeout)
        except OSError as e:
            print(f"[Camera] ❌ 无法绑定 UDP 端口 {listen_port}: {e}")
            self.running = False
            return

        print(f"[Camera] 🔌 ESP32-CAM UDP 直连监听 :{listen_port} (互联网分片重组)")

        while self.running:
            if self.paused:
                time.sleep(0.1)
                continue

            try:
                data, addr = self.sock.recvfrom(CAMERA_BUF_SIZE)
            except socket.timeout:
                continue
            except OSError:
                if self.running:
                    print("[Camera] ⚠️ UDP Socket 异常")
                break

            # 解析协议头
            result = proto.unpack_header(data)
            if result is None:
                print(f"[Camera] ❌ 头解析失败 | len={len(data)} | head={data[:16].hex()}")
                continue

            frame_id, chunk_idx, total_chunks = result
            jpeg_chunk = data[proto.HEADER_SIZE:]
            self.chunk_count += 1

            # ★ 每50分片轻量简报
            if self.chunk_count % 50 == 0:
                print(f"[Camera] 📦 分片={self.chunk_count} | 缓存帧={len(self.frame_cache)} | 完成={self.total_frames} | 丢弃={self.timeout_count}")

            tnow = time.time()

            # ── 帧缓存与驱逐 ──
            if frame_id not in self.frame_cache:
                if len(self.frame_cache) >= MAX_FRAME_CACHE:
                    to_evict = None
                    for fid in self.frame_cache:
                        fc = self.frame_cache[fid]
                        if len(fc["received"]) < fc["total"]:
                            to_evict = fid
                            break
                    if to_evict is None:
                        self.frame_cache.popitem(last=False)
                    else:
                        del self.frame_cache[to_evict]
                self.frame_cache[frame_id] = {
                    "chunks": [b""] * total_chunks,
                    "total": total_chunks,
                    "received": set(),
                    "start_time": tnow,
                    "last_chunk_time": tnow,
                }

            cache = self.frame_cache[frame_id]
            if chunk_idx not in cache["received"]:
                cache["chunks"][chunk_idx] = jpeg_chunk
                cache["received"].add(chunk_idx)
                cache["last_chunk_time"] = tnow

            # ── 完整帧收集 ──
            completed_fids = [
                fid for fid, c in self.frame_cache.items()
                if len(c["received"]) == c["total"]
            ]
            for fid in completed_fids:
                cache_entry = self.frame_cache.pop(fid)
                jpeg_data = b"".join(cache_entry["chunks"])
                # ★ 过滤暗帧: HVGA正常≥6KB, <4KB大概率暗帧（之前2.5KB没拦住）
                if len(jpeg_data) < 4000:
                    self.timeout_count += 1
                    print(f"[Camera] 🖤 帧 {fid} 疑似暗帧 ({len(jpeg_data)}B), 保留旧帧")
                    continue
                if self._try_set_jpeg(jpeg_data):
                    self.total_frames += 1
                    self.fps_history.append(tnow)
                    if len(self.fps_history) > 30:
                        self.fps_history.pop(0)

            # ── 超时清理 (互联网: 5s) ──
            stale = [
                fid for fid, fc in self.frame_cache.items()
                if tnow - fc["start_time"] > frame_timeout
            ]
            for fid in stale:
                c = self.frame_cache.pop(fid)
                received = len(c["received"])
                total = c["total"]
                if received > total // 2:
                    self.timeout_count += 1
                    print(f"[Camera] ⏳ 帧 {fid} 超时保留旧帧 | {received}/{total} (不更新画面防花屏)")
                else:
                    self.timeout_count += 1
                    print(f"[Camera] ⚠️ 帧 {fid} 超时丢弃 | {received}/{total}")

            # ── 提前渲染 (互联网: 1.0s 无新分片) ──
            early_render = [
                fid for fid, fc in self.frame_cache.items()
                if tnow - fc["last_chunk_time"] > early_render_stale
                and len(fc["received"]) > fc["total"] // 2
            ]
            for fid in early_render:
                c = self.frame_cache.pop(fid)
                received = len(c["received"])
                total = c["total"]
                self.timeout_count += 1
                print(f"[Camera] ⚡ 帧 {fid} 提前丢弃 | {received}/{total} (保留旧帧防花屏)")

            # ── 周期诊断 (每 15s) ──
            if tnow - self._last_debug_ts > 15:
                rate = self.fps
                print(f"[Camera] 📊 ESP32-UDP直连 | fps={rate:.1f} | 总帧={self.total_frames} | "
                      f"分片={self.chunk_count} | 超时丢弃={self.timeout_count} | "
                      f"缓存={len(self.frame_cache)}")
                self._last_debug_ts = tnow

        # 清理
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # ─── FPS 计算 ───────────────────────────────────────────

    @property
    def fps(self) -> float:
        """估算当前接收帧率"""
        history = self.fps_history
        if len(history) < 2:
            return 0.0
        dur = history[-1] - history[0]
        return (len(history) - 1) / dur if dur > 0 else 0.0

    # ─── MJPEG 流生成器 ────────────────────────────────────

    def mjpeg_stream(self):
        """
        FastAPI StreamingResponse 用的生成器。
        产出 multipart/x-mixed-replace 格式数据块。

        关键优化: 使用 Event 驱动，只在有新帧到达时才发送，
        避免重复帧堆积在浏览器缓冲区造成 7s+ 延迟。
        若超过 5 秒无新帧则发送最后一帧保活连接（不再发送占位黑图）。

        ★ 竞态修复: wait()→clear() 之间存在窗口，push_jpeg() 若在此窗口 set()，
           Event 已为 True → set() 无效 → clear() 清掉 → 事件丢失。
           修复: clear() 后二次读 seq，若已变则重新 set() 唤醒下一次 wait()。
        """
        boundary = "--frameboundary"
        last_seq = -1
        keepalive_interval = 0.5
        last_sent_jpeg = self.placeholder_jpeg  # 初始占位，收到首帧后永不黑屏

        while self.running:
            self._mjpeg_new_frame.wait(timeout=keepalive_interval)
            current_seq = self._mjpeg_frame_seq
            self._mjpeg_new_frame.clear()
            if self._mjpeg_frame_seq != current_seq:
                self._mjpeg_new_frame.set()
                current_seq = self._mjpeg_frame_seq

            if self.stream_enabled:
                if current_seq != last_seq:
                    jpeg_data = self.latest_jpeg
                    if jpeg_data:
                        last_sent_jpeg = jpeg_data  # ★ 缓存好帧，用于回退
                        last_seq = current_seq
                    else:
                        jpeg_data = last_sent_jpeg   # ★ 绝不发黑图，复用上一好帧
                else:
                    jpeg_data = last_sent_jpeg
            else:
                jpeg_data = self.placeholder_jpeg

            yield (
                f"{boundary}\r\n"
                f"Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(jpeg_data)}\r\n"
                f"Cache-Control: no-store, no-cache, max-age=0\r\n"
                f"Pragma: no-cache\r\n"
                f"\r\n"
            ).encode("ascii") + jpeg_data + b"\r\n"


# ─── 模块级单例 ─────────────────────────────────────────────
_camera_instance: Optional[CameraServer] = None


def get_camera_server() -> CameraServer:
    """获取全局 CameraServer 单例"""
    global _camera_instance
    if _camera_instance is None:
        _camera_instance = CameraServer()
    return _camera_instance


def start_camera():
    """便捷入口：启动全局单例"""
    cs = get_camera_server()
    cs.start()
    return cs


def stop_camera():
    """便捷入口：停止全局单例"""
    global _camera_instance
    if _camera_instance:
        _camera_instance.stop()
        _camera_instance = None
