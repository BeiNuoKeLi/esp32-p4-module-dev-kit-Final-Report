"""
摄像头服务 — UDP 8082 接收 + JPEG 分片重组 + MJPEG HTTP 流

实际摄像头数据流:
  ESP32-CAM (AI-Thinker, CameraWebServer.ino)
    → HTTP GET http://10.16.234.23/capture (QVGA JPEG, quality=10)
    → ESP32-P4 (camera_http_fetch.c, FreeRTOS Task, 3 fps)
    → UDP :8082 (0xAA55 协议分片, 每包 4096B payload)
    → Docker camera_server.py 后台线程直收（不经过 udp_to_web.py）

已弃用方案（camera_capture_sender.py / camera_display_receiver.py）:
  PC USB UVC 摄像头 → wasm_camera_capture_sender.py → UDP :8082

提供接口:
  1. GET /api/camera/mjpeg   — multipart/x-mixed-replace 实时视频流（支持 stream_enabled 开关）
  2. GET /api/camera/snapshot — 返回最新一帧 JPEG bytes (用于扫码 / 报警拍照)

降级策略:
  - 若容器内无 OpenCV → 自动进入 offline 模式，MJPEG 返回黑色占位图
  - UDP 无数据到达时 → MJPEG 保持最后一帧，snapshot 返回空
"""
import os
import socket
import threading
import time
from collections import OrderedDict
from typing import Optional

# 项目内协议模块 (camera_protocol.py 在 docker/app/ 同目录)
from . import camera_protocol as proto

CAMERA_PORT = 8082
CAMERA_BUF_SIZE = 65536
SOCK_TIMEOUT = 0.002       # socket recv 超时 (ms)
FRAME_TIMEOUT = 5.0        # 帧重组超时 (s)，P4 HTTP 拉图最高 3.9s
MAX_FRAME_CACHE = 6        # 最大缓存帧数（需 > 并发帧数，3fps×1s超时≈3帧，留余量）
MJPEG_FPS_LIMIT = 10       # MJPEG 生成器最大 FPS
JPEG_QUALITY = 85          # 编码质量 (仅占位图使用)


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

        # 视频流开关（默认开启）
        self.stream_enabled = True
        # UDP 接收暂停标志（关闭视频流时暂停接收，节省带宽/CPU）
        self.paused = False

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
        """生成黑色占位 JPEG (无需 cv2 的纯 Python 实现)"""
        if self.cv2_ok:
            import numpy as np
            black = np.zeros((240, 320, 3), dtype=np.uint8)
            _, buf = self._cv2.imencode('.jpg', black,
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

    def start(self):
        """启动后台 UDP 接收线程"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._recv_loop, daemon=True, name="CameraUDP")
        self.thread.start()
        print(f"[Camera] ✅ UDP 监听线程已启动 :{CAMERA_PORT}")

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
        """暂停 UDP 接收（关闭视频流时节省带宽和 CPU）"""
        self.paused = True
        print("[Camera] ⏸️ UDP 接收已暂停")

    def resume(self):
        """恢复 UDP 接收"""
        self.paused = False
        print("[Camera] ▶️ UDP 接收已恢复")

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
                }
            cache = self.frame_cache[frame_id]
            if chunk_idx not in cache["received"]:
                cache["chunks"][chunk_idx] = jpeg_chunk
                cache["received"].add(chunk_idx)

            # 收集完整帧
            completed_fids = [
                fid for fid, c in self.frame_cache.items()
                if len(c["received"]) == c["total"]
            ]
            for fid in completed_fids:
                cache_entry = self.frame_cache.pop(fid)
                jpeg_data = b"".join(cache_entry["chunks"])
                self.latest_jpeg = jpeg_data

                # 解码为 numpy array (需要 cv2)
                if self.cv2_ok:
                    import numpy as np
                    arr = np.frombuffer(jpeg_data, dtype=np.uint8)
                    frame = self._cv2.imdecode(arr, self._cv2.IMREAD_COLOR)
                    if frame is not None:
                        self.latest_frame = frame

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
                self.timeout_count += 1
                print(f"[Camera] ⚠️ 帧 {fid} 超时丢弃 | 已收 {len(c['received'])}/{c['total']} 分片")

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
        当 stream_enabled=False 时只输出占位图。
        """
        boundary = "--frameboundary"
        interval = 1.0 / MJPEG_FPS_LIMIT

        while self.running:
            t0 = time.time()
            if self.stream_enabled:
                jpeg_data = self.latest_jpeg or self.placeholder_jpeg
            else:
                jpeg_data = self.placeholder_jpeg
            yield (
                f"{boundary}\r\n"
                f"Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(jpeg_data)}\r\n"
                f"\r\n"
            ).encode("ascii") + jpeg_data + b"\r\n"

            # 帧率限速
            elapsed = time.time() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)


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
