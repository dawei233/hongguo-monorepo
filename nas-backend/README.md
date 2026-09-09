# NAS 后端（hongguo-nas-backend）

> Docker 容器化的 Flask 后端，部署在飞牛 OS NAS（<NAS_IP>:8000）

---

## 目录结构

```
nas-backend/
├── server.py                  # Flask 主程序（端口 8000，监听 0.0.0.0）
├── hongguo_core.py            # 视频签名 + 搜索 + 详情 + 解析（核心库）
├── mp4_sanitize.py            # MP4 净化器（剥 saio/saiz/senc/cslg）
├── app_nas.py                 # 容器入口（设置 HONGGUO_DATA_DIR=/data）
├── liushen/                   # 流沙签名 SDK
│   ├── device_register.py
│   ├── flurl/                 # HTTP 请求库
│   └── requirements.txt
├── static/
│   └── index.html             # 前端 UI（与 PC 版共用）
├── Dockerfile                 # python:3.11-slim 镜像
├── docker-compose.yml         # 容器编排 + volume mount
├── requirements.txt           # Python 依赖
├── data/                      # 持久化数据（volume mount 到容器 /data）
│   ├── cache/videos/          # 视频缓存（自动清理）
│   ├── prefs.json             # 用户偏好（收藏/进度）
│   └── config.json            # 配置
├── cache/                     # 临时缓存（gitignore）
├── temp/                      # 临时目录（gitignore）
└── README.md                  # 本文件
```

---

## 部署架构

```
飞牛 OS NAS (<NAS_IP>)
├── <部署目录>/   # git clone 的源码目录
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── nas-backend/             # 实际 build context
│   │   ├── server.py
│   │   └── ...
│   └── data/                    # volume mount 到容器 /data
│
└── Docker Container: hongguo
    ├── 镜像: python:3.11-slim
    ├── 端口: 8000 (0.0.0.0)
    ├── 入口: python app_nas.py → server.run_server(port=8000, host="0.0.0.0")
    ├── 工作目录: /app
    │   ├── server.py
    │   ├── hongguo_core.py
    │   ├── mp4_sanitize.py
    │   └── static/index.html
    └── 数据: /data (volume mount)
        ├── cache/videos/
        └── prefs.json
```

---

## 核心 API

### 路由列表
| 路径 | 方法 | 作用 |
|------|------|------|
| `/` | GET | 返回 `static/index.html` |
| `/api/ping` | GET | 健康检查 |
| `/api/log` | POST | 客户端日志上报 |
| `/api/settings` | GET/PUT | 应用设置（缓存目录等）|
| `/api/prefs` | GET/POST | 用户偏好（收藏/进度）|
| `/api/shutdown` | POST | 关闭后端 |
| `/api/search` | GET | 搜索（`?keyword=&tab=&page=`）|
| `/api/category/page` | GET | 分类页（推荐/短剧/漫剧分页）|
| `/api/series` | GET | 剧集详情（`?series_id=`）|
| `/api/play` | GET | 播放地址（`?vid=&sid=&client_cap=`）|
| `/api/cdn` | GET | CDN 视频代理（`?u=CDN_URL`）|
| `/local-video/<vid>.mp4` | GET | 本地缓存视频（Range 支持）|
| `/api/cache/add` | POST | 单集缓存 |
| `/api/cache/range` | GET | 批量缓存预取 |
| `/api/cache/file/<filename>` | GET | 缓存文件下载 |
| `/api/task` | GET | 任务状态查询 |
| `/api/device` | GET | 设备信息 |

### `/api/play` 完整流程（v44）
```
1. resolve_video_url(vid, sid) → CDN 加密 URL + content_key
2. raw_path = /data/cache/videos/{vid}.raw.mp4
3. if not raw_path.exists(): 下载 CDN URL 到 raw_path
4. ffmpeg -decryption_key +content_key -i raw_path -c copy +faststart → {vid}.mp4
5. python /app/mp4_sanitize.py {vid}.mp4 {vid}.san.mp4 → 剥 CENC box
6. 写 {vid}.mp4.meta.json (sid/title/ep_no/quality/processed_at)
7. 返回 {ok:true, task:{url:"/local-video/{vid}.mp4", ...}}
```

