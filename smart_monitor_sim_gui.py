#!/usr/bin/env python3
"""
SmartMonitor 综合工具体 v3.0
=============================
Tab 1 - 仿真测试: UDP 远程注入传感器数值到 ESP32, 接收实时数据
Tab 2 - 仓储管理: ESP32-CAM 扫码 → SQLite 入库/出库 → TreeView 库存表

依赖: tkinter + PIL (Pillow) + OpenCV + pyzbar + 标准库
"""

import tkinter as tk
from tkinter import ttk, messagebox
import socket
import json
import threading
import time
import sys
import os
import queue
from collections import OrderedDict

# 可选依赖（仓储管理 Tab）
try:
    import cv2
    import numpy as np
    CV2_OK = True
except ImportError:
    CV2_OK = False

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
    PYZBAR_OK = True
except ImportError:
    PYZBAR_OK = False

# 项目内模块
sys.path.insert(0, os.path.dirname(__file__))
import camera_protocol as proto
from warehouse_db import WarehouseDB

# ==================== 配置 ====================
ESP32_DATA_PORT = 8080
ESP32_CMD_PORT = 8081
CAMERA_PORT = 8082
RECV_BUF_SIZE = 2048
CAMERA_BUF_SIZE = 65536
CAMERA_SOCK_TIMEOUT = 0.002
FRAME_TIMEOUT = 0.3

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

LEVEL_COLORS = {
    0: "#4CAF50", 1: "#FFC107", 2: "#FF9800", 3: "#F44336", -1: "#9E9E9E",
}
LEVEL_NAMES = {0: "L0 正常", 1: "L1 预警", 2: "L2 严重", 3: "L3 紧急"}

# 仓储管理颜色
WH_COLORS = {
    "bg_dark": "#1E1E2E",
    "bg_panel": "#2D2D3F",
    "bg_input": "#3A3A50",
    "text": "#E0E0E0",
    "text_dim": "#B0B0B0",
    "text_bright": "#FFFFFF",
    "inbound": "#D32F2F",
    "inbound_light": "#FFCDD2",
    "outbound": "#388E3C",
    "outbound_light": "#C8E6C9",
    "neutral": "#1976D2",
}


