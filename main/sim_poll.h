/**
 * @file sim_poll.h
 * @brief 仿真命令轮询 — ESP32 HTTP GET /api/sim/poll 反转通信方向
 *
 * 背景:
 *   VPS (公网) 无法直接 UDP 发送到 ESP32 (内网 10.x.x.x)。
 *   本模块改为 ESP32 主动 HTTP GET 轮询 VPS，拉取待执行的仿真注入命令。
 *
 * 轮询间隔: 3 秒
 * 端点:     http://<VPS_IP>:8001/api/sim/poll?seq=<last_seq>
 *
 * 命令格式 (JSON):
 *   {"seq": N, "data": {"dht11_t":39,"dht11_h":60,...}}   — 注入命令
 *   {"seq": N, "data": {"cmd":"reset"}}                    — 恢复真实传感器
 *   {"seq": N, "data": null}                               — 无新命令
 *
 * seq 机制:
 *   ESP32 每次轮询带上上次收到的 seq, VPS 仅在 seq 更大时返回新命令,
 *   避免重复执行同一命令。
 */

#ifndef SIM_POLL_H
#define SIM_POLL_H

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief 仿真命令 HTTP 轮询任务入口
 *
 * 流程:
 *   1. 等待 Wi-Fi 连接就绪
 *   2. 循环: HTTP GET /api/sim/poll → 解析 JSON → 应用仿真值
 *   3. 间隔 3 秒
 *
 * @param arg 未使用 (NULL)
 */
void sim_poll_task(void *arg);

#ifdef __cplusplus
}
#endif

#endif /* SIM_POLL_H */
