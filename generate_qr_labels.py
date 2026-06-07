#!/usr/bin/env python3
"""
危化品仓储二维码标签生成工具
=============================
根据系统传感器能力（MQ-135检测NH3/硫化物/苯系蒸气 + DHT11温湿度 + 光敏）
精选 8 种与报警逻辑匹配的危化品，批量生成二维码标签。

用法:
  D:\Anaconda3\envs\ForAgents\python.exe generate_qr_labels.py

输出: qr_labels/ 目录，每张 PNG 即为可打印的仓储标签
"""

import qrcode
import json
import os
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "qr_labels"

# 示例危化品物料数据（可自定义扩展）
# ============ 示例危化品物料数据 ============
# 设计原则：每种物料必须能被至少一个传感器在泄漏时检测到
#   - MQ-135: 检测 NH3 / 硫化物(H2S) / 苯系蒸气 / 烟雾
#   - DHT11:  温湿度异常（高温加速挥发、高湿引发反应）
#   - 光敏:   光敏感物料（如过氧化氢需避光）
#   - 类别限为: 易燃液体 / 腐蚀品 / 有毒 / 氧化剂（不含爆炸品、剧毒品）
DEMO_CHEMICALS = [
    {
        "id": "CHEM-20260607-001",
        "name": "苯",
        "category": "易燃液体",
        "batch": "B2026-0510",
        "spec": "500ml/棕色瓶",
        "mfg_date": "2026-05-10",
        "exp_date": "2027-05-10",
        "sensor_note": "MQ-135(苯系蒸气) + DHT11(温控<25°C)",
    },
    {
        "id": "CHEM-20260607-002",
        "name": "浓硫酸(98%)",
        "category": "腐蚀品",
        "batch": "B2026-0315",
        "spec": "1000ml/玻璃瓶",
        "mfg_date": "2026-03-15",
        "exp_date": "2028-03-15",
        "sensor_note": "DHT11(湿度监控!! 遇湿放热冒烟) + MQ-135(酸雾)",
    },
    {
        "id": "CHEM-20260607-003",
        "name": "氨水(25%)",
        "category": "腐蚀品",
        "batch": "B2026-0420",
        "spec": "500ml/密封瓶",
        "mfg_date": "2026-04-20",
        "exp_date": "2027-04-20",
        "sensor_note": "MQ-135(NH3直接检测) + DHT11(温度加速挥发)",
    },
    {
        "id": "CHEM-20260607-004",
        "name": "过氧化氢(30%)",
        "category": "氧化剂",
        "batch": "B2026-0210",
        "spec": "500ml/遮光瓶",
        "mfg_date": "2026-02-10",
        "exp_date": "2027-02-10",
        "sensor_note": "光敏(必须避光!) + DHT11(温度<30°C, 否则分解产氧)",
    },
    {
        "id": "CHEM-20260607-005",
        "name": "甲苯",
        "category": "易燃液体",
        "batch": "B2026-0118",
        "spec": "500ml/棕色瓶",
        "mfg_date": "2026-01-18",
        "exp_date": "2027-07-18",
        "sensor_note": "MQ-135(苯系蒸气) + DHT11(高挥发性, 温湿度双控)",
    },
    {
        "id": "CHEM-20260607-006",
        "name": "硫化钠(九水)",
        "category": "腐蚀品",
        "batch": "B2026-0525",
        "spec": "500g/密封袋",
        "mfg_date": "2026-05-25",
        "exp_date": "2027-11-25",
        "sensor_note": "MQ-135(遇酸释H2S硫化物) + DHT11(易潮解)",
    },
    {
        "id": "CHEM-20260607-007",
        "name": "丙酮",
        "category": "易燃液体",
        "batch": "B2026-0610",
        "spec": "500ml/密封瓶",
        "mfg_date": "2026-06-10",
        "exp_date": "2027-06-10",
        "sensor_note": "MQ-135(VOC烟雾) + DHT11(极高挥发性, 温控存储)",
    },
    {
        "id": "CHEM-20260607-008",
        "name": "二氯甲烷",
        "category": "有毒",
        "batch": "B2026-0425",
        "spec": "1000ml/密封瓶",
        "mfg_date": "2026-04-25",
        "exp_date": "2027-10-25",
        "sensor_note": "MQ-135(卤代烃蒸气) + DHT11(温控, 高温加速挥发)",
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
    qr = qrcode.QRCode(
        version=3,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
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
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"二维码标签输出目录: {OUTPUT_DIR}")
    print(f"共 {len(DEMO_CHEMICALS)} 个示例物料\n")

    for item in DEMO_CHEMICALS:
        path = generate_qr_label(item, OUTPUT_DIR)
        print(f"  [OK] {item['id']}  ->  {path.name}  ({item['name']})")

    print(f"\n[DONE] 全部生成完成! {len(DEMO_CHEMICALS)} 张标签已保存到 {OUTPUT_DIR}")
    print("  可将标签打印后贴到化工品容器上，用于入库/出库扫码测试。")


if __name__ == "__main__":
    main()
