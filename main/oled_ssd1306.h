/**
 * @file oled_ssd1306.h
 * @brief 0.96寸 4针 OLED (SSD1306) I2C 驱动头文件
 *
 * 硬件接线（REQUIREMENT.md 3.1）：
 *   - SDA → GPIO7, SCL → GPIO8 (I2C 保留)
 *   - VCC → 3.3V, GND → GND
 *
 * 芯片特性（SSD1306 数据手册）：
 *   - 分辨率: 128x64 像素
 *   - I2C 地址: 0x3C (默认, SA0=GND)
 *   - 通信频率: 400KHz (标准模式)
 */

#ifndef OLED_SSD1306_H
#define OLED_SSD1306_H

#include "esp_err.h"

/* ==================== OLED 引脚定义 ==================== */
#define OLED_I2C_SDA_GPIO     7       /*!< SDA → GPIO7 (I2C 保留, REQUIREMENT.md 3.1) */
#define OLED_I2C_SCL_GPIO     8       /*!< SCL → GPIO8 (I2C 保留, REQUIREMENT.md 3.1) */
#define OLED_I2C_ADDR         0x3C    /*!< 7位 I2C 地址 (SA0=GND 时默认) */
#define OLED_I2C_PORT         I2C_NUM_0

/* ==================== OLED 屏幕尺寸 ==================== */
#define OLED_WIDTH            128
#define OLED_HEIGHT           64

/* ==================== 字号 ==================== */
#define OLED_CHAR_W           6       /*!< 字符宽度 (像素) */
#define OLED_CHAR_H           8       /*!< 字符高度 (像素) */
#define OLED_CHARS_PER_LINE   (OLED_WIDTH / OLED_CHAR_W)   /* 每行字符数: 21 */
#define OLED_MAX_LINES        (OLED_HEIGHT / OLED_CHAR_H)  /* 最大行数: 8 */

/* ==================== 公共函数 ==================== */

/**
 * @brief 初始化 OLED 显示屏 (I2C + SSD1306 初始化序列)
 *
 * - 安装 I2C 主设备驱动 (GPIO7/GPIO8, 400KHz)
 * - 发送 SSD1306 初始化命令序列
 * - 清屏
 *
 * @return ESP_OK 成功, 其他值失败
 */
esp_err_t oled_init(void);

/**
 * @brief 清屏 (填充 0x00)
 *
 * @return ESP_OK 成功
 */
esp_err_t oled_clear(void);

/**
 * @brief 在指定行显示字符串
 *
 * 超出屏幕宽度部分自动截断。自动将 framebuffer 刷新到屏幕。
 *
 * @param[in] line  行号 (0~OLED_MAX_LINES-1), 每行8像素高
 * @param[in] text  要显示的字符串 (ASCII)
 * @return ESP_OK 成功
 */
esp_err_t oled_show_string(uint8_t line, const char *text);

/**
 * @brief 在指定位置显示格式化字符串 (类似 printf)
 *
 * @param[in] line  行号 (0~OLED_MAX_LINES-1)
 * @param[in] format 格式化字符串 + 参数 (同 printf)
 * @return ESP_OK 成功
 */
esp_err_t oled_show_line(uint8_t line, const char *format, ...) __attribute__((format(printf, 2, 3)));

#endif /* OLED_SSD1306_H */
