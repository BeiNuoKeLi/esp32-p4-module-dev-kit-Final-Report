/**
 * @file udp_sender.h
 * @brief UDP 传感器数据发送任务声明
 *
 * 功能（REQUIREMENT.md 第6节）：
 *   - 每2秒读取 g_sensor_data（持锁）
 *   - snprintf() 构建 JSON（REQUIREMENT.md 6.3）
 *   - 通过 UDP socket 发送到 VPS:8002 或本地上位机:8080（UDP_TARGET_IP / UDP_TARGET_PORT）
 *
 * 任务参数（REQUIREMENT.md 5.2）：
 *   - 优先级: 2
 *   - 栈大小: 5120
 *   - 绑定核心: Core 1
 */

#ifndef UDP_SENDER_H
#define UDP_SENDER_H

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief UDP 传感器数据发送任务入口 (v2.0 分级报警)
 *
 * 流程：
 *   1. 等待 5 秒确保 Wi-Fi 连接就绪
 *   2. 创建 UDP socket (AF_INET, SOCK_DGRAM)
 *   3. 循环：持锁读传感器数据 → 计算分级报警源 → 构建 JSON → sendto() → 等2秒
 *
 * JSON 格式 v2.0: {"type":"data","level":0,"ts":...,"dht11_t":...,...,"reason":"..."}
 *
 * @param arg 未使用（NULL）
 */
void udp_sender_task(void *arg);

#ifdef __cplusplus
}
#endif

#endif /* UDP_SENDER_H */
