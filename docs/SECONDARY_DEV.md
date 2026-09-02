# 二次开发指南

> 面向想接手/改造/扩展这个项目的开发者。先读根 [README](../README.md) 了解整体,再看本文。

---

## 1. 环境准备

| 用途 | 需要 | 版本建议 |
|------|------|----------|
| PC 版开发 | Node.js + npm | Node 18+ |
| Python 后端 | Python | 3.11+ |
| Android | Android Studio / SDK + JDK | JDK 17,SDK 34,AGP 8.5.2,Gradle 8.7 |
| NAS 后端 | Docker + Compose | 任意 Linux(生产建议 NAS) |
| ffmpeg | 系统 PATH 或 `bin/` 目录 | 任意新版(需支持 `-decryption_key` 与 HEVC) |

Python 依赖:

```bash
pip install -r nas-backend/requirements.txt -r nas-backend/liushen/requirements.txt
```

> `liushen/` 是字节"流沙"签名 SDK(搜索/详情/视频模型接口签名用),**不要动里面的加密算法**,改坏会导致全部接口 403。

---

## 2. 代码地图

### 2.1 核心库 `hongguo_core.py`(PC 与 NAS 各一份,需保持同步)

| 函数/类 | 职责 |
|---------|------|
| `ensure_device()` | 设备注册(首次生成 device_id/install_id 存 `config.json`) |
| `sign_json_request()` | 流沙签名 + 构造字节接口请求 |
| `search_series()` | 搜索三路:官方 suggestion → 本地全量索引 → manual 索引 |
| `get_series_detail()` | 剧集详情 + 全集 vid 列表(manual 优先) |
| `resolve_video_url()` | 视频模型解析:拿 CDN URL + content_key(解密密钥) |
| `decrypt_spade_url()` | 播放 URL 解密(字节 main_url 是密文) |
| `stream_manager` | 异步下载任务队列:下载→解密→净化→写缓存 |
| `build_series_index()` | 构建本地全量剧集索引(web 分类 API 爬取) |
| `add_manual_series()` | 手工剧集录入(quickapp 解析产物) |

### 2.2 HTTP 层 `server.py`

| 路径 | 说明 |
|------|------|
| `/api/search` | 搜索(带 tab 过滤) |
| `/api/series` | 详情 + 全集 |
| `/api/play` | **核心**:缓存优先 → 直链解析 → 下载净化 → 本地播放 |
| `/api/cache/range` | 区间预缓存(连播预取) |
| `/api/quickapp_parse` | quickapp 链接录入 manual 索引 |
| `/api/prefs` | 观看记录(PC 端同步到 NAS) |
| `/stream/*` `/local-video/*` | 边下边播 / 本地缓存视频 |

### 2.3 前端 `static/index.html`(三端共用基线)

| 区块 | 作用 |
|------|------|
| `renderGrid()` | 搜索/分类瀑布流 |
| `playEpisode()` / `loadVideo()` | 点集播放,调 `/api/play` |
| `triggerPrefetchNext3()` | 连播预取后 3 集(调 `/api/cache/range`) |
| `downloadEpisodeToCache()` | 手动缓存某一集 |
| `ensureFullPlay()` | 完整文件就绪后接管播放 |

前端**没有构建步骤**,单文件 HTML 直接改直接生效(改完同步三端副本)。

---

## 3. 三端本地跑通

### 3.1 最简路径:只跑 NAS 后端(安卓版一切功能)

```bash
cd nas-backend
pip install -r requirements.txt -r liushen/requirements.txt
python server.py
# 浏览器 http://localhost:8000 即可搜索/播放/缓存
```

### 3.2 PC 版

```bash
cd pc && npm install
cd backend && pip install -r requirements.txt
cd .. && npm start
```

### 3.3 Android

```bash
cd android/android
./gradlew assembleRelease
# 产物 app/build/outputs/apk/release/app-release.apk
# 首次打开填 NAS 地址
```

---

## 4. 播放链路与缓存(二开最常动的地方)

```
用户点某集 → /api/play?vid=X
  ① 缓存优先(v48):cache/videos/X.mp4 + X.mp4.meta.json 都在 → 秒开
  ② 未命中:resolve_video_url() → CDN URL + content_key
  ③ 下载 raw → ffmpeg -decryption_key 解密 → mp4_sanitize 净化 → faststart
  ④ 写 meta → /local-video/X.mp4
```

**常见改动点**:

- 想改清晰度策略 → `resolve_video_url()` 的 quality 匹配 + `/api/play` 的 `want_hq` 逻辑
- 想换缓存目录 → `set_cache_dir()` / `HONGGUO_DATA_DIR` 环境变量
- 想调缓存保留时长 → `server.py` 里 `CACHE_AGE_LIMIT_SEC`(默认 24 小时)
- 想加剧源 → 仿照 `quickapp_parse` 写新的解析器,把结果喂给 `add_manual_series()`

