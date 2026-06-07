/**
 * @file oled_ssd1306.c
 * @brief 0.96寸 4针 OLED (SSD1306) I2C 驱动实现
 *
 * 硬件:
 *   - SDA → GPIO7, SCL → GPIO8 (I2C 保留)
 *   - 芯片: SSD1306, 分辨率 128x64
 *   - I2C 地址: 0x3C (SA0=GND)
 *
 * 驱动原理:
 *   - 使用 128x64/8 = 1024 字节全屏 framebuffer
 *   - 每次修改后整屏刷新 (write-only, 简单可靠)
 *   - 字符集: 6x8 像素 ASCII 可打印字符 (0x20~0x7E)
 */

#include "oled_ssd1306.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include <string.h>
#include <stdarg.h>
#include <stdio.h>

static const char *TAG = "oled";

/* I2C 主设备句柄 */
static i2c_master_bus_handle_t s_i2c_bus = NULL;
static i2c_master_dev_handle_t s_i2c_dev = NULL;

/* 全屏 framebuffer: 128 列 x 8 行 = 1024 字节 */
static uint8_t s_fb[OLED_WIDTH * (OLED_HEIGHT / 8)];

/* ==================== 6x8 ASCII 字库 (0x20~0x7E, 95 字符) ==================== */
static const uint8_t g_font_6x8[][6] = {
    {0x00,0x00,0x00,0x00,0x00,0x00}, /* space */
    {0x00,0x00,0x5F,0x00,0x00,0x00}, /* ! */
    {0x00,0x07,0x00,0x07,0x00,0x00}, /* " */
    {0x14,0x7F,0x14,0x7F,0x14,0x00}, /* # */
    {0x24,0x2A,0x7F,0x2A,0x12,0x00}, /* $ */
    {0x23,0x13,0x08,0x64,0x62,0x00}, /* % */
    {0x36,0x49,0x55,0x22,0x50,0x00}, /* & */
    {0x00,0x05,0x03,0x00,0x00,0x00}, /* ' */
    {0x00,0x1C,0x22,0x41,0x00,0x00}, /* ( */
    {0x00,0x41,0x22,0x1C,0x00,0x00}, /* ) */
    {0x08,0x2A,0x1C,0x2A,0x08,0x00}, /* * */
    {0x08,0x08,0x3E,0x08,0x08,0x00}, /* + */
    {0x00,0x50,0x30,0x00,0x00,0x00}, /* , */
    {0x08,0x08,0x08,0x08,0x08,0x00}, /* - */
    {0x00,0x60,0x60,0x00,0x00,0x00}, /* . */
    {0x20,0x10,0x08,0x04,0x02,0x00}, /* / */
    {0x3E,0x51,0x49,0x45,0x3E,0x00}, /* 0 */
    {0x00,0x42,0x7F,0x40,0x00,0x00}, /* 1 */
    {0x42,0x61,0x51,0x49,0x46,0x00}, /* 2 */
    {0x21,0x41,0x45,0x4B,0x31,0x00}, /* 3 */
    {0x18,0x14,0x12,0x7F,0x10,0x00}, /* 4 */
    {0x27,0x45,0x45,0x45,0x39,0x00}, /* 5 */
    {0x3C,0x4A,0x49,0x49,0x30,0x00}, /* 6 */
    {0x01,0x71,0x09,0x05,0x03,0x00}, /* 7 */
    {0x36,0x49,0x49,0x49,0x36,0x00}, /* 8 */
    {0x06,0x49,0x49,0x29,0x1E,0x00}, /* 9 */
    {0x00,0x36,0x36,0x00,0x00,0x00}, /* : */
    {0x00,0x56,0x36,0x00,0x00,0x00}, /* ; */
    {0x00,0x08,0x14,0x22,0x41,0x00}, /* < */
    {0x14,0x14,0x14,0x14,0x14,0x00}, /* = */
    {0x41,0x22,0x14,0x08,0x00,0x00}, /* > */
    {0x02,0x01,0x51,0x09,0x06,0x00}, /* ? */
    {0x32,0x49,0x79,0x41,0x3E,0x00}, /* @ */
    {0x7E,0x11,0x11,0x11,0x7E,0x00}, /* A */
    {0x7F,0x49,0x49,0x49,0x36,0x00}, /* B */
    {0x3E,0x41,0x41,0x41,0x22,0x00}, /* C */
    {0x7F,0x41,0x41,0x22,0x1C,0x00}, /* D */
    {0x7F,0x49,0x49,0x49,0x41,0x00}, /* E */
    {0x7F,0x09,0x09,0x01,0x01,0x00}, /* F */
    {0x3E,0x41,0x41,0x51,0x32,0x00}, /* G */
    {0x7F,0x08,0x08,0x08,0x7F,0x00}, /* H */
    {0x00,0x41,0x7F,0x41,0x00,0x00}, /* I */
    {0x20,0x40,0x41,0x3F,0x01,0x00}, /* J */
    {0x7F,0x08,0x14,0x22,0x41,0x00}, /* K */
    {0x7F,0x40,0x40,0x40,0x40,0x00}, /* L */
    {0x7F,0x02,0x04,0x02,0x7F,0x00}, /* M */
    {0x7F,0x04,0x08,0x10,0x7F,0x00}, /* N */
    {0x3E,0x41,0x41,0x41,0x3E,0x00}, /* O */
    {0x7F,0x09,0x09,0x09,0x06,0x00}, /* P */
    {0x3E,0x41,0x51,0x21,0x5E,0x00}, /* Q */
    {0x7F,0x09,0x19,0x29,0x46,0x00}, /* R */
    {0x46,0x49,0x49,0x49,0x31,0x00}, /* S */
    {0x01,0x01,0x7F,0x01,0x01,0x00}, /* T */
    {0x3F,0x40,0x40,0x40,0x3F,0x00}, /* U */
    {0x1F,0x20,0x40,0x20,0x1F,0x00}, /* V */
    {0x7F,0x20,0x18,0x20,0x7F,0x00}, /* W */
    {0x63,0x14,0x08,0x14,0x63,0x00}, /* X */
    {0x03,0x04,0x78,0x04,0x03,0x00}, /* Y */
    {0x61,0x51,0x49,0x45,0x43,0x00}, /* Z */
    {0x00,0x00,0x7F,0x41,0x41,0x00}, /* [ */
    {0x02,0x04,0x08,0x10,0x20,0x00}, /* \ */
    {0x41,0x41,0x7F,0x00,0x00,0x00}, /* ] */
    {0x04,0x02,0x01,0x02,0x04,0x00}, /* ^ */
    {0x40,0x40,0x40,0x40,0x40,0x00}, /* _ */
    {0x00,0x01,0x02,0x04,0x00,0x00}, /* ` */
    {0x20,0x54,0x54,0x54,0x78,0x00}, /* a */
    {0x7F,0x48,0x44,0x44,0x38,0x00}, /* b */
    {0x38,0x44,0x44,0x44,0x20,0x00}, /* c */
    {0x38,0x44,0x44,0x48,0x7F,0x00}, /* d */
    {0x38,0x54,0x54,0x54,0x18,0x00}, /* e */
    {0x08,0x7E,0x09,0x01,0x02,0x00}, /* f */
    {0x08,0x14,0x54,0x54,0x3C,0x00}, /* g */
    {0x7F,0x08,0x04,0x04,0x78,0x00}, /* h */
    {0x00,0x44,0x7D,0x40,0x00,0x00}, /* i */
    {0x20,0x40,0x44,0x3D,0x00,0x00}, /* j */
    {0x00,0x7F,0x10,0x28,0x44,0x00}, /* k */
    {0x00,0x41,0x7F,0x40,0x00,0x00}, /* l */
    {0x7C,0x04,0x18,0x04,0x78,0x00}, /* m */
    {0x7C,0x08,0x04,0x04,0x78,0x00}, /* n */
    {0x38,0x44,0x44,0x44,0x38,0x00}, /* o */
    {0x7C,0x14,0x14,0x14,0x08,0x00}, /* p */
    {0x08,0x14,0x14,0x18,0x7C,0x00}, /* q */
    {0x7C,0x08,0x04,0x04,0x08,0x00}, /* r */
    {0x48,0x54,0x54,0x54,0x20,0x00}, /* s */
    {0x04,0x3F,0x44,0x40,0x20,0x00}, /* t */
    {0x3C,0x40,0x40,0x20,0x7C,0x00}, /* u */
    {0x1C,0x20,0x40,0x20,0x1C,0x00}, /* v */
    {0x3C,0x40,0x30,0x40,0x3C,0x00}, /* w */
    {0x44,0x28,0x10,0x28,0x44,0x00}, /* x */
    {0x0C,0x50,0x50,0x50,0x3C,0x00}, /* y */
    {0x44,0x64,0x54,0x4C,0x44,0x00}, /* z */
    {0x00,0x08,0x36,0x41,0x00,0x00}, /* { */
    {0x00,0x00,0x7F,0x00,0x00,0x00}, /* | */
    {0x00,0x41,0x36,0x08,0x00,0x00}, /* } */
    {0x08,0x08,0x2A,0x1C,0x08,0x00}, /* ~ */
};

