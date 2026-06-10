#!/usr/bin/env python3
"""
⚠️ 已弃用 (DEPRECATED) — 请勿使用 ⚠️

本模块原先通过 USB UVC 摄像头采集图像，现已由 ESP32-CAM + ESP32-P4 方案替代。

当前摄像头数据流:
  ESP32-CAM (CameraWebServer) → HTTP GET /capture → ESP32-P4 (camera_http_fetch.c)
  → UDP 8082 (0xAA55 分片) → Docker camera_server.py 直收

本文件仅保留作为历史参考，不再参与系统运行。
"""

# ====== 以下为原始代码（已弃用） ======
"""
原始文档:
摄像头图像采集 + UDP 分包发送端

功能：
  1. 通过 OpenCV 读取 USB UVC 摄像头 (KYT-U400)
  2. 640x480 分辨率捕获
  3. JPEG 编码 (quality=70)
  4. 按 camera_protocol 协议分包
  5. UDP 发送到目标地址 :8082
  6. Ctrl+C 优雅退出

通信参数：
  - 目标端口: 8082 (图片专用, 不与 8080 传感器数据 / 8081 仿真命令冲突)
  - 传输方式: UDP (SOCK_DGRAM)
  - 单包大小: 1024 字节 (8 header + 1016 payload)
  - 帧率: 约 2-4 fps (帧间隔 0.3-0.5s)

用法:
  python camera_capture_sender.py [--camera 0] [--ip 127.0.0.1] [--port 8082] [--fps 3]
"""

import socket
import signal
import sys
import time
import argparse
from pathlib import Path

# 将当前目录加入 path，以便导入 camera_protocol
sys.path.insert(0, str(Path(__file__).parent))

import cv2
import numpy as np
import camera_protocol as proto


# === 默认配置 ===
DEFAULT_CAMERA_ID = 1          # USB 摄像头设备 ID (通常是 0)
DEFAULT_TARGET_IP = "127.0.0.1"  # 目标地址 (本机回环, 模拟传输; ESP32 上线后改为 10.16.234.215)
DEFAULT_TARGET_PORT = 8082     # 图片 UDP 端口
DEFAULT_FPS = 10               # 目标帧率 (UDP 流传输建议 10fps)
FRAME_WIDTH = 640              # 捕获分辨率宽度
FRAME_HEIGHT = 360             # 捕获分辨率高度
JPEG_QUALITY = 80              # JPEG 压缩质量 (提高减少块效应, 画面更锐利)
GAMMA = 0.55                    # Gamma 校正值 (<1 提亮暗部, 不过度拉伸避免噪点)
SHARPEN_STRENGTH = 0.2          # 锐化强度 (硬件已清晰, 轻补偿即可)

# ESP32 模式下的远程 IP (切换时使用)
ESP32_TARGET_IP = "10.16.234.215"


