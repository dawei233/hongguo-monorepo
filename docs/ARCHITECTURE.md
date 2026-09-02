# 红果漫剧 架构文档

> **最后更新**：2026-08-22（v49 monorepo 重构）
> **作者**：WorkBuddy AI
>
> **🤖 新接手的 AI Agent？先读 [`AGENT_CONTEXT.md`](AGENT_CONTEXT.md)** —— 项目速查 + 10 大踩坑清单

---

## 一、整体架构

### 1.1 双版本架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         用户设备层                                   │
│                                                                     │
│  ┌──────────────────────────┐    ┌──────────────────────────┐        │
│  │  PC 版（Windows）        │    │  安卓平板版              │        │
│  │  Electron + Edge WebView│    │  Android APK + WebView   │        │
│  │  文件: pc/发布版/        │    │  文件: android/发布版/   │        │
│  │        红果漫剧.exe      │    │        红果漫剧.apk      │        │
│  └────────┬─────────────────┘    └────────┬─────────────────┘        │
│           │                               │                          │
└───────────┼───────────────────────────────┼──────────────────────────┘
            │ 127.0.0.1:5137              │ HTTP (平板 → NAS:8000)
            ▼                               ▼
┌──────────────────────┐        ┌──────────────────────────┐
│  PC 版后端           │        │  NAS 后端                │
│  Python + Flask      │        │  Docker 容器 (hongguo)   │
│  集成在 exe 内       │        │  Python + Flask + ffmpeg │
│  文件: pc/backend/   │        │  文件: nas-backend/      │
│                      │        │  部署: <NAS_IP>:8000   │
└────────┬─────────────┘        └────────┬─────────────────┘
         │                               │
         └───────────────────────────────┘
                         │
                         ▼ HTTP (下载加密视频)
            ┌─────────────────────────────────┐
            │  红果短视频 CDN                  │
            │  hongguoduanju.com              │
            │  - HEVC 1080p / H.264 720p     │
            │  - 防盗链: saio/saiz/senc 标记  │
            └─────────────────────────────────┘