/* ==================== 内部函数 ==================== */

/**
 * @brief 发送 SSD1306 命令字节
 *
 * 控制字节: 0x00 表示命令
 */
static esp_err_t ssd1306_send_cmd(uint8_t cmd)
{
    uint8_t data[2] = { 0x00, cmd };  /* 0x00 = Co=0, D/C#=0 → 命令模式 */
    return i2c_master_transmit(s_i2c_dev, data, sizeof(data), pdMS_TO_TICKS(10));
}

/**
 * @brief 发送 SSD1306 数据字节
 *
 * 控制字节 0x40 必须和数据在同一个 I2C 事务中发送，
 * 否则 STOP 信号会重置 SSD1306 的 Co/D/C# 状态机。
 *
 * 调用方保证 len ≤ 128（单页），使用 129 字节栈缓冲区即可。
 */
static esp_err_t ssd1306_send_data(const uint8_t *data, size_t len)
{
    if (len > OLED_WIDTH) return ESP_ERR_INVALID_SIZE;

    uint8_t tx_buf[OLED_WIDTH + 1];  /* 128 + 1 = 129 字节, 栈安全 */
    tx_buf[0] = 0x40;                /* Co=0, D/C#=1 → 数据模式 */
    memcpy(tx_buf + 1, data, len);
    return i2c_master_transmit(s_i2c_dev, tx_buf, len + 1, pdMS_TO_TICKS(100));
}

