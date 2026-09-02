# 红果漫剧 Monorepo

一个自托管的红果/字节火山漫剧播放器:把红果官方 web/app 的内容索引 + 播放能力封装成 **Windows PC 桌面版**、**安卓平板版** 和 **NAS 后端** 三端,统一前端 UI,可搜索、缓存、连播。

> ⚠️ **免责声明**:本项目仅用于个人学习与技术研究。内容版权归红果/字节跳动及相关版权方所有,请勿用于商业用途。数据来自公开接口,请遵守目标站点服务条款。

---

## 一、仓库结构

```
hongguo-monorepo/
├── pc/                      # PC 版:Electron + NSIS 安装版,自带 PyInstaller 打包的 Python 后端
│   ├── main.js              #   Electron 主进程
│   ├── package.json         #   electron-builder 配置
│   └── backend/
│       ├── main_server.py   #   Flask 入口(端口 5137)+ NAS prefs 同步
│       ├── server.py        #   HTTP API(播放/搜索/缓存/手动索引)
│       ├── hongguo_core.py  #   核心库:签名/解析/下载/净化(与 NAS 端共用)
│       ├── static/index.html#   前端 UI(三端共用基线)
│       └── liushen/         #   字节"流沙"签名 SDK(依赖)
├── android/                 # 安卓平板版:纯 WebView 壳 APK(UI 从 NAS 后端加载)
│   └── android/app/src/main/java/com/hongguo/manju/MainActivity.java
├── nas-backend/             # NAS 后端:Docker 容器(Flask :8000),安卓版的后端
│   ├── server.py            #   HTTP API(与 PC 版同构)
│   ├── hongguo_core.py      #   核心库(与 PC 版共用)
│   ├── mp4_sanitize.py      #   MP4 净化:剥 CENC 伪加密 box(saio/saiz/senc/cslg)
│   ├── entrypoint.sh        #   容器启动时 DoH 解析域名注入 /etc/hosts(绕 DNS 劫持)
│   ├── Dockerfile / docker-compose.yml
│   └── static/index.html    #   前端 UI(三端共用基线)
├── docs/                    # 文档
│   ├── ARCHITECTURE.md        # 架构总览 + 数据流 + 设计决策
│   ├── SECONDARY_DEV.md       # ★ 二次开发指南(API 清单 / 构建 / 扩展点 / 踩坑)
│   ├── DEPLOY.md              # NAS 部署 + 升级
│   └── CHANGELOG.md           # 变更日志
└── .github/workflows/       # CI:build-pc.yml + build-android.yml
```

---

## 二、三端架构

```
┌──────────────┐         ┌──────────────────┐
│  PC 版        │         │  安卓平板版       │
│  Electron    │         │  WebView 壳 APK  │
│  本地后端:5137│         │  UI 加载 NAS 前端 │
└──────┬───────┘         └────────┬─────────┘
       │                          │ HTTP
       ▼                          ▼
┌──────────────────┐    ┌──────────────────────┐
│  PC 本地后端      │    │  NAS 后端(Docker)    │
│  Flask + ffmpeg  │◄──►│  Flask + ffmpeg :8000│  ← prefs 观看记录以 NAS 为权威,双端同步
└──────┬───────────┘    └──────────┬───────────┘
       │                           │
       └───────────┬───────────────┘
                   ▼ HTTP(下载加密视频)
        ┌─────────────────────────┐
        │ 红果/字节火山 CDN        │
        │ hongguoduanju.com 等    │
        └─────────────────────────┘
```

- **PC 版**:Electron 壳 + 本地 Python 后端,独立可用;观看记录/收藏通过 NAS 同步
- **安卓版**:APK 只是 WebView 壳,所有 UI/逻辑来自 NAS 后端 → 前端改动无需重新打包 APK
- **前后端共用**:`server.py` / `hongguo_core.py` / `static/index.html` 在 pc 与 nas-backend 两份维护,改动需同步(cp 或脚本)

---

## 三、快速开始

### 3.1 NAS 后端(安卓版后端)

```bash
cd nas-backend
# 本地起(调试)
pip install -r requirements.txt -r liushen/requirements.txt
python server.py          # 监听 0.0.0.0:8000
# 或 Docker
docker compose up -d --build
# 浏览器打开 http://localhost:8000
```

### 3.2 PC 版(开发模式)

```bash
cd pc
npm install               # Electron + electron-builder
cd backend
pip install -r requirements.txt
cd ..
npm start                 # 启动 Electron,自动拉起 Python 后端(5137)
```

### 3.3 PC 版(打包安装版)

```bash
cd pc
# 1) 先打 Python 后端 exe
cd backend
python -m PyInstaller backend.spec --workpath _pytmp --distpath dist_backend --clean
cd ..
# 2) 再打 NSIS 安装版
npx electron-builder --win nsis
```

### 3.4 安卓版(构建 APK)

```bash
cd android/android
./gradlew assembleRelease   # 产物: app/build/outputs/apk/release/app-release.apk
```