class CameraStreamSender:
    """摄像头流 UDP 发送器"""

    def __init__(self, camera_id: int = DEFAULT_CAMERA_ID,
                 target_ip: str = DEFAULT_TARGET_IP,
                 target_port: int = DEFAULT_TARGET_PORT,
                 target_fps: int = DEFAULT_FPS):
        self.camera_id = camera_id
        self.target_ip = target_ip
        self.target_port = target_port
        self.target_fps = target_fps
        self.frame_interval = 1.0 / max(target_fps, 1)

        self.cap: cv2.VideoCapture | None = None
        self.sock: socket.socket | None = None
        self.running = True
        self.frame_count = 0

    def start(self) -> None:
        """启动摄像头捕获 + UDP 发送主循环"""
        # 1. 打开摄像头 (DirectShow 后端, 兼容工业相机)
        self.cap = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            # 回退到默认后端
            print(f"[信息] DSHOW 后端失败, 尝试默认后端...")
            self.cap = cv2.VideoCapture(self.camera_id)
        if not self.cap.isOpened():
            print(f"[错误] 无法打开摄像头 (ID={self.camera_id})")
            print("  请检查: 1) 摄像头是否连接 2) 设备 ID 是否正确 3) 是否被其他程序占用")
            return

        # 设置 MJPG 格式 (高帧率相机原生格式)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        # 设置分辨率
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        # 设置相机内部帧率, 降低缓冲区
        self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # 不覆盖 VideoCapture 已调好的参数 (焦点/曝光/增益等)
        # OpenCV 的 cap.set 对 UVC 扩展控制兼容性差, 会覆盖独立控制面板设定

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = ''.join(chr((fourcc >> i) & 0xFF) for i in range(0, 32, 8)).strip('\x00')
        print(f"[信息] 摄像头已打开: {actual_w}x{actual_h} | FOURCC={fourcc_str}")
        print(f"[信息] 软件处理: gamma={GAMMA} sharpen={SHARPEN_STRENGTH} jpeg_q={JPEG_QUALITY}")

        # 2. 创建 UDP socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        print(f"[信息] UDP 发送目标: {self.target_ip}:{self.target_port}")
        print(f"[信息] 目标帧率: {self.target_fps} fps, 帧间隔: {self.frame_interval:.2f}s")
        print(f"[信息] JPEG 质量: {JPEG_QUALITY}, 单包载荷: {proto.MAX_PAYLOAD} 字节")
        print("=" * 50)
        print("  运行中... 按 Ctrl+C 停止\n")

        frame_seq = 0
        last_send = time.perf_counter()

        try:
            while self.running:
                # 帧率控制: 精确等待到帧间隔结束
                now = time.perf_counter()
                elapsed = now - last_send
                if elapsed < self.frame_interval:
                    time.sleep(self.frame_interval - elapsed)
                last_send = time.perf_counter()

                # 3. 捕获一帧
                ret, frame = self.cap.read()
                if not ret:
                    print("[警告] 捕获帧失败, 重试...")
                    time.sleep(0.01)
                    continue

                # 3.5 Gamma 校正: 提亮暗部 (工业摄像头采光不足的软件补偿)
                # 查表法, 零分支, 高性能
                if not hasattr(self, '_gamma_lut'):
                    self._gamma_lut = np.array(
                        [((i / 255.0) ** GAMMA) * 255.0 for i in range(256)],
                        dtype=np.uint8
                    )
                frame = cv2.LUT(frame, self._gamma_lut)

                # 3.6 锐化: 补偿对焦模糊 (filter2D unsharp kernel)
                if SHARPEN_STRENGTH > 0:
                    if not hasattr(self, '_sharpen_kernel'):
                        s = SHARPEN_STRENGTH
                        self._sharpen_kernel = np.array([
                            [      0,     -s,       0],
                            [     -s, 1+4*s,      -s],
                            [      0,     -s,       0]
                        ], dtype=np.float32)
                    frame = cv2.filter2D(frame, -1, self._sharpen_kernel)

                # 4. JPEG 编码
                success, jpeg_bytes = cv2.imencode('.jpg', frame,
                                                   [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if not success:
                    print("[警告] JPEG 编码失败")
                    continue

                jpeg_data = jpeg_bytes.tobytes()
                total_size = len(jpeg_data)

                # 5. 分片发送
                total_chunks = (total_size + proto.MAX_PAYLOAD - 1) // proto.MAX_PAYLOAD
                frame_id = frame_seq & 0xFFFF

                for chunk_idx in range(total_chunks):
                    start = chunk_idx * proto.MAX_PAYLOAD
                    end = min(start + proto.MAX_PAYLOAD, total_size)
                    chunk_data = jpeg_data[start:end]

                    header = proto.pack_header(frame_id, chunk_idx, total_chunks)
                    packet = header + chunk_data

                    self.sock.sendto(packet, (self.target_ip, self.target_port))

                # 进度打印 (每 30 帧打印一次)
                if frame_seq % 30 == 0:
                    print(f"[#{frame_seq:04d}] 已发送 | "
                          f"{FRAME_WIDTH}x{FRAME_HEIGHT} | "
                          f"JPEG {total_size}B → {total_chunks} 包")

                frame_seq += 1
                self.frame_count += 1

        except KeyboardInterrupt:
            print("\n正在关闭...")
        finally:
            self._cleanup()

    def stop(self) -> None:
        """停止发送器 (由信号触发)"""
        self.running = False

    def _cleanup(self) -> None:
        """清理资源"""
        if self.cap and self.cap.isOpened():
            self.cap.release()
        if self.sock:
            self.sock.close()
        print(f"[信息] 已停止。共发送 {self.frame_count} 帧。")


def main() -> None:
    parser = argparse.ArgumentParser(description="摄像头 UDP 图像流发送端")
    parser.add_argument("--camera", type=int, default=DEFAULT_CAMERA_ID,
                        help=f"摄像头设备 ID (默认: {DEFAULT_CAMERA_ID})")
    parser.add_argument("--ip", type=str, default=DEFAULT_TARGET_IP,
                        help=f"目标 IP 地址 (默认: {DEFAULT_TARGET_IP})")
    parser.add_argument("--port", type=int, default=DEFAULT_TARGET_PORT,
                        help=f"目标 UDP 端口 (默认: {DEFAULT_TARGET_PORT})")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS,
                        help=f"目标帧率 (默认: {DEFAULT_FPS})")
    args = parser.parse_args()

    sender = CameraStreamSender(
        camera_id=args.camera,
        target_ip=args.ip,
        target_port=args.port,
        target_fps=args.fps
    )

    # 信号处理
    def signal_handler(sig, frame):
        sender.stop()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    sender.start()


if __name__ == "__main__":
    main()