/**
 * @brief 将 framebuffer 整屏刷新到 SSD1306
 *
 * 使用 Page Addressing Mode (0x02)，逐页发送 128 字节。
 * 避免一次性发送 1025 字节的单次 I2C 事务超出 ESP32 FIFO 能力，
 * 导致数据丢失和乱码。
 */
static esp_err_t ssd1306_refresh(void)
{
    esp_err_t ret;

    /* 切换到 Page Addressing Mode (自动回绕, 不需要设置列/页范围) */
    ret = ssd1306_send_cmd(0x20);  /* 内存寻址模式命令 */
    if (ret != ESP_OK) return ret;
    ret = ssd1306_send_cmd(0x02);  /* 页寻址模式 */
    if (ret != ESP_OK) return ret;

    /* 逐页刷新: 每页 128 字节, 8 次小 I2C 事务 */
    for (uint8_t page = 0; page < 8; page++) {
        /* 设置页地址 (0xB0~0xB7) */
        ret = ssd1306_send_cmd(0xB0 | page);
        if (ret != ESP_OK) return ret;

        /* 设置列地址 = 0 (低4位 0x00, 高4位 0x10) */
        ret = ssd1306_send_cmd(0x00);
        if (ret != ESP_OK) return ret;
        ret = ssd1306_send_cmd(0x10);
        if (ret != ESP_OK) return ret;

        /* 发送本页 128 字节 */
        ret = ssd1306_send_data(s_fb + page * OLED_WIDTH, OLED_WIDTH);
        if (ret != ESP_OK) return ret;
    }

    return ESP_OK;
}

/**
 * @brief 向 framebuffer 写入一个 6x8 字符
 *
 * @param x  列位置 (像素)
 * @param y  行位置 (像素, 0~63, 应为8的倍数)
 * @param ch ASCII 字符
 */
