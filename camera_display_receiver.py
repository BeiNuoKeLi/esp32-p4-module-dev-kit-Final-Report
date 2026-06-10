#!/usr/bin/env python3
"""
⚠️ 已弃用 (DEPRECATED) — 请勿使用 ⚠️

本模块原先用于配合 camera_capture_sender.py 在 PC 端接收并显示摄像头画面。
当前视频流已改为浏览器端 MJPEG 直接显示（GET /api/camera/mjpeg），
不再需要本模块。

本文件仅保留作为历史参考，不再参与系统运行。
"""

# ====== 以下为原始代码（已弃用） ======
"""
原始文档:
UDP 图片接收 + OpenCV 实时显示端

功能：
  1. 监听 UDP 8082 端口，接收 camera_capture_sender 发出的图片分片
  2. 按 FrameID + ChunkIdx 重组分片为完整 JPEG
  3. OpenCV 解码 JPEG → imshow 实时显示
  4. 覆盖 FPS 计数器 + 接收状态信息
  5. 自动清理超时/不完整的旧帧

用法:
  python camera_display_receiver.py [--port 8082] [--window "Camera Stream"]
"""

import socket
import signal
import sys
import time
import argparse
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import cv2
import numpy as np
import camera_protocol as proto


# === 默认配置 ===
DEFAULT_LISTEN_PORT = 8082    # 监听端口
DEFAULT_WINDOW_NAME = "Camera Stream - UDP Receiver"  # 显示窗口标题
BUFFER_SIZE = 65536           # UDP 接收缓冲区 (加大, 避免丢包)
SOCK_TIMEOUT = 0.002           # Socket 超时 (2ms, 高频轮询)
FRAME_TIMEOUT = 0.3             # 帧超时秒数 (10fps 下约 100ms/帧)