```

---

## 二、各组件职责

### 2.1 PC 版（`pc/`）

| 文件/目录 | 作用 |
|-----------|------|
| `main.js` | Electron 主进程（创建窗口 + 启动 Python 后端 + 管理生命周期） |
| `preload.js` | 渲染进程预加载脚本（暴露 IPC API 给前端） |
| `package.json` | electron-builder 配置（NSIS 安装版 + 中文语言 + 可选安装路径） |
| `backend/main_server.py` | Flask HTTP 服务（端口 5137）+ Chromium WebView 通信桥接 |
| `backend/hongguo_core.py` | 视频签名 + 搜索 + 详情解析（复用 NAS 版核心库） |
| `backend/static/index.html` | 前端 UI（**所有版本共用此 HTML**） |
| `backend/bin/` | FFmpeg 可执行文件（PyInstaller 打包时捆绑） |
| `backend/dist_backend/红果后端.exe` | PyInstaller 单文件后端 exe（electron-builder extraResources 读取） |
| `发布版/红果漫剧_安装版_Setup.exe` | NSIS 安装版（最终交付物） |

**启动流程**：
1. 用户双击 `红果漫剧.exe`
2. Electron 主进程启动 → `main.js` 启动 Python 后端子进程（`红果后端.exe`）
3. Python 后端监听 `127.0.0.1:5137` + Flask 服务 `static/index.html`
4. Electron 创建 `BrowserWindow` → `loadURL('http://127.0.0.1:5137/')`
5. 前端展示 + 用户交互 → 调用 `/api/*` JSON 接口

---

### 2.2 安卓平板版（`android/` + `nas-backend/`）

#### 2.2.1 安卓端（`android/android/app/src/main/`）

| 文件 | 作用 |
|------|------|
| `java/com/hongguo/manju/MainActivity.java` | WebView 壳 + JS Bridge (`hgBridge`) |
| `assets/index.html` | **冗余 HTML**（实际从 NAS 加载，不读 assets） |
| `AndroidManifest.xml` | 网络权限 + 全屏主题 |
| `build.gradle` | 依赖：AndroidX + media3 1.4.1（保留 ExoPlayer 备用） |
| `signing/hongguo.jks` | APK 签名密钥 |
| `.github/workflows/build.yml` | GitHub Actions 自动构建 APK |

**关键决策**（v34+）：**所有 UI 由 NAS 后端返回的 HTML 提供**，APK 只做 WebView 壳。这样前端代码修改不需要重新构建 APK。

#### 2.2.2 NAS 后端（`nas-backend/`）

| 文件 | 作用 |
|------|------|
| `server.py` | Flask 主程序（端口 8000，监听 0.0.0.0） |
| `hongguo_core.py` | 流沙签名 + 视频 URL 解析 + 搜索/详情 API |
| `mp4_sanitize.py` | MP4 净化器（剥 saio/saiz/senc/cslg 等 CENC 标记） |
| `liushen/` | 流沙签名 SDK（依赖） |
| `static/index.html` | 前端 UI（**与 PC 版共用**） |
| `Dockerfile` + `docker-compose.yml` | 容器化部署 |
| `app_nas.py` | 容器入口（设 `HONGGUO_DATA_DIR=/data`） |
| `requirements.txt` | Python 依赖 |

**部署架构**：
```
<部署目录>/hongguo/        # NAS 上 git clone 的源码目录
├── Dockerfile
├── docker-compose.yml
├── server.py / hongguo_core.py / ...
├── static/index.html
└── data/                          # volume mount 到容器 /data
    ├── cache/videos/             # 视频缓存（自动清理）
    ├── prefs.json                # 用户偏好
    └── config.json
```

---

## 三、数据流

### 3.1 浏览列表

```
[用户] → 点 [Tab 推荐/短剧/漫剧]
  ↓
[前端 JS]
  fetch(`/api/search?keyword=&tab=<tab>`)
  ↓
[NAS server.py / PC main_server.py]
  → search_series() 调用 hongguoduanju.com 分类 API
  → 转换字段名 {series_id, title, cover, ...}
  → 返回 {items, total, source}
  ↓
[前端 JS]
  renderGrid(items) 渲染瀑布流卡片
  ↓
[用户] 看到推荐列表
```

### 3.2 播放单集（核心流程）

```
[用户] → 点 [剧集卡片] → 进入详情页
  ↓
[前端 JS]
  fetch(`/api/series?series_id=<id>`)
  ↓
[后端] get_series_detail() → 解析 episodes[]
  ↓
[前端] renderEpisodes(episodes) → 显示集数列表
  ↓
[用户] 点 [某集]
  ↓
[前端]
  const url = await apiPlayUrl(sid, vid)
  = fetch(`/api/play?vid=<vid>&sid=<sid>&client_cap=hevc`)
  ↓
[后端 /api/play 流程]
  1. resolve_video_url(vid, sid) → CDN 加密 URL + content_key
  2. raw_path = /data/cache/videos/{vid}.raw.mp4
  3. if not raw_path.exists(): 下载到 raw_path
  4. ffmpeg -decryption_key + -i raw_path -c copy +faststart → {vid}.mp4
  5. mp4_sanitize.py 二次净化（剥 CENC box）
  6. 写 {vid}.mp4.meta.json (sid/title/ep_no/quality/processed_at)
  7. 返回 {ok:true, task:{url:"/local-video/{vid}.mp4"}}
  ↓
[前端]
  <video src="/local-video/{vid}.mp4">
  ↓
[后端 /local-video/<vid>.mp4]
  send_file(CACHE_DIR/{vid}.mp4) with Range support
  ↓
[WebView/video 标签] 直接播放（解密+净化+faststart MP4）
```

### 3.3 预缓存（仅平板版，平板需要）

```
[用户] 点 [连播按钮] → btn-auto active
  ↓
[前端 triggerPrefetchNext3()]
  → fetch(`/api/cache/range?sid=&quality=&from=2&to=4`)
  ↓
[后端 /api/cache/range]
  → stream_manager.start(vid, quality) for each ep
  ↓
[stream_manager worker 后台异步]
  1. ffmpeg -decryption_key + -i URL -c copy +faststart → {vid}.mp4
  2. mp4_sanitize.py 二次净化
  3. 写 {vid}.mp4 + {vid}.mp4.meta.json
  ↓
[用户切下一集] → /api/play 命中预缓存 → 秒开
```

---

## 四、关键设计决策

### 4.1 为何平板端前后端分离（v22+）？

**问题**：原方案安卓 APK 内嵌 Chaquopy + Python 后端，APK 体积大（80MB+），签名逻辑更新需要重新打包。

**解决**：后端搬到 NAS（Docker），APK 退化为纯 WebView 壳。**前端 HTML 修改 NAS 一处生效，安卓自动跟随**。

**代价**：安卓端必须能访问 NAS IP（同一局域网）。

### 4.2 为何共用 static/index.html？

**所有版本**（PC + 安卓 + NAS）的 HTML 完全相同——包括搜索、详情、播放 UI。**改一处即可同步到所有版本**。流水线：

```
修改 nas-backend/static/index.html
  ↓
  git commit + push main
  ↓
  NAS docker compose build + up    [平板端自动生效]
  ↓
  cp nas-backend/static/index.html pc/backend/static/index.html
  ↓
  PyInstaller 重打 红果后端.exe
  ↓
  electron-builder NSIS 打安装版   [PC 版生效]
```

**安卓 APK 不需要重新构建**（HTML 在 APK 内是冗余，实际从 NAS 加载）。

### 4.3 为何 mp4_sanitize.py 必须有？

红果 CDN 的视频**带 CENC 伪加密标记**（saio/saiz/senc box），ffmpeg 解密后这些标记还在容器里。WebView `<video>` 和 ExoPlayer 看到 saio 找不到 senc 会拒绝播放。

**mp4_sanitize.py**：剥 saio/saiz/senc/cslg 等 box，重写 stco/co64 偏移，输出 ISO BMFF 标准 MP4。

### 4.4 为何 PC 版用 Electron 而不是 pywebview？

| 维度 | Electron | pywebview |
|------|----------|-----------|
| 窗口/菜单控制 | 完善 | 基础 |
| 全屏体验 | 真全屏 + 自定义控件 | 浏览器 fullscreen API |
| 安装包体积 | 大（150MB+） | 小（80MB） |
| 打包复杂度 | 中（electron-builder + NSIS） | 低（PyInstaller） |
| 维护活跃度 | 高 | 低 |

**结论**：选 Electron 是为了未来扩展（多窗口、菜单栏、托盘、系统通知），pywebview 后期维护困难。

---

## 五、目录约定

### 5.1 缓存目录命名（`/data/cache/videos/`）

| 文件 | 含义 | 来源 |
|------|------|------|
| `{vid}.raw.mp4` | CDN 下载的原始加密文件 | `/api/play` 下载阶段 |
| `{vid}.mp4` | 解密 + 净化 + faststart 最终产物 | `/api/play` 净化 / `stream_manager` 缓存 |
| `{vid}.mp4.meta.json` | 元数据伴生（sid/title/ep_no/quality/processed_at） | `/api/play` / `stream_manager` 写入 |
| `{vid}_h264.mp4` | （旧版本命名，已废弃，保留兼容） | v37 及之前 |

**前端加载路径**：`/local-video/{vid}.mp4`（send_file + Range 支持）。

### 5.2 模块依赖

```
hongguo-monorepo/
├── pc/
│   └── backend/
│       ├── static/index.html  ← 复用 nas-backend/static/index.html
│       └── hongguo_core.py    ← 复用 nas-backend/hongguo_core.py（核心签名/解析）
│
├── android/
│   └── android/app/src/main/assets/index.html  (冗余，实际从 NAS 加载)
│
└── nas-backend/
    ├── static/index.html       ← 三版本共用基线
    ├── hongguo_core.py          ← 核心库
    ├── server.py                ← 唯一后端入口
    ├── mp4_sanitize.py
    └── liushen/
```

---

## 六、典型工作流

### 6.1 修改前端 UI

```bash
# 1. 改 HTML
$EDITOR nas-backend/static/index.html

# 2. 本地测试
cd nas-backend
docker compose build --no-cache
docker compose up -d
# 浏览器访问 http://localhost:8000

# 3. 测试通过后同步 PC 版
cp nas-backend/static/index.html pc/backend/static/index.html

# 4. 重打 PC 后端 exe
cd pc/backend
pyinstaller backend.spec --workpath /tmp/build --distpath dist

# 5. 打 NSIS 安装版
cd ..
npx electron-builder --win nsis
# 产物: release/红果漫剧_安装版_Setup_1.0.0.exe

# 6. commit
git add -A
git commit -m "v49: <修改内容>"
git push origin main
```

### 6.2 修改后端 API

```bash
# 1. 改 Python
$EDITOR nas-backend/server.py

# 2. 同步到 PC 版（共用核心）
cp nas-backend/hongguo_core.py pc/backend/hongguo_core.py

# 3. NAS 重建
cd <部署目录>/hongguo   # NAS 端源码目录
sudo docker compose build --no-cache
sudo docker compose up -d

# 4. 清缓存（修改 mp4_sanitize.py 时必须）
sudo docker exec hongguo bash -c 'rm -f /data/cache/videos/*.mp4 /data/cache/videos/*.mp4.meta.json /data/cache/videos/*.raw.mp4'

# 5. PC 端验证
cd pc
npm start

# 6. commit
git add -A
git commit -m "v49: <后端修改>"
git push origin main
```

### 6.3 修改安卓端 Java

```bash
# 1. 改 Java
$EDITOR android/android/app/src/main/java/com/hongguo/manju/MainActivity.java

# 2. GitHub Actions 自动构建
git add -A
git commit -m "v49: <Java 修改>"
git push origin main
# → .github/workflows/build.yml 自动跑 → 产物在 Artifacts
```

---

*文档结束。如有架构变更请同步更新本文档。*