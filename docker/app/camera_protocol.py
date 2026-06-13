"""
摄像头 UDP 图片分包协议 — 共享模块

协议格式 (大端序, 与网络字节序一致, 方便后续 ESP32 C 移植):

  Byte 0-1:  Magic      0xAA55 (帧同步标识)
  Byte 2-3:  FrameID    uint16 big-endian, 帧序号 0-65535 循环
  Byte 4-5:  ChunkIdx   uint16 big-endian, 当前分片序号 从0开始
  Byte 6-7:  TotalChunks uint16 big-endian, 该帧总分片数
  Byte 8-:  JPEG Data  二进制数据, 最多 1016 字节/包

参考: REQUIREMENT.md 6.1 (UDP 单包 < 512 → 图片协议放大到 1024)
"""

import struct

# === 协议常量 ===
MAGIC_HIGH = 0xAA       # Magic 高字节
MAGIC_LOW = 0x55        # Magic 低字节
HEADER_SIZE = 8         # 协议头字节数
MAX_PAYLOAD = 4096      # 单包有效载荷 (local UDP 不限 MTU)


def pack_header(frame_id: int, chunk_idx: int, total_chunks: int) -> bytes:
    """
    编码 8 字节协议头

    Args:
        frame_id:     帧序号 (0-65535)
        chunk_idx:    当前分片序号 (0-based)
        total_chunks: 该帧总分片数

    Returns:
        8 字节 bytes 对象 (大端序)
    """
    return struct.pack('>HHHH',
                       (MAGIC_HIGH << 8) | MAGIC_LOW,
                       frame_id & 0xFFFF,
                       chunk_idx & 0xFFFF,
                       total_chunks & 0xFFFF)


def unpack_header(data: bytes) -> tuple | None:
    """
    解码并校验协议头

    Args:
        data: 至少 8 字节的原始数据

    Returns:
        (frame_id, chunk_idx, total_chunks) 或 None (Magic 不匹配/数据不足)
    """
    if len(data) < HEADER_SIZE:
        return None

    magic, frame_id, chunk_idx, total_chunks = struct.unpack('>HHHH', data[:HEADER_SIZE])

    # Magic 校验
    if magic != ((MAGIC_HIGH << 8) | MAGIC_LOW):
        return None

    return (frame_id, chunk_idx, total_chunks)
