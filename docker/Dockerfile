# 1. 替换为目前国内 100% 稳定的公益加速源，绕过 Docker Hub 的网络限制
FROM docker.1ms.run/library/python:3.11-slim

# 设置中国时区 + 摄像头/二维码解码系统依赖（合并为单层）
# 注意: libgl1-mesa-glx 在 Debian Trixie 已废弃，opencv-headless 不需要它
ENV TZ=Asia/Shanghai

# 2. 在 apt-get 之前，自动将系统下载源替换为中国科学技术大学（USTC）镜像源，防止 apt update 卡死
RUN sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || sed -i 's/deb.debian.org/mirrors.ustc.edu.cn/g' /etc/apt/sources.list

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tzdata libzbar0 \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# 3. 为 pip install 添加清华大学镜像源（-i 参数），让第三方 Python 库下载变快
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY app/ ./app/

RUN mkdir -p /app/data

EXPOSE 8000 8082/udp

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]