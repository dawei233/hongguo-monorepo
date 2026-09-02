FROM python:3.11-slim

WORKDIR /app

# 时区
ENV TZ=Asia/Shanghai

# ffmpeg（HEVC 视频转 H.264，保证 Android WebView 可解码播放）
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 复制 entrypoint 脚本,容器启动时自动注入 /etc/hosts(绕过 NAS 上 caddy-proxy 的 DNS 劫持)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 数据目录（播放记录/缓存/设备ID 持久化）
ENV HONGGUO_DATA_DIR=/data
VOLUME /data

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "app_nas.py"]
