/**
 * @file camera_http_fetch.h
 * @brief HTTP 拉取 ESP32-CAM JPEG → UDP 分包转发到 PC
 *
 * 方案: ESP32-CAM (Arduino CameraWebServer) 已部署在局域网, 提供 HTTP /capture
 *       P4 通过 esp_http_client 拉取 JPEG, 再以 0xAA55 协议分包通过 UDP 发给 PC
 *
 * 数据流:
 *   ESP32-CAM (http://ip/capture) → HTTP GET → P4 → UDP(8082) → PC
 *
 * 协议: 复用 camera_protocol.py 的 0xAA55 分包协议
 *   Header (8B): Magic 0xAA55 | FrameID(BE) | ChunkIdx(BE) | TotalChunks(BE)
 *   Payload:     JPEG data, max 4096 bytes/packet
 *
 * 端口: 8082 (与 camera_display_receiver.py 兼容)
 * 依赖: esp_http_client (IDF 内置组件)
 */

#ifndef CAMERA_HTTP_FETCH_H
#define CAMERA_HTTP_FETCH_H

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief HTTP 拉图 + UDP 转发任务入口
 *
 * 流程:
 *   1. 等待 Wi-Fi 连接就绪
 *   2. 创建 UDP socket → 指向 PC (port 8082)
 *   3. 分配 JPEG 下载缓冲 (PSRAM)
 *   4. 循环: HTTP GET /capture → 读 JPEG → 分包 → sendto()
 *   5. 帧率控制 (由 CONFIG_CAMERA_HTTP_FPS 决定)
 *
 * @param arg 未使用 (NULL)
 */
void camera_http_fetch_task(void *arg);

#ifdef __cplusplus
}
#endif

#endif /* CAMERA_HTTP_FETCH_H */
