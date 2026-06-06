#!/usr/bin/env python3
"""
SmartMonitor 仿真测试 GUI 工具
===============================
通过 UDP 远程注入传感器数值到 ESP32，方便测试分级报警逻辑。
- 端口 8080: 接收 ESP32 上报的实时数据
- 端口 8081: 向 ESP32 发送仿真注入命令

依赖: 仅 Python 标准库 (tkinter + socket + json + threading)
"""

import tkinter as tk
from tkinter import ttk, messagebox
import socket
import json
import threading
import time
import sys

# ==================== 配置 ====================
ESP32_DATA_PORT = 8080       # ESP32 → PC 数据上报
ESP32_CMD_PORT  = 8081       # PC → ESP32 仿真命令
RECV_BUF_SIZE   = 2048

# 预设场景参数 (对应四级报警)
PRESETS = {
    "L0 正常": {
        "dht11_t": 25, "dht11_h": 60, "ds18b20_t": 25.0,
        "mq135_v": 1.2, "mq135_do": 1, "photo_raw": 2000, "photo_do": 1,
        "desc": "全部正常, 绿灯"
    },
    "L1 预警": {
        "dht11_t": 25, "dht11_h": 86, "ds18b20_t": 25.0,
        "mq135_v": 1.2, "mq135_do": 1, "photo_raw": 100, "photo_do": 0,
        "desc": "仅 B 类源(光敏+湿度), 风扇不开"
    },
    "L2 严重": {
        "dht11_t": 39, "dht11_h": 60, "ds18b20_t": 25.0,
        "mq135_v": 2.8, "mq135_do": 0, "photo_raw": 2000, "photo_do": 1,
        "desc": "单个 A 类源(MQ135+高温), 风扇 ON"
    },
    "L3 紧急": {
        "dht11_t": 39, "dht11_h": 60, "ds18b20_t": 38.5,
        "mq135_v": 2.8, "mq135_do": 0, "photo_raw": 2000, "photo_do": 1,
        "desc": "多 A 类源(MQ135+双高温), 紧急排风"
    },
}

# 颜色映射
LEVEL_COLORS = {
    0: "#4CAF50",      # 绿色 L0
    1: "#FFC107",      # 黄色 L1
    2: "#FF9800",      # 橙色 L2
    3: "#F44336",      # 红色 L3
    -1: "#9E9E9E",     # 灰色 未连接
}
LEVEL_NAMES = {0: "L0 正常", 1: "L1 预警", 2: "L2 严重", 3: "L3 紧急"}