static void fb_write_char(uint8_t x, uint8_t y, char ch)
{
    if (ch < 0x20 || ch > 0x7E) ch = ' ';  /* 不可打印字符显示空格 */
    const uint8_t *glyph = g_font_6x8[ch - 0x20];

    for (uint8_t col = 0; col < OLED_CHAR_W; col++) {
        uint8_t fb_col = x + col;
        if (fb_col >= OLED_WIDTH) break;

        /* 每列对应 framebuffer 中 y/8 页 */
        uint8_t page = y / 8;
        uint8_t bit_offset = y % 8;
        uint16_t fb_idx = page * OLED_WIDTH + fb_col;

        uint8_t col_data = glyph[col];
        if (bit_offset != 0) {
            /* 跨页 (字符可能跨越两个 bank) */
            s_fb[fb_idx] &= ~(0xFF << bit_offset);
            s_fb[fb_idx] |= (col_data << bit_offset);
            if (page + 1 < OLED_HEIGHT / 8) {
                s_fb[fb_idx + OLED_WIDTH] &= ~(0xFF >> (8 - bit_offset));
                s_fb[fb_idx + OLED_WIDTH] |= (col_data >> (8 - bit_offset));
            }
        } else {
            /* 页对齐: 直接写入完整 8 位列数据 */
            s_fb[fb_idx] = col_data;
        }
    }
}

/**
 * @brief 向 framebuffer 写入一行字符串
 *
 * @param line 行号 (0~OLED_MAX_LINES-1)
 * @param text 字符串
 */
static void fb_write_string(uint8_t line, const char *text)
{
    uint8_t y = line * OLED_CHAR_H;
    uint8_t x = 0;
    while (*text && x + OLED_CHAR_W <= OLED_WIDTH) {
        fb_write_char(x, y, *text++);
        x += OLED_CHAR_W;
    }
    /* 填充行尾空白 */
    while (x < OLED_WIDTH) {
        fb_write_char(x, y, ' ');
        x += OLED_CHAR_W;
    }
}

/* ==================== I2C 设备探测 ==================== */

/**
 * @brief 使用 I2C 总线探测指定地址是否有设备响应
 *
 * 向目标地址发送一个空写事务，检查是否收到 ACK。
 *
 * @param[in] bus      I2C 主总线句柄
 * @param[in] dev_addr 7 位设备地址
 * @return ESP_OK 设备存在, ESP_ERR_NOT_FOUND 无设备应答
 */
static esp_err_t i2c_probe_addr(i2c_master_bus_handle_t bus, uint8_t dev_addr)
{
    /* 临时添加设备，用完后立即删除 */
    i2c_master_dev_handle_t probe_dev = NULL;
    i2c_device_config_t probe_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = dev_addr,
        .scl_speed_hz = 100000,
    };
    esp_err_t ret = i2c_master_bus_add_device(bus, &probe_cfg, &probe_dev);
    if (ret != ESP_OK) return ret;

    /* 发送 1 字节 0x00 探测设备 */
    ret = i2c_master_transmit(probe_dev, (uint8_t[]){0x00}, 1, pdMS_TO_TICKS(50));

    i2c_master_bus_rm_device(probe_dev);
    return ret;
}

/* ==================== 公共函数 ==================== */

