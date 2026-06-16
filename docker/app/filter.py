"""
传感器数据滤波模块 — 物理范围截断 + EMA 指数移动平均

解决:
  - DHT11 单总线通信错误 → 温度突降至负值 / 湿度归零
  - MQ-135 模拟量 ADC 量化噪声 → 锯齿波
  - 光敏电阻同上

用法:
    f = SensorFilter()
    filtered = f.apply({"dht11_t": 25.3, "dht11_h": 60, "mq135_v": 1.2, ...})
"""
import os


# ─── 各传感器物理范围 (min, max) ─────────────────────────────
RANGE_LIMITS = {
    "dht11_t": (-10.0, 60.0),      # ★ 上限放宽至 60°C（化肥堆垛自热可达 55°C）
    "dht11_h": (0.0, 100.0),
    "ds18b20_t": (-55.0, 125.0),
    "mq135_v": (0.0, 3.6),
    "mq135_raw": (0, 4095),
    "light_raw": (0, 4095),
}

# ─── EMA 平滑系数 (alpha 越小越平滑) ─────────────────────────
EMA_ALPHAS = {
    "dht11_t": 0.20,
    "dht11_h": 0.20,
    "ds18b20_t": 0.20,
    "mq135_v": 0.08,      # 电压向後兼容，保持强平滑
    "mq135_raw": 0.15,    # ★ 与光敏对齐，减小响应延迟
    "light_raw": 0.15,
}


# ─── 哨兵值：ESP32 传感器读失败时发送的标记值 ────────────────
#   通用哨兵: <-0.5 (DHT11 失败返回 -127.0, MQ135/光敏失败返回 -1.0)
#   DS18B20 合法范围含负值 (-55~125°C), 需特殊处理
_SENTINEL_THRESHOLD = -0.5
_SENTINEL_DS18B20 = -50.0  # ★ DS18B20: <-50°C 视为哨兵 (物理极限 -55°C)


def _is_sentinel(key: str, value: float) -> bool:
    """判断是否为传感器读失败的哨兵值"""
    if key == "ds18b20_t":
        return value < _SENTINEL_DS18B20  # DS18B20: -55°C 下限, <-50 即弃
    return value < _SENTINEL_THRESHOLD   # 其他传感器: 负值无意义


def _clamp(key: str, value: float | None) -> float | None:
    """将值截断到该字段的物理范围，None / 哨兵值 返回 None"""
    if value is None:
        return None
    limits = RANGE_LIMITS.get(key)
    if limits is None:
        return value

    # 哨兵值检测：传感器读失败标记（如 DHT11 返回 -1.0）
    if _is_sentinel(key, value):
        return None

    lo, hi = limits
    # 如果值超出范围超过阈值，返回 None 标记为无效
    if value < lo or value > hi:
        return None
    return value


class SensorFilter:
    """
    每个字段独立维护 EMA 状态的滤波器。

    流程: 原始值 → 范围截断(剔除离群点) → EMA 平滑 → 输出
    """

    def __init__(self):
        self._ema: dict[str, float] = {}   # 当前 EMA 值
        self._init: dict[str, bool] = {}   # 是否已初始化
        self._enabled = os.getenv("SENSOR_FILTER", "true").lower() in ("1", "true", "on")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def apply(self, data: dict) -> dict:
        """
        对一条传感器数据字典做滤波，原地修改后返回同一引用。

        非数值字段(level/alert/reason/err 等)保持不变。
        """
        if not self._enabled:
            return data

        for key in list(RANGE_LIMITS.keys()):
            raw = data.get(key)
            if raw is None:
                continue

            # Step 1: 范围截断
            clamped = _clamp(key, raw)

            # 截断后为 None 说明是严重离群值，保留 None
            if clamped is None:
                data[key] = None
                continue

            # Step 2: EMA 平滑
            alpha = EMA_ALPHAS.get(key, 0.15)
            if not self._init.get(key, False):
                self._ema[key] = clamped
                self._init[key] = True
                data[key] = clamped
            else:
                ema_val = self._ema[key]
                smoothed = alpha * clamped + (1 - alpha) * ema_val
                self._ema[key] = smoothed
                data[key] = round(smoothed, 4)

        return data

    def reset(self):
        """重置所有 EMA 状态（用于重新启动或切换场景）"""
        self._ema.clear()
        self._init.clear()
