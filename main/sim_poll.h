/**
 * @file sim_poll.h
 * @brief 仿真命令 + 报警配置轮询 — ESP32 HTTP GET 反转通信方向
 *
 * 背景:
 *   VPS (公网) 无法直接 UDP 发送到 ESP32 (内网 10.x.x.x)。
 *   本模块改为 ESP32 主动 HTTP GET 轮询 VPS，拉取：
 *     1. 仿真注入命令    (/api/sim/poll)
 *     2. 报警配置命令    (/api/alarm/config/poll)
 *
 * 轮询间隔: 3 秒 (仿真注入) + 10 秒 (报警配置)
 *
 * seq 机制:
 *   ESP32 每次轮询带上上次收到的 seq, VPS 仅在 seq 更大时返回新命令,
 *   避免重复执行同一命令。
 */

#ifndef SIM_POLL_H
#define SIM_POLL_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief 仿真命令 + 报警配置 HTTP 轮询任务入口
 *
 * 同时轮询两个 VPS 端点：
 *   - GET /api/sim/poll?seq=N          → 仿真注入
 *   - GET /api/alarm/config/poll?seq=N → 报警配置
 *
 * @param arg 未使用 (NULL)
 */
void sim_poll_task(void *arg);

/**
 * @brief 将当前报警配置持久化到 NVS Flash
 *
 * 由 sim_poll_task 在收到报警配置命令后调用。
 * 定义在 smart_monitor_main.c 中。
 */
void save_alarm_config_to_nvs(void);

#ifdef __cplusplus
}
#endif

#endif /* SIM_POLL_H */