> 首次安装 APK 后在配置页填写你的 NAS 后端地址(`http://<NAS_IP>:8000`)。

---

## 四、核心机制(二开必读)

| 机制 | 位置 | 说明 |
|------|------|------|
| **搜索** | `search_series()` | 官方 suggestion API 优先 + 本地全量索引兜底 + manual 索引(字节内部剧)优先 |
| **视频签名** | `resolve_video_url()` / `sign_json_request()` | 字节火山视频模型接口 + 流沙签名(liushen SDK)拿真实播放 URL |
| **解密播放** | `/api/play` | 下载 CDN 加密 MP4 → ffmpeg `-decryption_key` 解密 → `mp4_sanitize.py` 净化 → faststart → 本地 `/local-video/{vid}.mp4` |
| **缓存** | `stream_manager` | 异步下载+净化到 `cache/videos/`,连播预取后 3 集,命中即秒开(见 v48 缓存优先) |
| **手工剧集索引** | `/api/quickapp_parse` | 粘贴 quickapp 分享链接 → 解析 SSR `_ROUTER_DATA` 提取 100 集 vid 列表 → 录入 manual 索引,前端立即可搜(web 搜不到的字节内部剧) |
| **观看记录同步** | `/api/prefs` | NAS 为权威,PC 本地为离线缓存,双端收藏/进度互通 |
| **DNS 劫持绕过** | `entrypoint.sh` | 容器启动时阿里 DoH 解析全部相关域名注入 `/etc/hosts`(解决 NAS 上 Clash TUN 劫持到 fake-ip 段) |

**播放完整链路**:

```
用户点某一集
  → GET /api/play?vid=xxx
  → [v48] 缓存优先:cache/videos/{vid}.mp4 + meta 存在 → 直接返回 /local-video/{vid}.mp4(秒开,不碰网络)
  → 未命中:resolve_video_url() 拿 CDN URL + content_key
      → 下载 {vid}.raw.mp4 → ffmpeg -decryption_key 解密重封装 → mp4_sanitize 净化 → faststart
      → 写 meta.json → 返回 /local-video/{vid}.mp4
  → 前端 <video> 播放(WebView Chrome 内核硬解 HEVC)
```

---

## 五、API 速览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/search?keyword=&tab=` | 搜索(tab: 空=全部 / 1=短剧 / 2=漫剧),manual 优先 |
| GET | `/api/series?series_id=` | 剧集详情(含全集 vid 列表) |
| GET | `/api/play?vid=&quality=&mode=` | 播放(mode=download 走下载;默认直链解析+缓存优先) |
| GET | `/api/cache` | 缓存列表 / POST 清空 |
| GET | `/api/cache/range?sid=&from=&to=` | 批量预缓存某个区间(连播预取用) |
| GET | `/api/task?vid=` | 查询下载任务进度 |
| GET | `/stream/<filename>` | 边下边播(阻塞流式,2% 即可起播) |
| GET | `/local-video/<vid>.mp4` | 本地缓存视频(send_file + Range) |
| GET | `/api/cdn?u=<url>` | CDN 代理(无 Referer,WebView 防盗链绕行) |
| POST | `/api/quickapp_parse {"url": ...}` | 录入 quickapp 分享链接到 manual 索引 |
| GET | `/api/manual_series` | 查看 manual 索引 |
| GET/POST | `/api/prefs` | 观看记录/收藏(PC 端走 NAS 同步) |
| GET/PUT | `/api/settings` | 应用设置(缓存目录等) |

---

## 六、二次开发

- 📖 **[docs/SECONDARY_DEV.md](docs/SECONDARY_DEV.md)** — 二次开发指南:环境搭建、代码地图、常用 API、扩展点(新剧源/CDN)、已知坑、打包发布
- 📖 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — 架构总览 + 数据流 + 设计决策
- 📖 **[docs/DEPLOY.md](docs/DEPLOY.md)** — NAS Docker 部署
- 📖 **[docs/CHANGELOG.md](docs/CHANGELOG.md)** — 变更日志

---

## 七、技术栈

| 端 | 技术 |
|----|------|
| PC | Electron + Node.js + Python 3.11(Flask) + PyInstaller + electron-builder/NSIS |
| Android | Android SDK 34 + WebView + Gradle 8.7(AGP 8.5.2) |
| NAS 后端 | Python 3.11 + Flask + ffmpeg + Docker Compose |
| 前端 | 单文件 index.html(原生 JS,无构建) |
| CI | GitHub Actions(windows-latest / ubuntu-latest) |

## 八、已知限制

- 安卓版依赖局域网内可访问的 NAS 后端(不走公网)
- 部分 CDN 播放 URL 带时间戳签名,过期后需重新解析
- NAS 上若存在透明代理(Clash TUN 等),需保留 `entrypoint.sh` 的 DoH hosts 注入
- 版权内容仅供学习,勿商用分发
