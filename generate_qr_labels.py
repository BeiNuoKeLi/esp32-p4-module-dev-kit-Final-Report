#!/usr/bin/env python3
"""
农资化肥仓储二维码标签生成工具
=============================
根据系统传感器能力（MQ-135检测NH3/硫化物 + DHT11温湿度 + 光敏）
精选 6 种与报警逻辑匹配的化肥，批量生成二维码标签。

用法:
  D:\Anaconda3\envs\ForAgents\python.exe generate_qr_labels.py                # 默认每种 3 份
  D:\Anaconda3\envs\ForAgents\python.exe generate_qr_labels.py --copies 5     # 每种 5 份
  D:\Anaconda3\envs\ForAgents\python.exe generate_qr_labels.py --copies 10    # 每种 10 份

输出: qr_labels/ 目录，每张 PNG 即为可打印的仓储标签（ID 格式: FERT-YYYYMMDD-XXX-XX）
"""

import qrcode
import json
import os
import argparse
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "qr_labels"

# 示例化肥物料数据（可自定义扩展）
# ============ 示例化肥物料数据 ============
# 设计原则：每种物料必须能被至少一个传感器在泄漏时检测到
#   - MQ-135: 检测 NH3 / 硫化物 / 烟雾
#   - DHT11:  温湿度异常（高温加速氨挥发、高湿引起结块潮解）
#   - 光敏:   光敏感物料辅助检测
#   - 类别限为: 氮肥 / 复合肥 / 钾肥
DEMO_FERTILIZERS = [
    {
        "id": "FERT-20260611-001",
        "name": "碳酸氢铵",
        "category": "氮肥",
        "batch": "B2026-0611",
        "spec": "50kg/袋",
        "mfg_date": "2026-06-01",
        "exp_date": "2027-06-01",
        "sensor_note": "MQ-135(NH3直接检测) + DHT11(高温高湿加速分解)",
    },
    {
        "id": "FERT-20260611-002",
        "name": "尿素",
        "category": "氮肥",
        "batch": "B2026-0611",
        "spec": "50kg/袋",
        "mfg_date": "2026-06-01",
        "exp_date": "2027-12-01",
        "sensor_note": "MQ-135(氨挥发) + DHT11(吸潮结块, 湿度敏感)",
    },
    {
        "id": "FERT-20260611-003",
        "name": "硫酸铵",
        "category": "氮肥",
        "batch": "B2026-0611",
        "spec": "50kg/袋",
        "mfg_date": "2026-06-01",
        "exp_date": "2027-12-01",
        "sensor_note": "MQ-135(NH3检测) + DHT11(酸性肥料, 湿度敏感)",
    },
    {
        "id": "FERT-20260611-004",
        "name": "磷酸二铵(DAP)",
        "category": "复合肥",
        "batch": "B2026-0611",
        "spec": "50kg/袋",
        "mfg_date": "2026-06-01",
        "exp_date": "2027-12-01",
        "sensor_note": "MQ-135(NH3检测) + DHT11(湿度→结块, 养分流失)",
    },
    {
        "id": "FERT-20260611-005",
        "name": "氯化钾(MOP)",
        "category": "钾肥",
        "batch": "B2026-0611",
        "spec": "50kg/袋",
        "mfg_date": "2026-06-01",
        "exp_date": "2027-12-01",
        "sensor_note": "DHT11(纯湿度敏感, 易潮解)",
    },
    {
        "id": "FERT-20260611-006",
        "name": "NPK 复合肥(15-15-15)",
        "category": "复合肥",
        "batch": "B2026-0611",
        "spec": "50kg/袋",
        "mfg_date": "2026-06-01",
        "exp_date": "2027-12-01",
        "sensor_note": "MQ-135(NH3检测) + DHT11(湿度+温度双重敏感)",
    },
]


def generate_qr_label(item: dict, output_dir: Path):
    """
    为单个物料生成二维码标签

    输出: output_dir/{id}.png
    标签设计: 左半部分为二维码，右上角为品名，右侧依次展示属性
    """
    data_json = json.dumps(item, ensure_ascii=False)
    item_id = item["id"]

    # 创建二维码
    # ★ P3: version=4 + EC=H(30%) → 抗污损能力翻倍, 物理 33×33 模块
    qr = qrcode.QRCode(
        version=4,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=8,
        border=2,
    )
    qr.add_data(data_json)
    qr.make(fit=True)

    # 生成图像（黑白配色，带文字说明）
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    # 保存
    output_path = output_dir / f"{item_id}.png"
    img.save(str(output_path))
    return output_path


def main():
    parser = argparse.ArgumentParser(description="农资化肥仓储二维码标签生成工具")
    parser.add_argument("--copies", type=int, default=3,
                        help="每种化肥生成的标签份数（默认 3）")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    copies = max(1, args.copies)

    print(f"二维码标签输出目录: {OUTPUT_DIR}")
    print(f"共 {len(DEMO_FERTILIZERS)} 种化肥，每种生成 {copies} 份标签\n")

    total = 0
    for base_item in DEMO_FERTILIZERS:
        base_id = base_item["id"]
        for serial in range(1, copies + 1):
            item = dict(base_item)  # 浅拷贝，避免修改原数据
            item["id"] = f"{base_id}-{serial:02d}"
            path = generate_qr_label(item, OUTPUT_DIR)
            total += 1
        print(f"  [OK] {base_id}  ({base_item['name']}) x{copies}")

    print(f"\n[DONE] 全部生成完成! {total} 张标签已保存到 {OUTPUT_DIR}")
    print("  可将标签打印后贴到化肥包装袋上，用于入库/出库扫码测试。")


if __name__ == "__main__":
    main()