### `stream_manager` 预缓存（v33+）
```
触发: /api/cache/range?sid=&quality=&from=2&to=4
↓
后台 worker:
1. ffmpeg -decryption_key +content_key -i URL -c copy +faststart → {vid}.mp4
2. python /app/mp4_sanitize.py {vid}.mp4 → 剥 CENC box
3. 写 {vid}.mp4 + {vid}.mp4.meta.json
↓
用户切下一集 → /api/play 命中预缓存 → /local-video/{vid}.mp4 秒开
```

---

## 缓存目录命名

| 文件 | 含义 |
|------|------|
| `{vid}.raw.mp4` | CDN 下载的原始加密文件（中间产物） |
| `{vid}.mp4` | 解密 + 净化 + faststart 最终产物（前端加载） |
| `{vid}.mp4.meta.json` | 元数据伴生（判断缓存有效性） |
| `{vid}_h264.mp4` | （旧版本命名，已废弃） |

**前端加载**：`/local-video/{vid}.mp4`（send_file + Range 支持）

---

## 视频净化

CDN 源是 **CENC 伪加密 MP4**（带 saio/saiz/senc/cslg box）。WebView `<video>` 看到这些 box 会拒绝播放。

**mp4_sanitize.py** 干的事：
1. 读取 MP4 box 结构
2. 剥 saio/saiz/senc/cslg 等字节系非标 box
3. 重写 stco/co64 偏移（因为删了 box）
4. 输出 ISO BMFF 标准 MP4（顶层 boxes = [ftyp, free?, moov, mdat]）

**验证**：`grep -c saio {file}.mp4` 应该 == 0。

---

## 后台清理线程（v33+）

```python
# _do_cache_clean_by_age() - 每 30 分钟
# 按 processed_at 删超 1 小时

# _do_cache_clean_backup() - 每 24 小时
# 按 mtime 删超 24 小时（兜底）
```

启动入口：`server.run_server(port=8000, open_browser=False, host="0.0.0.0")` —— **必须传 `host="0.0.0.0"`** 否则 NAS 上访问不到。

---

## Docker 配置

### Dockerfile
```dockerfile
FROM python:3.11-slim
# 安装 ffmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
ENV HONGGUO_DATA_DIR=/data
EXPOSE 8000
CMD ["python", "app_nas.py"]
```

### docker-compose.yml
```yaml
services:
  hongguo:
    build: .
    container_name: hongguo
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
```

---

## 部署命令

详见 [`../docs/DEPLOY.md`](../docs/DEPLOY.md)

```bash
# 首次部署
ssh <NAS_USER>@<NAS_IP>
cd <部署目录>/nas-backend
sudo docker compose build --no-cache
sudo docker compose up -d

# 更新代码
sudo docker compose build --no-cache
sudo docker compose up -d
sudo docker exec hongguo bash -c 'rm -f /data/cache/videos/*.mp4 /data/cache/videos/*.mp4.meta.json'
```

---

## 调试

```bash
# 容器日志
sudo docker logs hongguo --tail 100 -f

# 进入容器
sudo docker exec -it hongguo bash

# 测试 API
curl http://<NAS_IP>:8000/api/search?keyword=&tab=1

# 看视频缓存
sudo docker exec hongguo bash -c 'ls -la /data/cache/videos/'
```

---

## 关联文档

- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — 后端在整体架构中的角色
- [`../docs/CHANGELOG.md`](../docs/CHANGELOG.md) — v1-v49 后端变更
- [`../docs/DEPLOY.md`](../docs/DEPLOY.md) — 完整部署流程
- [`../README.md`](../README.md) — monorepo 总入口