class CameraStreamReceiver:
    """UDP 图片流接收 + OpenCV 显示"""

    def __init__(self, listen_port: int = DEFAULT_LISTEN_PORT,
                 window_name: str = DEFAULT_WINDOW_NAME):
        self.listen_port = listen_port
        self.window_name = window_name
        self.sock: socket.socket | None = None
        self.running = True

        # 帧重组缓存: {frame_id: {'chunks': [bytes], 'total': int, 'received': set, 'start_time': float}}
        self.frame_cache: OrderedDict[int, dict] = OrderedDict()

        # 统计
        self.total_frames_displayed = 0
        self.total_frames_lost = 0
        self.fps_history = []       # 最近 N 帧的间隔

    def start(self) -> None:
        """启动 UDP 监听 + 显示主循环"""
        # 1. 创建 UDP socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)  # 1MB 接收缓冲
        self.sock.bind(("0.0.0.0", self.listen_port))
        # 设置超时, 以便定期检查 running 标志和清理缓存
        self.sock.settimeout(SOCK_TIMEOUT)

        self._print_banner()

        # 2. 创建 OpenCV 显示窗口
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.window_name, 640, 360)

        # 显示初始提示画面
        placeholder = np.zeros((360, 640, 3), dtype=np.uint8)
        cv2.putText(placeholder, "Waiting for camera stream...",
                    (50, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.putText(placeholder, f"Listening on UDP 0.0.0.0:{self.listen_port}",
                    (50, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
        cv2.imshow(self.window_name, placeholder)
        cv2.waitKey(1)

        try:
            while self.running:
                # 3. 接收 UDP 数据包
                try:
                    data, addr = self.sock.recvfrom(BUFFER_SIZE)
                    self._handle_packet(data, addr)
                except socket.timeout:
                    pass  # 正常, 继续循环

                # 4. 清理超时的旧帧
                self._cleanup_stale_frames()

                # 5. 检查是否有完整帧可以显示
                self._try_display()

                # 6. OpenCV 事件循环 (必须, 否则窗口无响应)
                key = cv2.waitKey(1) & 0xFF
                if key == 27:  # ESC
                    print("[信息] ESC 按下, 退出...")
                    break
                elif key == ord('q'):
                    print("[信息] q 按下, 退出...")
                    break

        except KeyboardInterrupt:
            print("\n正在关闭...")
        finally:
            self._cleanup()

    def _handle_packet(self, data: bytes, addr: tuple) -> None:
        """处理收到的 UDP 数据包"""
        # 解码协议头
        result = proto.unpack_header(data)
        if result is None:
            # Magic 不匹配, 可能是非图片数据 (静默丢弃)
            return

        frame_id, chunk_idx, total_chunks = result

        # 提取 JPEG 数据
        jpeg_chunk = data[proto.HEADER_SIZE:]

        # 初始化或更新帧缓存
        if frame_id not in self.frame_cache:
            # 新帧到来, 淘汰最旧的帧 (最多缓存 3 帧, 防止延迟累积)
            if len(self.frame_cache) >= 3:
                old_fid = next(iter(self.frame_cache))
                self.frame_cache.pop(old_fid)
                self.total_frames_lost += 1

            self.frame_cache[frame_id] = {
                'chunks': [b''] * total_chunks,
                'total': total_chunks,
                'received': set(),
                'start_time': time.time(),
            }

        cache = self.frame_cache[frame_id]

        # 存储分片 (忽略重复包)
        if chunk_idx not in cache['received']:
            cache['chunks'][chunk_idx] = jpeg_chunk
            cache['received'].add(chunk_idx)

    def _try_display(self) -> None:
        """检查并显示已完成的帧 (每次只显示一帧以保持 UI 响应)"""
        # 找到第一个完整帧 (不修改 dict 以避免迭代异常)
        completed_fid = None
        for fid, cache in self.frame_cache.items():
            if len(cache['received']) == cache['total']:
                completed_fid = fid
                break

        if completed_fid is None:
            return

        cache = self.frame_cache.pop(completed_fid)

        # 拼接所有分片
        jpeg_data = b''.join(cache['chunks'])

        # JPEG 解码
        img_array = np.frombuffer(jpeg_data, dtype=np.uint8)
        frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        if frame is None:
            print(f"[警告] 帧 #{completed_fid} JPEG 解码失败 ({len(jpeg_data)} bytes)")
            self.total_frames_lost += 1
            return

        # FPS 计算
        now = time.time()
        self.fps_history.append(now)
        if len(self.fps_history) > 30:
            self.fps_history.pop(0)

        fps = 0.0
        if len(self.fps_history) >= 2:
            duration = self.fps_history[-1] - self.fps_history[0]
            fps = (len(self.fps_history) - 1) / duration if duration > 0 else 0.0

        # 叠加信息
        display_frame = frame.copy()
        h, w = display_frame.shape[:2]

        # 半透明信息栏背景
        overlay = display_frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 60), (0, 0, 0), -1)
        display_frame = cv2.addWeighted(overlay, 0.4, display_frame, 0.6, 0)

        # 显示 FPS
        cv2.putText(display_frame, f"FPS: {fps:.1f}",
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        # 显示分辨率和帧序号
        cv2.putText(display_frame, f"{w}x{h} | Frame #{completed_fid}",
                    (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        # 显示接收统计
        cv2.putText(display_frame,
                    f"Recv: {self.total_frames_displayed} | Lost: {self.total_frames_lost}",
                    (w - 300, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        # 分片数
        cv2.putText(display_frame, f"Chunks: {cache['total']} | {len(jpeg_data)}B",
                    (w - 300, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        # 显示
        cv2.imshow(self.window_name, display_frame)
        self.total_frames_displayed += 1

    def _cleanup_stale_frames(self) -> None:
        """清理超时的未完成帧"""
        now = time.time()
        stale_ids = []

        for fid, cache in self.frame_cache.items():
            if now - cache['start_time'] > FRAME_TIMEOUT:
                stale_ids.append(fid)

        for fid in stale_ids:
            cache = self.frame_cache[fid]
            missed = cache['total'] - len(cache['received'])
            print(f"[警告] 帧 #{fid} 超时丢弃 (收到 {len(cache['received'])}/{cache['total']}, 丢 {missed} 包)")
            self.total_frames_lost += 1
            del self.frame_cache[fid]

    def _print_banner(self) -> None:
        print("=" * 55)
        print("  摄像头 UDP 图像流接收 + 实时显示")
        print(f"  监听端口: UDP 0.0.0.0:{self.listen_port}")
        print(f"  显示窗口: '{self.window_name}'")
        print("  操作: ESC/q 退出")
        print("=" * 55)
        print("  等待摄像头数据...\n")

    def _cleanup(self) -> None:
        if self.sock:
            self.sock.close()
        cv2.destroyAllWindows()
        print(f"\n[信息] 接收器已关闭。")
        print(f"[统计] 显示: {self.total_frames_displayed} 帧, 丢失: {self.total_frames_lost} 帧")


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP 图片流接收 + OpenCV 实时显示")
    parser.add_argument("--port", type=int, default=DEFAULT_LISTEN_PORT,
                        help=f"监听 UDP 端口 (默认: {DEFAULT_LISTEN_PORT})")
    parser.add_argument("--window", type=str, default=DEFAULT_WINDOW_NAME,
                        help=f"显示窗口标题 (默认: '{DEFAULT_WINDOW_NAME}')")
    args = parser.parse_args()

    receiver = CameraStreamReceiver(listen_port=args.port, window_name=args.window)

    def signal_handler(sig, frame):
        receiver.running = False
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    receiver.start()


if __name__ == "__main__":
    main()