class SimGUI:
    """仿真测试 GUI 主窗口"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SmartMonitor 仿真测试工具 v2.0")
        self.root.geometry("820x680")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 状态变量
        self.esp_ip = tk.StringVar(value="10.16.234.86")
        self.latest_data = {}           # 最新接收到的传感器数据
        self.sim_active = False
        self.recv_thread_running = False
        self.recv_sock = None

        # 仿真值变量 (绑定到 GUI 控件)
        self.var_dht11_t   = tk.IntVar(value=25)
        self.var_dht11_h   = tk.IntVar(value=60)
        self.var_ds18b20_t = tk.DoubleVar(value=25.0)
        self.var_mq135_v   = tk.DoubleVar(value=1.2)
        self.var_mq135_do  = tk.IntVar(value=1)
        self.var_photo_raw = tk.IntVar(value=2000)
        self.var_photo_do  = tk.IntVar(value=1)

        self._build_ui()

    # ==================== UI 构建 ====================

    def _build_ui(self):
        """构建完整界面"""
        # --- 顶部: 连接栏 ---
        top_frame = ttk.Frame(self.root, padding=5)
        top_frame.pack(fill=tk.X)
        ttk.Label(top_frame, text="ESP32 IP:").pack(side=tk.LEFT)
        ttk.Entry(top_frame, textvariable=self.esp_ip, width=18).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="启动监听", command=self._start_listener).pack(side=tk.LEFT, padx=3)
        ttk.Button(top_frame, text="停止监听", command=self._stop_listener).pack(side=tk.LEFT, padx=3)
        ttk.Separator(top_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        self.lbl_status = ttk.Label(top_frame, text="● 未连接", foreground="#9E9E9E")
        self.lbl_status.pack(side=tk.LEFT, padx=5)

        # --- 主体: 左右分栏 ---
        main_frame = ttk.Frame(self.root, padding=5)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 左侧: 实时数据面板
        left_frame = ttk.LabelFrame(main_frame, text="📡 ESP32 实时数据 (UDP 8080)", padding=8)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        self.realtime_labels = {}
        fields = [
            ("DHT11 温度", "dht11_t", "°C"),
            ("DHT11 湿度", "dht11_h", "%RH"),
            ("DS18B20 温度", "ds18b20_t", "°C"),
            ("MQ-135 电压", "mq135_v", "V"),
            ("MQ-135 DO", "mq135_do", ""),
            ("光敏 AO", "light_v", "V"),
            ("光敏 DO", "photo_do", ""),
            ("报警级别", "level", ""),
            ("报警原因", "reason", ""),
        ]
        for i, (label, key, unit) in enumerate(fields):
            row = ttk.Frame(left_frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=f"{label}:", width=14, anchor=tk.E).pack(side=tk.LEFT)
            lbl_val = ttk.Label(row, text="--", width=16, anchor=tk.W,
                                font=("Courier", 10, "bold"))
            lbl_val.pack(side=tk.LEFT, padx=3)
            ttk.Label(row, text=unit, width=6, anchor=tk.W).pack(side=tk.LEFT)
            self.realtime_labels[key] = lbl_val

        # 实时报警级别指示灯
        ttk.Separator(left_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)
        self.lbl_level_indicator = tk.Label(left_frame, text="未连接", font=("Arial", 14, "bold"),
                                            bg="#E0E0E0", fg="#333333", padx=20, pady=8,
                                            relief=tk.RAISED)
        self.lbl_level_indicator.pack(fill=tk.X, pady=5)

        # 模式标签
        self.lbl_mode = ttk.Label(left_frame, text="模式: 真实传感器", foreground="#4CAF50")
        self.lbl_mode.pack(anchor=tk.W, pady=3)

        # 右侧: 仿真控制面板
        right_frame = ttk.LabelFrame(main_frame, text="🎮 仿真控制 (UDP 8081 → ESP32)", padding=8)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(5, 0))

        # 传感器参数网格
        controls = [
            ("DHT11 温度", self.var_dht11_t,   0, 50,   1, "°C"),
            ("DHT11 湿度", self.var_dht11_h,   20, 90,  1, "%RH"),
            ("DS18B20 温度", self.var_ds18b20_t, -10, 50, 0.5, "°C"),
            ("MQ-135 电压", self.var_mq135_v,   0, 3.3, 0.1, "V"),
            ("光敏 AO",     self.var_photo_raw, 0, 4095, 50, ""),
        ]
        self.scale_vars = []  # 用于批量 Apply 时读取真实值
        for i, (label, var, vmin, vmax, step, unit) in enumerate(controls):
            row = ttk.Frame(right_frame)
            row.pack(fill=tk.X, pady=3)
            ttk.Label(row, text=f"{label}:", width=14, anchor=tk.E).pack(side=tk.LEFT)
            val_display = ttk.Label(row, textvariable=var, width=6, anchor=tk.E,
                                    font=("Courier", 9))
            val_display.pack(side=tk.RIGHT)
            ttk.Label(row, text=unit, width=4).pack(side=tk.RIGHT)
            scale = ttk.Scale(row, from_=vmax, to=vmin, variable=var,
                              length=140, command=lambda v, vr=var, st=step: self._on_scale(vr, st))
            scale.pack(side=tk.RIGHT, fill=tk.X, expand=True)
            self.scale_vars.append((var, step))

        # DO 复选框
        do_frame = ttk.Frame(right_frame)
        do_frame.pack(fill=tk.X, pady=5)
        ttk.Label(do_frame, text="DO 控制:", width=14, anchor=tk.E).pack(side=tk.LEFT)
        ttk.Checkbutton(do_frame, text="MQ-135 DO=1(正常)",
                        variable=self.var_mq135_do).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(do_frame, text="光敏 DO=1(正常)",
                        variable=self.var_photo_do).pack(side=tk.LEFT, padx=5)

        # 预设场景按钮
        ttk.Separator(right_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        preset_frame = ttk.LabelFrame(right_frame, text="📋 预设场景", padding=5)
        preset_frame.pack(fill=tk.X)

        for name, params in PRESETS.items():
            btn_row = ttk.Frame(preset_frame)
            btn_row.pack(fill=tk.X, pady=2)
            ttk.Button(btn_row, text=name, width=12,
                       command=lambda p=params: self._load_preset(p)).pack(side=tk.LEFT)
            ttk.Label(btn_row, text=params["desc"], foreground="#666666",
                      font=("Arial", 8)).pack(side=tk.LEFT, padx=5)

        # Apply / Reset 按钮
        ttk.Separator(right_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        action_frame = ttk.Frame(right_frame)
        action_frame.pack(fill=tk.X)
        ttk.Button(action_frame, text="▶ Apply 注入", command=self._apply_sim,
                   width=15).pack(side=tk.LEFT, padx=3)
        ttk.Button(action_frame, text="⟳ Reset 恢复", command=self._reset_sim,
                   width=15).pack(side=tk.LEFT, padx=3)

        # --- 底部: 日志 ---
        log_frame = ttk.LabelFrame(self.root, text="📜 操作日志", padding=5)
        log_frame.pack(fill=tk.BOTH, padx=5, pady=(0, 5))
        self.log_text = tk.Text(log_frame, height=6, font=("Courier", 9), state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

    # ==================== 功能方法 ====================

    def _on_scale(self, var, step):
        """滑块拖动时自动量化到 step"""
        val = var.get()
        if isinstance(var, tk.IntVar):
            rounded = round(val / step) * int(step) if step > 1 else val
            var.set(max(0, rounded))
        elif isinstance(var, tk.DoubleVar):
            rounded = round(val / step) * step
            var.set(round(rounded, 2))

    def _load_preset(self, params):
        """加载预设场景参数到控件"""
        self.var_dht11_t.set(params["dht11_t"])
        self.var_dht11_h.set(params["dht11_h"])
        self.var_ds18b20_t.set(params["ds18b20_t"])
        self.var_mq135_v.set(params["mq135_v"])
        self.var_mq135_do.set(params["mq135_do"])
        self.var_photo_raw.set(params["photo_raw"])
        self.var_photo_do.set(params["photo_do"])
        self._log(f"加载预设场景: {params['desc']}")
        # 自动 Apply
        self._apply_sim()

    def _apply_sim(self):
        """发送仿真值到 ESP32"""
        ip = self.esp_ip.get().strip()
        if not ip:
            self._log("[错误] 请输入 ESP32 IP")
            return

        cmd = {
            "dht11_t": self.var_dht11_t.get(),
            "dht11_h": self.var_dht11_h.get(),
            "ds18b20_t": round(self.var_ds18b20_t.get(), 2),
            "mq135_v": round(self.var_mq135_v.get(), 2),
            "mq135_do": self.var_mq135_do.get(),
            "photo_raw": self.var_photo_raw.get(),
            "photo_do": self.var_photo_do.get(),
        }

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            msg = json.dumps(cmd)
            sock.sendto(msg.encode(), (ip, ESP32_CMD_PORT))
            self._log(f"→ 发送仿真命令 → {ip}:{ESP32_CMD_PORT}")
            self._log(f"  {msg}")

            # 等待确认
            try:
                data, _ = sock.recvfrom(512)
                ack = data.decode("utf-8").strip()
                self._log(f"← ESP32 确认: {ack}")
            except socket.timeout:
                self._log("[警告] 未收到 ESP32 确认 (超时)")

            sock.close()
            self.sim_active = True
            self.lbl_mode.config(text="模式: 仿真注入", foreground="#F44336")
        except Exception as e:
            self._log(f"[错误] 发送失败: {e}")

    def _reset_sim(self):
        """发送 reset 命令恢复真实传感器"""
        ip = self.esp_ip.get().strip()
        if not ip:
            self._log("[错误] 请输入 ESP32 IP")
            return

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            msg = '{"cmd":"reset"}'
            sock.sendto(msg.encode(), (ip, ESP32_CMD_PORT))
            self._log(f"→ 发送重置命令 → {ip}:{ESP32_CMD_PORT}")

            try:
                data, _ = sock.recvfrom(512)
                self._log(f"← ESP32: {data.decode('utf-8').strip()}")
            except socket.timeout:
                pass

            sock.close()
            self.sim_active = False
            self.lbl_mode.config(text="模式: 真实传感器", foreground="#4CAF50")
        except Exception as e:
            self._log(f"[错误] Reset 发送失败: {e}")

    def _start_listener(self):
        """启动后台 UDP 监听线程 (端口 8080)"""
        if self.recv_thread_running:
            self._log("[提示] 监听已在运行中")
            return

        self.recv_thread_running = True
        thread = threading.Thread(target=self._recv_loop, daemon=True)
        thread.start()
        self._log("UDP 监听已启动 (0.0.0.0:8080)")

    def _stop_listener(self):
        """停止后台监听"""
        self.recv_thread_running = False
        if self.recv_sock:
            try:
                self.recv_sock.close()
            except OSError:
                pass
        self._log("UDP 监听已停止")
        self.lbl_status.config(text="● 未连接", foreground="#9E9E9E")
        self.lbl_level_indicator.config(text="未连接", bg="#E0E0E0")

    def _recv_loop(self):
        """后台线程: 持续接收 UDP 8080 数据"""
        try:
            self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.recv_sock.bind(("0.0.0.0", ESP32_DATA_PORT))
            self.recv_sock.settimeout(1.0)
        except OSError as e:
            self._log(f"[错误] 无法绑定 8080: {e}")
            self.recv_thread_running = False
            return

        self._log("UDP 8080 监听线程已启动, 等待数据...")

        while self.recv_thread_running:
            try:
                data, addr = self.recv_sock.recvfrom(RECV_BUF_SIZE)
                raw = data.decode("utf-8").strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                self.latest_data = obj
                self.root.after(0, self._update_realtime_display, obj)
                self.lbl_status.config(text=f"● 已连接 {addr[0]}", foreground="#4CAF50")
            except socket.timeout:
                continue
            except OSError:
                if self.recv_thread_running:
                    self._log("[错误] socket 异常")
                break

        if self.recv_sock:
            try:
                self.recv_sock.close()
            except OSError:
                pass
            self.recv_sock = None

    def _update_realtime_display(self, obj):
        """主线程: 更新实时面板 (由 _recv_loop 通过 root.after 触发)"""
        # 更新各字段
        field_map = {
            "dht11_t":   ("dht11_t",   lambda v: f"{v:.1f}"),
            "dht11_h":   ("dht11_h",   lambda v: f"{v:.1f}"),
            "ds18b20_t": ("ds18b20_t", lambda v: f"{v:.4f}"),
            "mq135_v":   ("mq135_v",   lambda v: f"{v:.2f}"),
            "mq135_do":  ("mq135_do",  lambda v: "正常" if v == 1 else ("超标" if v == 0 else "--")),
            "light_v":   ("light_v",   lambda v: f"{v:.2f}"),
            "photo_do":  ("photo_do",  lambda v: "正常" if v == 1 else ("异常" if v == 0 else "--")),
            "level":     ("level",     lambda v: LEVEL_NAMES.get(v, f"未知({v})")),
            "reason":    ("reason",    lambda v: str(v) if v else "--"),
        }

        for key, (json_key, fmt) in field_map.items():
            if json_key in obj:
                val = fmt(obj[json_key])
                self.realtime_labels[key].config(text=val)

        # 更新级别指示灯
        level = obj.get("level", -1)
        color = LEVEL_COLORS.get(level, "#9E9E9E")
        name = LEVEL_NAMES.get(level, "未知")
        self.lbl_level_indicator.config(text=name, bg=color,
                                        fg="white" if level >= 2 else "#333333")

    def _log(self, msg):
        """追加日志到文本框"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _on_close(self):
        """窗口关闭时自动发送 reset + 清理"""
        self._log("正在关闭...")
        if self.sim_active:
            self._reset_sim()
            time.sleep(0.5)
        self._stop_listener()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ==================== 入口 ====================
if __name__ == "__main__":
    app = SimGUI()
    app.run()
