#!/bin/bash
# NAS 后端一键部署脚本
# 用法：ssh admin@<NAS_IP> 后粘贴本脚本内容（或 sudo -S bash deploy.sh）
# 注意：DEPLOY_DIR 按你的实际部署路径改

set -e

DEPLOY_DIR="${DEPLOY_DIR:-<部署目录>/hongguo}"

echo "=== 红果 NAS 后端部署 ==="
echo ""

# 1. 拉新代码
echo "[1/4] git pull ..."
cd "$DEPLOY_DIR"
sudo git pull origin main

# 2. 重建镜像（关键：--no-cache 确保用最新 static/index.html）
echo ""
echo "[2/4] docker compose build --no-cache ..."
cd "$DEPLOY_DIR/nas-backend"
sudo docker compose build --no-cache

# 3. 重启容器
echo ""
echo "[3/4] docker compose up -d ..."
sudo docker compose up -d

# 4. 验证
echo ""
echo "[4/4] 验证 ..."
sleep 3
sudo docker ps | grep hongguo
echo ""
echo "最近日志："
sudo docker logs hongguo --tail 20
echo ""
echo "=== 完成 ==="
echo "平板/PC 端访问：http://<NAS_IP>:8000/"