esp_err_t oled_init(void)
{
    uint8_t i2c_addr = OLED_I2C_ADDR;

    ESP_LOGI(TAG, "初始化 OLED SSD1306 (SDA=GPIO%d, SCL=GPIO%d)...", OLED_I2C_SDA_GPIO, OLED_I2C_SCL_GPIO);

    /* ---- 1. 安装 I2C 主设备驱动 ---- */
    i2c_master_bus_config_t bus_cfg = {
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .i2c_port = OLED_I2C_PORT,
        .scl_io_num = OLED_I2C_SCL_GPIO,
        .sda_io_num = OLED_I2C_SDA_GPIO,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,   /* 启用内部上拉 (模块上拉可能不足) */
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_cfg, &s_i2c_bus), TAG, "I2C 总线初始化失败");

    /* ---- 2. 探测 I2C 设备地址 ---- */
    esp_err_t probe_ret = i2c_probe_addr(s_i2c_bus, i2c_addr);
    if (probe_ret != ESP_OK) {
        ESP_LOGW(TAG, "I2C 地址 0x%02X 无响应, 尝试 0x%02X...", i2c_addr, 0x3D);
        probe_ret = i2c_probe_addr(s_i2c_bus, 0x3D);
        if (probe_ret == ESP_OK) {
            i2c_addr = 0x3D;
            ESP_LOGI(TAG, "找到 OLED 设备, I2C 地址 = 0x%02X", i2c_addr);
        } else {
            ESP_LOGE(TAG, "I2C 总线上未检测到 SSD1306 设备 (0x3C/0x3D)");
            ESP_LOGE(TAG, "请检查: (1) 接线 SDA→GPIO7, SCL→GPIO8, VCC→3.3V, GND→GND");
            ESP_LOGE(TAG, "        (2) 是否缺少上拉电阻 (推荐 4.7KΩ SDA/SCL 各接一个到 3.3V)");
            return ESP_ERR_NOT_FOUND;
        }
    } else {
        ESP_LOGI(TAG, "找到 OLED 设备, I2C 地址 = 0x%02X", i2c_addr);
    }

    /* ---- 3. 添加 I2C 设备 ---- */
    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = i2c_addr,
        .scl_speed_hz = 100000,  /* SSD1306 标准 100KHz */
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(s_i2c_bus, &dev_cfg, &s_i2c_dev), TAG, "I2C 添加设备失败");

    /* ---- 4. SSD1306 初始化序列 (来自数据手册) ---- */
    /* 使用宏简化两字节命令发送 + 错误检查 */
    #define CMD1(c)       ESP_RETURN_ON_ERROR(ssd1306_send_cmd(c), TAG, "SSD1306 cmd 0x%02X 失败", c)
    #define CMD2(c1, c2)  do { \
        ESP_RETURN_ON_ERROR(ssd1306_send_cmd(c1), TAG, "SSD1306 cmd 0x%02X 失败", c1); \
        ESP_RETURN_ON_ERROR(ssd1306_send_cmd(c2), TAG, "SSD1306 cmd 0x%02X 失败", c2); \
    } while(0)

    /* 4a. 关闭显示 */
    CMD1(0xAE);

    /* 4b. 设置时钟分频/振荡器频率 */
    CMD2(0xD5, 0x80);

    /* 4c. 设置多路复用比 */
    CMD2(0xA8, 0x3F);  /* 64 */

    /* 4d. 设置显示偏移 */
    CMD2(0xD3, 0x00);

    /* 4e. 设置显示起始行 */
    CMD1(0x40);

    /* 4f. 设置段重映射 (列0映射到SEG0) */
    CMD1(0xA1);

    /* 4g. 设置 COM 输出扫描方向 */
    CMD1(0xC8);

    /* 4h. 设置 COM 引脚硬件配置 */
    CMD2(0xDA, 0x12);

    /* 4i. 设置对比度 */
    CMD2(0x81, 0xCF);

    /* 4j. 设置预充电周期 */
    CMD2(0xD9, 0xF1);

    /* 4k. 设置 VCOMH 取消选择级别 */
    CMD2(0xDB, 0x40);

    /* 4l. 整屏显示开启 (非部分显示) */
    CMD1(0xA4);

    /* 4m. 非反色显示 */
    CMD1(0xA6);

    /* 4n. 电荷泵设置 */
    CMD2(0x8D, 0x14);  /* 启用电荷泵 */

    /* 4o. 开启显示 */
    CMD1(0xAF);

    #undef CMD1
    #undef CMD2

    /* ---- 5. 清屏 ---- */
    ESP_RETURN_ON_ERROR(oled_clear(), TAG, "OLED 清屏失败");

    ESP_LOGI(TAG, "OLED SSD1306 初始化完成");
    return ESP_OK;
}

esp_err_t oled_clear(void)
{
    memset(s_fb, 0x00, sizeof(s_fb));
    return ssd1306_refresh();
}

esp_err_t oled_show_string(uint8_t line, const char *text)
{
    if (line >= OLED_MAX_LINES) {
        return ESP_ERR_INVALID_ARG;
    }
    fb_write_string(line, text);
    return ssd1306_refresh();
}

esp_err_t oled_show_line(uint8_t line, const char *format, ...)
{
    if (line >= OLED_MAX_LINES) {
        return ESP_ERR_INVALID_ARG;
    }
    char buf[OLED_CHARS_PER_LINE + 1];
    va_list args;
    va_start(args, format);
    vsnprintf(buf, sizeof(buf), format, args);
    va_end(args);
    return oled_show_string(line, buf);
}


