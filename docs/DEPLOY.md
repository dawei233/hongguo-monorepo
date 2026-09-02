# 部署指南 (Deploy Guide)

> **目标**:把 NAS 后端部署到 NAS(fnOS / 任意 Linux)+ 升级 + 缓存清理 + 故障排查
>
> 所有 `<占位符>` 按你的实际环境替换。

---

## 一、环境要求

### 1.1 NAS 硬件
- 任意 Linux NAS 或服务器
- Docker + Docker Compose
- 至少 10GB 可用磁盘空间(视频缓存)

### 1.2 网络
- NAS IP:`<NAS_IP>`(如 `192.168.1.100`,按实际改)
- 平板 IP:`<TABLET_IP>`(按实际改)
- 二者**同一局域网**
- NAS 端口 `8000` 需开放(或配反向代理)

### 1.3 账号
- SSH:`admin@<NAS_IP>`,密码/密钥按你的环境配置

---

## 二、首次部署

### 2.1 把代码传到 NAS

```bash
# 方式 A:从 git 仓库拉
ssh admin@<NAS_IP>
cd <部署目录>          # 或你习惯的部署目录
sudo git clone <你的仓库地址> hongguo
cd hongguo
```

### 2.2 修改配置(如果需要)

```bash
# docker-compose.yml 默认端口 8000;如需改端口,改这里 + 反代
# entrypoint.sh 里 DOMAINS 列表可按需增删(DoH 解析注入 hosts,绕 DNS 劫持)
```

### 2.3 构建 + 启动容器

```bash
cd hongguo/nas-backend        # 注意:nas-backend 是独立子目录,Dockerfile 在其中
sudo docker compose build --no-cache   # 首次构建慢,10+ 分钟
sudo docker compose up -d
sudo docker ps | grep hongguo         # 确认容器运行
```

> **目录结构提醒**:NAS 上的部署目录是 `hongguo/nas-backend/`(不是根目录)。若你的 Dockerfile 在仓库根,调整 `cd` 路径。

### 2.4 验证

```bash
curl http://<NAS_IP>:8000/api/ping          # 应返回 {"ok":true}
curl "http://<NAS_IP>:8000/api/search?keyword=短剧"
```

浏览器打开 `http://<NAS_IP>:8000/` —— 应看到前端。

### 2.5 平板端配置

平板装安卓 APK(本地 `./gradlew assembleRelease` 或 CI 产物),首次打开填 NAS 地址 `http://<NAS_IP>:8000`。

---

## 三、代码更新后升级

### 3.1 同步源码到 NAS

**方式 A:git push + NAS pull**
```bash
# 本地
git push origin main
# NAS
ssh admin@<NAS_IP>
cd <部署目录>/hongguo
sudo git pull
```

**方式 B:SFTP/SCP 上传**
```bash
scp -r nas-backend/ admin@<NAS_IP>:<部署目录>/hongguo/
```

### 3.2 重建容器

```bash
cd <部署目录>/hongguo/nas-backend
sudo docker compose build --no-cache   # 关键:--no-cache 确保用最新代码
sudo docker compose up -d
```

### 3.3 清缓存(修改 `mp4_sanitize.py` / ffmpeg 命令时必须)

```bash
sudo docker exec hongguo bash -c 'rm -f /data/cache/videos/*.mp4 /data/cache/videos/*.mp4.meta.json /data/cache/videos/*.raw.mp4'
```

---

## 四、故障排查

### 4.1 容器启动失败

```bash
sudo docker logs hongguo --tail 100
# 常见:
# - ModuleNotFoundError:依赖没装全 → 检查 requirements.txt
# - Port 8000 already in use:端口占用 → docker ps 看谁占的
# - Permission denied:data 目录权限 → chmod -R 777 <部署目录>/data
```

### 4.2 播放"视频加载失败"

**排查**:
```bash
sudo docker logs hongguo --tail 50 | grep -E "净化|sanitize|play"
sudo docker exec hongguo bash -c 'ls -la /data/cache/videos/'
sudo docker exec hongguo bash -c 'ffprobe /data/cache/videos/<vid>.mp4'
sudo docker exec hongguo bash -c 'grep -ac saio /data/cache/videos/<vid>.mp4'  # 应为 0
```

**修复**:清旧产物重新走完整流程
```bash
sudo docker exec hongguo bash -c 'rm -f /data/cache/videos/*.mp4 /data/cache/videos/*.mp4.meta.json'
```

### 4.3 容器内网络不通(DNS 劫持)

**症状**:ffmpeg 报 `Connection timed out`,容器内 `getent hosts <域名>` 返回 `198.18.x.x`

**原因**:NAS 上若有 Clash TUN / 透明代理,会把容器 DNS 劫持到 fake-ip 段

**修复**:
1. 确认 `entrypoint.sh` 的 `DOMAINS` 列表覆盖所有要用的域名
2. `docker compose build` 重建(entrypoint 是构建进镜像的)
3. 验证:`docker exec hongguo getent ahosts hongguoduanju.com` 应返回真实 IP

### 4.4 磁盘占满

```bash
sudo docker exec hongguo bash -c 'du -sh /data/cache/videos'
# 自动清理:每 30 分钟扫一轮,删除超过 CACHE_AGE_LIMIT_SEC(默认 24 小时,见 server.py)的视频
# 手动清:sudo docker exec hongguo bash -c 'rm -f /data/cache/videos/*.mp4'
```

### 4.5 PC 版启动失败

```bash
红果漫剧.exe --console        # 控制台模式看错误
```
- 缺 DLL → 装 `vc_redist.x64.exe`(微软官方)
- WebView2 没装 → 装 Microsoft Edge WebView2 Runtime
- 端口 5137 被占 → `netstat -ano | findstr 5137` 找进程

---

*部署指南结束。更深的问题排查见根 README 与 SECONDARY_DEV.md。*