class SimGUI:
    """综合工具主窗口"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SmartMonitor 综合工具体 v3.0")
        self.root.geometry("1280x920")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---- 仿真状态 ----
        self.esp_ip = tk.StringVar(value="10.16.234.86")
        self.latest_data = {}
        self.sim_active = False
        self.recv_thread_running = False
        self.recv_sock = None

        self.var_dht11_t = tk.IntVar(value=25)
        self.var_dht11_h = tk.IntVar(value=60)
        self.var_ds18b20_t = tk.DoubleVar(value=25.0)
        self.var_mq135_v = tk.DoubleVar(value=1.2)
        self.var_mq135_do = tk.IntVar(value=1)
        self.var_photo_raw = tk.IntVar(value=2000)
        self.var_photo_do = tk.IntVar(value=1)

        # ---- 仓储管理状态 ----
        self.warehouse_mode = None  # 'inbound' / 'outbound'
        self.camera_running = False
        self.camera_sock = None
        self.camera_thread = None
        self.frame_cache: OrderedDict = OrderedDict()
        self.frame_queue = queue.Queue(maxsize=2)
        self.last_scan_time = 0.0
        self.scan_cooldown = 0.5   # ★ P2: 缩短冷却时间, 不再怕频繁扫码
        self.qr_scan_counter = 0
        self.total_frames = 0
        self.fps_history = []
        self.current_frame = None  # 最新帧 (BGR numpy array)
        self.cam_window = None
        self.cam_window_label = None
        self.db = WarehouseDB() if os.path.exists(os.path.join(os.path.dirname(__file__), "warehouse_db.py")) else None
        if self.db is None:
            try:
                self.db = WarehouseDB()
            except Exception:
                pass

        self._build_ui()

    # ==================== UI 构建 ====================

    def _build_ui(self):
        """构建 Notebook 双标签页界面"""
        # 顶部连接栏（跨标签页共享）
        top_frame = ttk.Frame(self.root, padding=5)
        top_frame.pack(fill=tk.X)
        ttk.Label(top_frame, text="ESP32 IP:").pack(side=tk.LEFT)
        ttk.Entry(top_frame, textvariable=self.esp_ip, width=18).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="启动监听", command=self._start_listener).pack(side=tk.LEFT, padx=3)
        ttk.Button(top_frame, text="停止监听", command=self._stop_listener).pack(side=tk.LEFT, padx=3)
        ttk.Separator(top_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(top_frame, text="打开摄像头预览", command=self._open_camera_window).pack(side=tk.LEFT, padx=3)
        self.lbl_status = ttk.Label(top_frame, text="● 未连接", foreground="#9E9E9E")
        self.lbl_status.pack(side=tk.LEFT, padx=5)

        # Notebook 标签页
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        # Tab 1: 仿真测试
        self.tab_sim = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_sim, text="  仿真测试  ")
        self._build_sim_tab()

        # Tab 2: 仓储管理
        self.tab_warehouse = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_warehouse, text="  仓储管理  ")
        self._build_warehouse_tab()

    # ==================== Tab 1: 仿真测试 ====================

    def _build_sim_tab(self):
        """构建仿真测试标签页（迁移原有内容）"""
        main_frame = ttk.Frame(self.tab_sim, padding=5)
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
            ("光敏 ADC", "light_raw", "raw"),
            ("光敏 DO", "photo_do", ""),
            ("报警级别", "level", ""),
            ("报警原因", "reason", ""),
        ]
        for label, key, unit in fields:
            row = ttk.Frame(left_frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=f"{label}:", width=14, anchor=tk.E).pack(side=tk.LEFT)
            lbl_val = ttk.Label(row, text="--", width=16, anchor=tk.W, font=("Courier", 10, "bold"))
            lbl_val.pack(side=tk.LEFT, padx=3)
            ttk.Label(row, text=unit, width=6, anchor=tk.W).pack(side=tk.LEFT)
            self.realtime_labels[key] = lbl_val

        ttk.Separator(left_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)
        self.lbl_level_indicator = tk.Label(left_frame, text="未连接", font=("Arial", 14, "bold"),
                                            bg="#E0E0E0", fg="#333333", padx=20, pady=8, relief=tk.RAISED)
        self.lbl_level_indicator.pack(fill=tk.X, pady=5)
        self.lbl_mode = ttk.Label(left_frame, text="模式: 真实传感器", foreground="#4CAF50")
        self.lbl_mode.pack(anchor=tk.W, pady=3)

        # 右侧: 仿真控制面板
        right_frame = ttk.LabelFrame(main_frame, text="🎮 仿真控制 (UDP 8081 → ESP32)", padding=8)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(5, 0))

        controls = [
            ("DHT11 温度", self.var_dht11_t, 0, 50, 1, "°C"),
            ("DHT11 湿度", self.var_dht11_h, 20, 90, 1, "%RH"),
            ("DS18B20 温度", self.var_ds18b20_t, -10, 50, 0.5, "°C"),
            ("MQ-135 电压", self.var_mq135_v, 0, 3.3, 0.1, "V"),
            ("光敏 AO", self.var_photo_raw, 0, 4095, 50, ""),
        ]
        self.scale_vars = []
        for label, var, vmin, vmax, step, unit in controls:
            row = ttk.Frame(right_frame)
            row.pack(fill=tk.X, pady=3)
            ttk.Label(row, text=f"{label}:", width=14, anchor=tk.E).pack(side=tk.LEFT)
            val_display = ttk.Label(row, textvariable=var, width=6, anchor=tk.E, font=("Courier", 9))
            val_display.pack(side=tk.RIGHT)
            ttk.Label(row, text=unit, width=4).pack(side=tk.RIGHT)
            scale = ttk.Scale(row, from_=vmax, to=vmin, variable=var, length=140,
                              command=lambda v, vr=var, st=step: self._on_scale(vr, st))
            scale.pack(side=tk.RIGHT, fill=tk.X, expand=True)
            self.scale_vars.append((var, step))

        do_frame = ttk.Frame(right_frame)
        do_frame.pack(fill=tk.X, pady=5)
        ttk.Label(do_frame, text="DO 控制:", width=14, anchor=tk.E).pack(side=tk.LEFT)
        ttk.Checkbutton(do_frame, text="MQ-135 DO=1(正常)", variable=self.var_mq135_do).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(do_frame, text="光敏 DO=1(正常)", variable=self.var_photo_do).pack(side=tk.LEFT, padx=5)

        ttk.Separator(right_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        preset_frame = ttk.LabelFrame(right_frame, text="📋 预设场景", padding=5)
        preset_frame.pack(fill=tk.X)
        for name, params in PRESETS.items():
            btn_row = ttk.Frame(preset_frame)
            btn_row.pack(fill=tk.X, pady=2)
            ttk.Button(btn_row, text=name, width=12, command=lambda p=params: self._load_preset(p)).pack(side=tk.LEFT)
            ttk.Label(btn_row, text=params["desc"], foreground="#666666", font=("Arial", 8)).pack(side=tk.LEFT, padx=5)

        ttk.Separator(right_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        action_frame = ttk.Frame(right_frame)
        action_frame.pack(fill=tk.X)
        ttk.Button(action_frame, text="▶ Apply 注入", command=self._apply_sim, width=15).pack(side=tk.LEFT, padx=3)
        ttk.Button(action_frame, text="⟳ Reset 恢复", command=self._reset_sim, width=15).pack(side=tk.LEFT, padx=3)

        # 日志
        log_frame = ttk.LabelFrame(self.tab_sim, text="📜 操作日志", padding=5)
        log_frame.pack(fill=tk.BOTH, padx=5, pady=(0, 5))
        self.log_text = tk.Text(log_frame, height=6, font=("Courier", 9), state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(self.log_text, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

    # ==================== Tab 2: 仓储管理 ====================

    def _build_warehouse_tab(self):
        """构建仓储管理标签页 — 工业深色主题"""
        wh = self.tab_warehouse
        bg = WH_COLORS["bg_dark"]
        wh.configure(style="Dark.TFrame")
        self._setup_styles()

        # 主容器
        main = tk.Frame(wh, bg=bg)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ---- 上半区：扫码操作区 ----
        top_area = tk.Frame(main, bg=bg)
        top_area.pack(fill=tk.X, pady=(0, 8))

        right_area = tk.Frame(top_area, bg=bg)
        right_area.pack(fill=tk.BOTH, expand=True)

        # 摄像头状态栏（放在模式控制上方）
        self.lbl_cam_status = tk.Label(right_area, text="等待连接...",
                                       bg=WH_COLORS["bg_input"], fg=WH_COLORS["text_dim"],
                                       font=("Courier", 9), anchor=tk.W, padx=5)
        self.lbl_cam_status.pack(fill=tk.X, pady=(0, 5))

        # 模式切换按钮
        mode_frame = tk.LabelFrame(right_area, text="  出入库模式 ", bg=WH_COLORS["bg_panel"],
                                   fg=WH_COLORS["text_bright"], font=("Microsoft YaHei", 10, "bold"),
                                   padx=8, pady=8)
        mode_frame.pack(fill=tk.X, pady=(0, 8))

        btn_frame = tk.Frame(mode_frame, bg=WH_COLORS["bg_panel"])
        btn_frame.pack()
        self.btn_inbound = tk.Button(btn_frame, text="📥  入库模式",
                                     bg=WH_COLORS["inbound"], fg="#FFFFFF",
                                     font=("Microsoft YaHei", 11, "bold"),
                                     relief=tk.FLAT, padx=16, pady=8, cursor="hand2",
                                     activebackground="#B71C1C", activeforeground="#FFFFFF",
                                     command=lambda: self._set_warehouse_mode("inbound"))
        self.btn_inbound.pack(side=tk.LEFT, padx=5)

        self.btn_outbound = tk.Button(btn_frame, text="📤  出库模式",
                                      bg="#555555", fg="#CCCCCC",
                                      font=("Microsoft YaHei", 11, "bold"),
                                      relief=tk.FLAT, padx=16, pady=8, cursor="hand2",
                                      activebackground="#388E3C", activeforeground="#FFFFFF",
                                      command=lambda: self._set_warehouse_mode("outbound"))
        self.btn_outbound.pack(side=tk.LEFT, padx=5)

        self.lbl_mode_status = tk.Label(mode_frame, text="● 待机中 — 请选择出入库模式",
                                        bg=WH_COLORS["bg_panel"], fg=WH_COLORS["text_dim"],
                                        font=("Microsoft YaHei", 9))
        self.lbl_mode_status.pack(pady=(5, 0))

        # 扫码结果展示
        result_frame = tk.LabelFrame(right_area, text="  扫码结果 ", bg=WH_COLORS["bg_panel"],
                                     fg=WH_COLORS["text_bright"], font=("Microsoft YaHei", 10, "bold"),
                                     padx=8, pady=8)
        result_frame.pack(fill=tk.BOTH, expand=True)

        self.wh_item_labels = {}
        item_fields = [
            ("物料编号", "id"), ("品名", "name"), ("危险类别", "category"),
            ("批次号", "batch"), ("规格", "spec"), ("生产日期", "mfg_date"),
            ("有效期至", "exp_date"),
        ]
        for label, key in item_fields:
            row = tk.Frame(result_frame, bg=WH_COLORS["bg_panel"])
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=f"{label}:", width=10, anchor=tk.E,
                     bg=WH_COLORS["bg_panel"], fg=WH_COLORS["text_dim"],
                     font=("Microsoft YaHei", 9)).pack(side=tk.LEFT)
            lbl = tk.Label(row, text="——", width=22, anchor=tk.W,
                           bg=WH_COLORS["bg_panel"], fg=WH_COLORS["text"],
                           font=("Microsoft YaHei", 9, "bold"))
            lbl.pack(side=tk.LEFT, padx=5)
            self.wh_item_labels[key] = lbl

        # 环境快照
        tk.Frame(result_frame, height=1, bg="#555555").pack(fill=tk.X, pady=4)
        self.lbl_env_snapshot = tk.Label(result_frame, text="入库环境: ——",
                                         bg=WH_COLORS["bg_panel"], fg=WH_COLORS["text_dim"],
                                         font=("Microsoft YaHei", 8))
        self.lbl_env_snapshot.pack(anchor=tk.W)

        # ---- 下半区：库存数据区 ----
        bottom_area = tk.Frame(main, bg=bg)
        bottom_area.pack(fill=tk.BOTH, expand=True)

        # 库存表格
        table_frame = tk.LabelFrame(bottom_area, text="  库存清单 ", bg=WH_COLORS["bg_panel"],
                                    fg=WH_COLORS["text_bright"], font=("Microsoft YaHei", 10, "bold"),
                                    padx=5, pady=5)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        # TreeView 样式
        tree_style = ttk.Style()
        tree_style.configure("Dark.Treeview",
                             background=WH_COLORS["bg_input"],
                             foreground=WH_COLORS["text"],
                             fieldbackground=WH_COLORS["bg_input"],
                             rowheight=24)
        tree_style.configure("Dark.Treeview.Heading",
                             background=WH_COLORS["bg_panel"],
                             foreground=WH_COLORS["text_bright"],
                             font=("Microsoft YaHei", 9, "bold"))
        tree_style.map("Dark.Treeview.Heading", background=[("active", "#4A4A60")])

        columns = ("id", "name", "category", "batch", "spec", "status", "time", "temp", "humi")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                 style="Dark.Treeview", height=8)
        col_config = {
            "id": ("物料编号", 130), "name": ("品名", 100), "category": ("类别", 70),
            "batch": ("批次", 90), "spec": ("规格", 80), "status": ("状态", 55),
            "time": ("入库时间", 130), "temp": ("温度", 45), "humi": ("湿度", 45),
        }
        for col, (title, width) in col_config.items():
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor=tk.CENTER, minwidth=40)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll = ttk.Scrollbar(table_frame, command=self.tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        # 操作日志
        wh_log_frame = tk.LabelFrame(bottom_area, text="  出货记录 ", bg=WH_COLORS["bg_panel"],
                                     fg=WH_COLORS["text_bright"], font=("Microsoft YaHei", 10, "bold"),
                                     padx=5, pady=5)
        wh_log_frame.pack(fill=tk.X)
        self.wh_log_text = tk.Text(wh_log_frame, height=3, font=("Courier", 9),
                                   bg=WH_COLORS["bg_input"], fg=WH_COLORS["text_dim"],
                                   state=tk.DISABLED, relief=tk.FLAT, borderwidth=3)
        self.wh_log_text.pack(fill=tk.BOTH, expand=True)

        # 初始刷新库存表
        self._refresh_inventory_table()

    def _setup_styles(self):
        """设置深色主题样式"""
        style = ttk.Style()
        style.configure("Dark.TFrame", background=WH_COLORS["bg_dark"])
        style.configure("Dark.TLabelframe", background=WH_COLORS["bg_panel"])
        style.configure("Dark.TLabelframe.Label",
                        background=WH_COLORS["bg_panel"],
                        foreground=WH_COLORS["text_bright"])

    def _show_camera_placeholder(self):
        """在摄像头窗口显示占位画面"""
        if self.cam_window is None or not self.cam_window.winfo_exists():
            return
        try:
            placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(placeholder, "Camera Stream", (80, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (80, 80, 80), 2)
            cv2.putText(placeholder, f"UDP 0.0.0.0:{CAMERA_PORT}", (80, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 60), 1)
            cv2.putText(placeholder, "等待视频帧...", (80, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (50, 50, 50), 1)
            self._set_cam_image(placeholder)
        except Exception:
            pass

    def _set_cam_image(self, cv_image_bgr):
        """将 OpenCV BGR 图像设置到摄像头预览窗口"""
        if not PIL_OK or self.cam_window_label is None:
            return
        try:
            h, w = cv_image_bgr.shape[:2]
            # 根据窗口大小自适应缩放，但最大不超过 960x720
            max_w, max_h = 960, 720
            scale = min(max_w / w, max_h / h, 1.0)
            new_w, new_h = int(w * scale), int(h * scale)
            rgb = cv2.cvtColor(cv_image_bgr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            if new_w != w or new_h != h:
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            self._cam_tk = ImageTk.PhotoImage(img)
            self.cam_window_label.config(image=self._cam_tk)
        except Exception:
            pass

    def _open_camera_window(self):
        """打开独立的摄像头预览窗口"""
        if self.cam_window is not None and self.cam_window.winfo_exists():
            self.cam_window.lift()
            self.cam_window.focus_force()
            return
        self.cam_window = tk.Toplevel(self.root)
        self.cam_window.title("SmartMonitor 摄像头预览")
        self.cam_window.geometry("1000x780")
        self.cam_window.configure(bg="#000000")
        self.cam_window.protocol("WM_DELETE_WINDOW", self._on_cam_window_close)

        self.cam_window_label = tk.Label(self.cam_window, bg="#000000")
        self.cam_window_label.pack(expand=True, fill=tk.BOTH)

        self._show_camera_placeholder()
        # 确保摄像头流在运行
        if not self.camera_running:
            self._start_camera_stream()
        self._wh_log("已打开摄像头预览窗口")

    def _on_cam_window_close(self):
        """摄像头窗口关闭回调"""
        if self.cam_window is not None:
            self.cam_window.destroy()
            self.cam_window = None
            self.cam_window_label = None
        self._wh_log("摄像头预览窗口已关闭")

    # ==================== 仿真控制 (原有方法) ====================

    def _on_scale(self, var, step):
        val = var.get()
        if isinstance(var, tk.IntVar):
            rounded = round(val / step) * int(step) if step > 1 else val
            var.set(max(0, rounded))
        elif isinstance(var, tk.DoubleVar):
            rounded = round(val / step) * step
            var.set(round(rounded, 2))

    def _load_preset(self, params):
        self.var_dht11_t.set(params["dht11_t"])
        self.var_dht11_h.set(params["dht11_h"])
        self.var_ds18b20_t.set(params["ds18b20_t"])
        self.var_mq135_v.set(params["mq135_v"])
        self.var_mq135_do.set(params["mq135_do"])
        self.var_photo_raw.set(params["photo_raw"])
        self.var_photo_do.set(params["photo_do"])
        self._log(f"加载预设场景: {params['desc']}")
        self._apply_sim()

    def _apply_sim(self):
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
            try:
                data, _ = sock.recvfrom(512)
                self._log(f"← ESP32 确认: {data.decode('utf-8').strip()}")
            except socket.timeout:
                self._log("[警告] 未收到 ESP32 确认 (超时)")
            sock.close()
            self.sim_active = True
            self.lbl_mode.config(text="模式: 仿真注入", foreground="#F44336")
        except Exception as e:
            self._log(f"[错误] 发送失败: {e}")

    def _reset_sim(self):
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
        if self.recv_thread_running:
            self._log("[提示] 监听已在运行中")
            return
        self.recv_thread_running = True
        thread = threading.Thread(target=self._recv_loop, daemon=True)
        thread.start()
        self._log("UDP 监听已启动 (0.0.0.0:8080)")

    def _stop_listener(self):
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
        field_map = {
            "dht11_t": ("dht11_t", lambda v: f"{v:.1f}"),
            "dht11_h": ("dht11_h", lambda v: f"{v:.1f}"),
            "ds18b20_t": ("ds18b20_t", lambda v: f"{v:.4f}"),
            "mq135_v": ("mq135_v", lambda v: f"{v:.2f}"),
            "mq135_do": ("mq135_do", lambda v: "正常" if v == 1 else ("超标" if v == 0 else "--")),
            "light_raw": ("light_raw", lambda v: f"{v}"),
            "photo_do": ("photo_do", lambda v: "正常" if v == 1 else ("异常" if v == 0 else "--")),
            "level": ("level", lambda v: LEVEL_NAMES.get(v, f"未知({v})")),
            "reason": ("reason", lambda v: str(v) if v else "--"),
        }
        for key, (json_key, fmt) in field_map.items():
            if json_key in obj:
                val = fmt(obj[json_key])
                self.realtime_labels[key].config(text=val)
        level = obj.get("level", -1)
        color = LEVEL_COLORS.get(level, "#9E9E9E")
        name = LEVEL_NAMES.get(level, "未知")
        self.lbl_level_indicator.config(text=name, bg=color, fg="white" if level >= 2 else "#333333")

    # ==================== 仓储管理 - 摄像头拉流 ====================

    def _start_camera_stream(self):
        """启动 UDP 8082 摄像头后台接收线程"""
        if self.camera_running:
            return
        if not CV2_OK:
            self._wh_log("摄像头模块不可用：需要 opencv-python")
            return
        self.camera_running = True
        self.frame_cache.clear()
        while not self.frame_queue.empty():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break
        self.camera_thread = threading.Thread(target=self._camera_recv_loop, daemon=True)
        self.camera_thread.start()
        self._wh_log("摄像头接收已启动 (UDP 8082)")
        self.root.after(100, self._poll_camera_queue)

    def _stop_camera_stream(self):
        self.camera_running = False
        if self.camera_sock:
            try:
                self.camera_sock.close()
            except OSError:
                pass
            self.camera_sock = None

    def _camera_recv_loop(self):
        """后台线程: UDP 8082 接收 0xAA55 分片 → 重组 JPEG → 解码 → 入队"""
        try:
            self.camera_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.camera_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.camera_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            self.camera_sock.bind(("0.0.0.0", CAMERA_PORT))
            self.camera_sock.settimeout(CAMERA_SOCK_TIMEOUT)
        except OSError as e:
            self._wh_log(f"[错误] 无法绑定 {CAMERA_PORT}: {e}")
            self.camera_running = False
            return

        while self.camera_running:
            try:
                data, _ = self.camera_sock.recvfrom(CAMERA_BUF_SIZE)
            except socket.timeout:
                continue
            except OSError:
                if self.camera_running:
                    self._wh_log("[错误] 摄像头 socket 异常")
                break

            result = proto.unpack_header(data)
            if result is None:
                continue

            frame_id, chunk_idx, total_chunks = result
            jpeg_chunk = data[proto.HEADER_SIZE:]

            # 帧缓存管理
            if frame_id not in self.frame_cache:
                if len(self.frame_cache) >= 3:
                    self.frame_cache.pop(next(iter(self.frame_cache)))
                self.frame_cache[frame_id] = {
                    "chunks": [b""] * total_chunks,
                    "total": total_chunks,
                    "received": set(),
                    "start_time": time.time(),
                }
            cache = self.frame_cache[frame_id]
            if chunk_idx not in cache["received"]:
                cache["chunks"][chunk_idx] = jpeg_chunk
                cache["received"].add(chunk_idx)

            # 检查完整帧
            completed_fids = []
            for fid, c in self.frame_cache.items():
                if len(c["received"]) == c["total"]:
                    completed_fids.append(fid)

            for fid in completed_fids:
                c = self.frame_cache.pop(fid)
                jpeg_data = b"".join(c["chunks"])
                img_array = np.frombuffer(jpeg_data, dtype=np.uint8)
                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                if frame is not None:
                    # 丢弃旧帧，放入最新帧
                    while True:
                        try:
                            self.frame_queue.get_nowait()
                        except queue.Empty:
                            break
                    try:
                        self.frame_queue.put_nowait(frame)
                    except queue.Full:
                        pass

            # 清理超时帧
            now = time.time()
            stale = [fid for fid, c in self.frame_cache.items()
                     if now - c["start_time"] > FRAME_TIMEOUT]
            for fid in stale:
                del self.frame_cache[fid]

        if self.camera_sock:
            try:
                self.camera_sock.close()
            except OSError:
                pass
            self.camera_sock = None

    def _poll_camera_queue(self):
        """主线程定时轮询帧队列, 显示到独立窗口 + 扫码"""
        if not self.camera_running:
            return
        try:
            frame = self.frame_queue.get_nowait()
            self.current_frame = frame
            self.total_frames += 1

            # FPS 计算
            now = time.time()
            self.fps_history.append(now)
            if len(self.fps_history) > 30:
                self.fps_history.pop(0)
            fps = 0.0
            if len(self.fps_history) >= 2:
                dur = self.fps_history[-1] - self.fps_history[0]
                fps = (len(self.fps_history) - 1) / dur if dur > 0 else 0.0

            # 显示到独立窗口（如果已打开）
            if self.cam_window is not None and self.cam_window.winfo_exists():
                if self.warehouse_mode is None:
                    black = np.zeros((480, 640, 3), dtype=np.uint8)
                    self._set_cam_image(black)
                else:
                    self._set_cam_image(frame)
            mode_text = {"inbound": "入库模式", "outbound": "出库模式"}.get(self.warehouse_mode, "待机")
            self.lbl_cam_status.config(text=f"FPS:{fps:.1f} | 模式:{mode_text} | 帧#{self.total_frames}")

            # 每 3 帧扫码一次
            self.qr_scan_counter += 1
            if self.qr_scan_counter >= 3:
                self.qr_scan_counter = 0
                self._scan_qr(frame)

        except queue.Empty:
            pass
        self.root.after(100, self._poll_camera_queue)

    # ==================== 仓储管理 - 二维码扫描 ====================

    def _scan_qr(self, frame):
        """对当前帧执行 pyzbar 二维码解码"""
        if not PYZBAR_OK:
            return
        now = time.time()
        if now - self.last_scan_time < self.scan_cooldown:
            return
        if self.warehouse_mode is None:
            return

        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # ★ P2: CLAHE 局部直方图均衡 → 增强 QR 边缘对比度, 与 Docker API 对齐
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray = clahe.apply(gray)
            # ★ 轻度锐化核 → 进一步强化模糊文本边缘
            kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
            gray = cv2.filter2D(gray, -1, kernel)
            results = pyzbar_decode(gray)
            for r in results:
                data_str = r.data.decode("utf-8").strip()
                try:
                    item = json.loads(data_str)
                except json.JSONDecodeError:
                    continue  # 非 JSON 格式，跳过

                # 验证必要字段
                if "id" not in item:
                    continue

                self.last_scan_time = time.time()
                self._wh_log(f"扫描到二维码: {item.get('id')} - {item.get('name', '未知')}")

                if self.warehouse_mode == "inbound":
                    self._do_checkin(item)
                elif self.warehouse_mode == "outbound":
                    self._do_checkout(item)
                return  # 每帧只处理一个码
        except Exception as e:
            pass  # 静默处理

    # ==================== 仓储管理 - 出入库业务逻辑 ====================

    def _set_warehouse_mode(self, mode):
        """切换入库/出库模式"""
        if mode not in ("inbound", "outbound"):
            return

        # 切换模式
        if self.warehouse_mode == mode:
            self.warehouse_mode = None
            self.btn_inbound.config(bg=WH_COLORS["inbound"], relief=tk.FLAT)
            self.btn_outbound.config(bg="#555555", fg="#CCCCCC", relief=tk.FLAT)
            self.lbl_mode_status.config(text="● 待机中 — 请选择出入库模式", fg=WH_COLORS["text_dim"])
            self._wh_log("已退出出入库模式")
            # 清空扫码结果
            for lbl in self.wh_item_labels.values():
                lbl.config(text="——")
            self.lbl_env_snapshot.config(text="入库环境: ——")
            return

        self.warehouse_mode = mode
        if mode == "inbound":
            self.btn_inbound.config(bg="#B71C1C", relief=tk.SUNKEN)
            self.btn_outbound.config(bg="#555555", fg="#CCCCCC", relief=tk.FLAT)
            self.lbl_mode_status.config(text="● 入库模式已激活 — 请将二维码对准摄像头",
                                        fg=WH_COLORS["inbound"])
        else:
            self.btn_outbound.config(bg="#1B5E20", relief=tk.SUNKEN)
            self.btn_inbound.config(bg=WH_COLORS["inbound"], relief=tk.FLAT)
            self.lbl_mode_status.config(text="● 出库模式已激活 — 请将二维码对准摄像头",
                                        fg=WH_COLORS["outbound"])

        self._wh_log(f"进入{'入库' if mode == 'inbound' else '出库'}模式")
        # 确保摄像头在运行
        if not self.camera_running:
            self._start_camera_stream()

    def _get_env_snapshot(self):
        """从 latest_data 提取当前环境快照"""
        env = {"temp": 0, "humi": 0, "level": 0}
        if self.latest_data:
            env["temp"] = self.latest_data.get("dht11_t", 0)
            env["humi"] = self.latest_data.get("dht11_h", 0)
            env["level"] = self.latest_data.get("level", 0)
        return env

    def _do_checkin(self, item):
        """执行入库操作"""
        env = self._get_env_snapshot()

        if self.db is None:
            self._wh_log("[错误] 数据库不可用")
            return

        result = self.db.checkin(item, env)
        if result:
            self._wh_log(f"✅ 入库成功: {item['id']} - {item.get('name', '')}")
        else:
            self._wh_log(f"⚠️ 重复入库: {item['id']} 已在库中")

        # 更新扫码结果显示
        for key, lbl in self.wh_item_labels.items():
            lbl.config(text=str(item.get(key, "——")))
        level_name = LEVEL_NAMES.get(env["level"], "未知")
        self.lbl_env_snapshot.config(
            text=f"入库环境: 温度{env['temp']:.1f}°C  湿度{env['humi']:.1f}%  级别{level_name}")

        self._refresh_inventory_table()

    def _do_checkout(self, item):
        """执行出库操作"""
        item_id = item.get("id", "")
        env = self._get_env_snapshot()

        if self.db is None:
            self._wh_log("[错误] 数据库不可用")
            return

        success, info = self.db.checkout(item_id, env)
        if success:
            self._wh_log(f"✅ 出库成功: {item_id} - {info.get('name', '')}")
            # 更新扫码结果显示
            for key, lbl in self.wh_item_labels.items():
                lbl.config(text=str(info.get(key, "——")))
            level_name = LEVEL_NAMES.get(env["level"], "未知")
            self.lbl_env_snapshot.config(
                text=f"出库环境: 温度{env['temp']:.1f}°C  湿度{env['humi']:.1f}%  级别{level_name}")
        else:
            self._wh_log(f"❌ 出库失败: {info}")
            # 清空显示
            for lbl in self.wh_item_labels.values():
                lbl.config(text="——")

        self._refresh_inventory_table()

    def _refresh_inventory_table(self):
        """刷新 TreeView 库存表格"""
        for row in self.tree.get_children():
            self.tree.delete(row)

        if self.db is None:
            return

        items = self.db.get_inventory()
        for item in items:
            status_color = {"在库": "#4CAF50", "已出库": "#9E9E9E"}.get(item["status"], "#FFFFFF")
            self.tree.insert("", tk.END, values=(
                item["id"], item["name"], item["category"],
                item["batch"], item["spec"], item["status"],
                item["checkin_time"][:16] if item["checkin_time"] else "",
                f"{item['checkin_temp']:.1f}" if item["checkin_temp"] else "",
                f"{item['checkin_humi']:.1f}" if item["checkin_humi"] else "",
            ), tags=(item["status"],))

        self.tree.tag_configure("在库", foreground="#4CAF50")
        self.tree.tag_configure("已出库", foreground="#9E9E9E")

    # ==================== 日志方法 ====================

    def _log(self, msg):
        """追加日志到仿真 Tab 日志区"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _wh_log(self, msg):
        """追加日志到仓储管理操作日志区"""
        timestamp = time.strftime("%H:%M:%S")
        self.wh_log_text.config(state=tk.NORMAL)
        self.wh_log_text.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.wh_log_text.see(tk.END)
        self.wh_log_text.config(state=tk.DISABLED)

    # ==================== 生命周期 ====================

    def _on_close(self):
        """窗口关闭时清理资源"""
        self._log("正在关闭...")
        if self.sim_active:
            self._reset_sim()
            time.sleep(0.5)
        self._stop_listener()
        self._stop_camera_stream()
        if self.cam_window is not None and self.cam_window.winfo_exists():
            self.cam_window.destroy()
            self.cam_window = None
            self.cam_window_label = None
        if self.db:
            try:
                self.db.close()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        # 启动摄像头接收（自动启动）
        if CV2_OK and PIL_OK:
            self._start_camera_stream()
        self.root.mainloop()


# ==================== 入口 ====================
if __name__ == "__main__":
    app = SimGUI()
    app.run()