---

## 5. 扩展点

### 5.1 接入新的剧源平台

1. 找到该平台的"分享页/详情页"(无需登录即可 fetch 的 SSR 或 API)
2. 写一个解析函数:输入 URL → 输出 `{title, series_id, episode_cnt, vid_list, cover}`
3. 在 `server.py` 注册新路由(参考 `/api/quickapp_parse`),解析结果调 `add_manual_series()`
4. 前端无需改动(搜索/详情/播放走同一套 API)

### 5.2 适配新的播放 CDN

- 字节系 CDN 域名族:`vXX-cold.douyinvod.com`、`vXX-reading-video-d.qznovelvod.com`、`vXX-hongman.qznovelvod.com`、`*.bytegoofy.com`、`*.snssdk.com`
- 如果部署在**有透明代理(Clash TUN)的 NAS** 上,把新域名加进 `nas-backend/entrypoint.sh` 的 `DOMAINS` 列表(容器启动时 DoH 解析注入 hosts,绕 fake-ip 劫持)
- 如果 CDN URL 带防盗链(Referer/TLS 指纹),走 `/api/cdn?u=` 代理(后端无 Referer 抓取)

### 5.3 新增客户端端(电视/手机 App 等)

- 只要做成"加载 NAS 前端 + 调 `/api/*`"的壳即可(参考 android 的 WebView 壳)
- 观看记录天然共享(走 NAS `/api/prefs`)

---

## 6. 已知坑(踩过,提前避)

| 坑 | 现象 | 解决 |
|----|------|------|
| **字节接口 403** | 搜索/详情全挂 | 不要改 `liushen/` 签名 SDK;确认 `ensure_device()` 注册过设备(config.json 存在) |
| **ffmpeg "Connection timed out"** | 下载失败 | 容器内 DNS 被劫持到 fake-ip → 检查 entrypoint.sh hosts 是否注入 |
| **WebView 播放失败** | video 元素报错 | MP4 必须经过 `mp4_sanitize.py` 净化(CENC 伪加密 box 会导致 demuxer 拒绝) |
| **WinError 2 / 找不到 ffmpeg** | PC 版播放失败 | PyInstaller 打包时把 ffmpeg.exe 放进 `backend/bin/`(见 backend.spec) |
| **stdout 编码崩溃(Windows)** | 报 `'gbk' codec can't encode character` | `main_server.py` 顶部 `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` |
| **弹出 cmd 黑框(Windows)** | 切剧集闪黑框 | 所有 `subprocess.run/Popen` 加 `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)` |
| **缓存命中还加载半天** | 切下一集慢 | 确认 `/api/play` 的缓存优先检查在网络解析之前(v48 已修) |
| **AGP 版本不匹配** | Gradle 构建报 Minimum supported | AGP 8.5.2 需要 Gradle 8.7+(见 gradle-wrapper.properties) |
| **红果反爬 500** | 网页接口被拦 | WEB_UA 用 Chrome 120 + 带 Referer/Accept 头(见 hongguo_core.fetch_text) |

---

## 7. 发布与 CI

| 产物 | Workflow | 触发 | 产物位置 |
|------|----------|------|----------|
| PC 安装版 | `.github/workflows/build-pc.yml` | push 改动 `pc/**` | Actions Artifacts: `hongguo-pc-setup` |
| Android APK | `.github/workflows/build-android.yml` | push 改动 `android/**` | Actions Artifacts: `hongguo-manju-release` |

- PC 版版本号由 `GITHUB_RUN_NUMBER` 自动递增(见 workflow 中"设置构建版本号"步骤)
- Android release 无正式 keystore 时回退 debug.keystore 签名(可安装,系统提示"来源未签名"属正常)
- NAS 后端无 CI,改完 `docker compose build && up -d` 即可

---

## 8. 环境变量一览

| 变量 | 默认 | 说明 |
|------|------|------|
| `HONGGUO_PORT` | `5137` | PC 后端端口 |
| `NAS_URL` | `http://192.168.1.100:8000` | PC 端同步观看记录/manual 索引的 NAS 地址 |
| `HONGGUO_DATA_DIR` | `/data`(容器) | 数据目录(缓存/设备 ID/prefs) |
| `NAS_DATA_DIR` | `/data` | 缓存视频目录 |
| `KEYSTORE_FILE/PASSWORD/KEY_ALIAS/KEY_PASSWORD` | - | Android 签名(CI 注入) |
| `VERSION_CODE` | `1` | Android versionCode(CI 注入) |

---

*文档结束。保持与 README.md / ARCHITECTURE.md 同步更新。*
