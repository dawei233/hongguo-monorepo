# 变更日志 (Changelog)

> **格式**：每个版本记录变更点 + 已知问题 + 关联 commit
> **更新规则**：每次发布新版本时追加新章节，**绝不修改旧章节**——保证历史可追溯

---

## v51 (2026-09-02) — 修复与优化：缓存路径/净化调用/清理策略/索引性能

### 修复（bug）
- 🐛 **PC 版 `/api/play` 下载产物路径错位**：`NAS_DATA_DIR` 硬编码 `/data`，PC 版没人设该变量 → Windows 上 `Path("/data")` 指向盘符根 `\data`，产物与 `/local-video` 读的 `CACHE_DIR` 对不上 → 404。统一改用 `hongguo_core.CACHE_DIR()`（尊重 settings.json 自定义目录）
- 🐛 **PC 版净化步骤永远静默失败**：`python /app/mp4_sanitize.py` 子进程只在容器里存在，PC 上必失败且被吞。改为进程内 `import mp4_sanitize` 调用（NAS/PC/打包三端通用），`server.py` 与 `stream_manager._worker` 均已替换
- 🐛 **缓存清理策略自相矛盾**：原来"7 天主清理 + 24h mtime 保底清理"并行，24h 保底先删 → 7 天主清理永远轮不到（v48 想保留缓存的目标实际没生效）。按用户确认统一为 **24 小时单策略**，日志/注释同步修正
- 🐛 **清理线程路径错位**：`CACHE_VIDEOS_DIR` 硬编码 `/data`，PC 版清理线程根本不工作、自定义缓存目录永不清。改为动态 `CACHE_DIR()`
- 🐛 **`/api/play` raw 产物删除条件写反**：meta 写成功时反而不删 `{vid}.raw.mp4` → 加密中间产物残留进 `/api/cache` 列表污染 UI
- 🐛 **`/api/cache` POST 清空不删 meta**：只删 `*.mp4`，`.meta.json` 残留成孤儿。现在连带删除 + 清孤儿 meta
- 🐛 **前端切 tab 清空搜索框无效**（v48 遗留）：`$('searchbox')` 找不到元素（是 class 不是 id），改 `$('kw')`
- 🐛 **封面 URL 未转义**：`renderGrid` / `hcardHTML` 的 `<img src>` 补 `escapeHtml`

### 优化（性能/结构）
- ⚡ **`/api/play` 少一遍全文件 remux**：步骤 A 已带 `+faststart`，删掉多余的"二次 faststart remux"（原先把整个视频完整读+写一遍却什么都不改）
- ⚡ **全量索引落盘 + 并发**：`build_series_index` 原先串行抓 126 页且只有内存缓存（重启/TTL 过期就同步重抓几十秒）。现在 6 条链并发（链内仍限速）+ 落盘 `cache/series_index.json`（6h TTL），重启秒加载
- ⚡ **解析重试收敛**：HQ 源 8 次×3s（最坏阻塞 3 分钟）→ 3 次×2s，并修复 `info` 未初始化的潜在 NameError
- 🧹 **双份 `hongguo_core.py` 合并**：两端差异仅 9 行且全部可合并（`CREATE_NO_WINDOW` 已 getattr 兼容），合并后不再漂移；`mp4_sanitize.py` 补齐到 `pc/backend/`
- ✨ 新增 `scripts/sync_core.py`：共用文件（hongguo_core / mp4_sanitize / index.html）一键同步 + `--check` 校验，替代人肉 cp
- 📦 `backend.spec` 补 `mp4_sanitize` hiddenimport（打包不再依赖路径碰运气）

### 关联 commit
- v51 修复与优化（本仓库新 GitHub 库首个功能性 commit）

### 已知问题
- `/api/play` 默认模式仍是"完整下载+净化后才返回"（起播慢），真正的异步流式播放需要重构播放链路，本次未动


---

## v52 (2026-09-03) — 性能优化 + 智谱 AI 搜剧

### 优化（按 OPTIMIZATION_PLAN 批次 A/B/C）
- ⚡ **T3**: dev server → waitress 生产级 WSGI（纯 Python、Windows 友好；解决 Werkzeug 206+chunked 边界 bug；threads=8）。`server.py` / `main_server.py` 三处替换，缺包时自动回退
- ⚡ **T4**: 裸 `requests.get/post` → 全局 `SESSION`（连接池复用 + GET 在 429/5xx 时指数退避重试 2 次）。`Retry(allowed_methods=["GET"])` 关键：签名视频接口/设备注册 POST 绝不允许自动重放。9 处 GET + 1 处签名 POST 全部迁移，仅 `device_register` 一处保留裸 POST
- ⚡ **T5**: browse 后台 worker 上限 `BROWSE_PREFETCH_LIMIT=2000`，到达后长眠 1 小时（保留进程可调参）。之前对 sitemap 47 万 ID 永不停歇抓取，对官方站点持续压力+白耗带宽；按需抓取 (`browse_ensure`) 不受限制
- ⚡ **T7**: `/api/play` 三段找源逻辑（60 行）抽到 `hongguo_core.resolve_with_fallback()` 纯函数，`api_play` → 3 行调用。两份 `server.py` 60 行重复彻底消除
- 🔒 **T9**: `/api/cdn` 代理加域名白名单（`CDN_ALLOW_SUFFIXES` + `CDN_ALLOW_KEYWORDS` 双重匹配），防被当任意 HTTP 代理跳板滥用
- 🧪 **T8**: 11 → 13 个冒烟测试固化为 `nas-backend/tests/test_smoke.py`；新增 `.github/workflows/test.yml`（ubuntu + Python 3.11，跑 pytest + `sync_core.py --check`）

### 新功能
- 🤖 **智谱开放平台 API · AI 智能搜剧**：自然语言描述 → 智谱 GLM 提取 1-3 个关键词 → 走现有 `search_series` 搜索
  - 后端: `GET /api/search_ai?q=想看重生复仇类短剧&tab=1`
  - 模型: `glm-4-flash`（免费额度大、低延迟），通过 OpenAI 兼容协议直连无需 SDK
  - key: 环境变量 `ZHIPU_API_KEY`，未配置时返回 502 + 明确错误（前端降级提示用普通搜索）
  - 前端: 搜索栏新增紫色 `🤖 AI 搜` 按钮；触发后 toast 显示"AI 解读: 重生 / 复仇"
  - prompt 严格约束 GLM 返回 JSON 数组（兼容 markdown 围栏容错）

### 依赖
- 新增 `waitress==3.0.2`、`zhipuai==2.1.5`（CI 装，PyInstaller 打包时 bundled）
- `backend.spec` hiddenimports 补 `waitress` / `zhipuai`

### 已知未做
- **T1 播放链路流式化**：用户本次明确跳过（动主播放链路风险大，需真机回归）
- **T2 mp4_sanitize 流式重写**：内存优化，本轮未做（O(moov) → O(文件大小) 重写，需逐 box byte-identical 验证）
- **T6 详情页 JSON 解析**：需真网验证官方页面 JSON 结构，本轮未做

### 关联 commit
- v52 性能优化 + AI 搜剧（本仓库第二个功能性 commit）

## v49 (2026-08-22) — Monorepo 重构

### 变更
- 🏗️ **架构重构**：三个独立项目（`hongguo-android` / `hongguo-electron` / `hongguo-nas-backend`）合并到单一 monorepo：`hongguo-monorepo/`
- 📁 新目录结构：`pc/`、`android/`、`nas-backend/`、`docs/`
- 📝 新增 4 类文档：`README.md`、`docs/ARCHITECTURE.md`、`docs/CHANGELOG.md`、`docs/DEPLOY.md`
- 🚀 配套 git 脚本：`nas_rebuild.py` / `nas_deploy_v46.py` 等部署脚本（已在 monorepo 外保留）
- 🗑️ 删除发布产物进 git：`pc/发布版/*.exe`、`pc/node_modules/`、`pc/backend/bin/` 等已 gitignore

### 关联 commit
- 初始化 monorepo + 迁移代码 + gitignore（1 个 commit）
- 老目录已标记 deprecated，README 指向新仓库

### 已知问题
- 无

---

## v48 (2026-08-22) — 搜索切 tab + browse 游标 bug

### 变更
- 🐛 修复 PC + 平板 HTML：**搜索后切分类 tab 不清除搜索词**（`switchType` 里 `if (currentKeyword) doSearch(currentKeyword)` 改成清空 + loadHot）
- 🐛 修复 PC + 平板后端 `hongguo_core.py`：**browse 全局游标共享导致 PC 加载数量比平板少**（`_BROWSE_SENT` 改为按 `page` 参数算 offset + `_browse_remaining` 用缓存数 + `browse_ensure` 支持循环抓取）
- ✨ PC 版 HTML 新增无限滚动：`listPage/listTotal/listEnded/listLoading` + `loadMore()` + 滚动监听 +「点击加载更多」按钮
- 📦 PC 版 PyInstaller 重新打包（exe md5 `6cbdeb18a7222ee604c0f78eee635b48`，102MB）

### 关联 commit
- 无（NAS 版未启用 git，PC 版未启用 git）

### 验证
```
客户端A tab=2 第2页: 24 items, total=474333
客户端B tab=2 第2页: 24 items, total=474333  ← 完全一致
推荐 tab 第5页:     24 items, total=475367  ← 47 万总量
```

---

## v47 (2026-08-22) — NAS 平板版连播/预缓存

### 变更
- 🐛 修复 NAS HTML **连播按钮默认 active**（line 624 删除 `class="active"`）
- ✨ 新增 `triggerPrefetchNext3()` 函数：点击连播按钮立即预缓存后 3 集
- ✨ 新增 `timeupdate` 监听器：active + currentTime > 10s 兜底触发预缓存
- ✨ `playEpisode()` 切下一集时调用 `triggerPrefetchNext3` 自动预缓存

### 关联 commit
- 无（NAS HTML 改在 NAS 容器内编辑）

### 验证
- 模拟平板操作：点连播 → 后端 4 个 stream_manager worker 并行下载 + mp4_sanitize 净化 → 切下一集秒开

---

## v46 (2026-08-22) — 项目分离（PC + 平板）

### 变更
- 🏗️ **架构澄清**：明确 PC 版（`hongguo-electron`）+ 平板版（`hongguo-nas-backend` + 安卓 APK）两个独立项目
- 🔧 **关键 bug**：安卓 App `MainActivity.java` `loadUrl(NAS_URL)` 不读 APK 内 `assets/index.html`——但 v44 之前 NAS 上的 HTML 是 **电脑版 HTML**（带平板 CSS），调用 `/api/category/page` 等**电脑版后端 API**，但 NAS 后端没有这些路由 → 推荐/搜索/播放全部失效
- ✅ 修复：用 `hongguo-electron/backend/static/index.html`（electron 版用相对路径 `/api/*`）覆盖 NAS HTML
- 🔧 **v45 配套**：NAS server.py 加 `/api/category/page` 路由 + 修改 `apiPlayUrl`/`apiDetail` 调 `/api/play`/`/api/series`

### 关联 commit
- 无

### 已知问题
- 旧 `hongguo-nas-backend/static/index.html` 是电脑版 HTML，**NAS 项目从未有专门的 NAS 版 HTML**

---

## v45 (2026-08-22) — NAS 后端补 3 个路由

### 变更
- ✨ `server.py` 加 `/api/category/page` 路由（包装 `hongguo_core.fetch_category_page()`）
- 🔧 `static/index.html` `apiPlayUrl` 改调 `/api/play` JSON API
- 🔧 `static/index.html` `apiDetail` 改调 `/api/series` JSON API

### 关联 commit
- 无

### 验证
- `/api/category/page` → 200, 24 items
- `/api/series` → 200, title=虾仁闯洪荒
- `/api/play` → 200, url=/local-video/{vid}.mp4

---

## v44 (2026-08-22) — 修复 v42 命名 bug + 同步平板 HTML

### 变更
- 🐛 修复 **v42 命名 bug**：`/api/play` 里 `raw_path` 和 `h264_path` 改成同一个路径 → 下载完 raw_path 已存在 → 永远跳过 ffmpeg 净化 → 产物含 CENC box → WebView 拒绝播放
- ✅ 恢复 v37 路径分离：`raw_path = {vid}.raw.mp4` + `h264_path = {vid}.mp4`
- 📝 **关键架构坑**：安卓 App `MainActivity.loadServerPage(url)` 直接 `webView.loadUrl(url)` 加载 NAS URL——**不读 APK 内 `assets/index.html`**。NAS 上的 HTML 必须正确，否则前端全错
- 🔧 同步 NAS `static/index.html` 到 v42 安卓版（含 btn-auto 默认不 active + triggerPrefetchNext3）

### 关联 commit
- 无（NAS HTML + server.py 改动在 NAS 容器内）

---

## v43 (2026-08-21) — stream_manager 加 mp4_sanitize

### 变更
- 🐛 修复 **stream_manager worker 产物没净化**：之前只跑 ffmpeg decrypt+copy → 产物含 saio/saiz/senc → WebView 拒绝播放
- ✅ stream_manager worker 末尾加 `python /app/mp4_sanitize.py` 净化
- 🧹 启动时 `_clean_v28_raw_path()` 清理无 meta 伴生的 `{vid}.mp4`（v28 时代残留的 CDN 加密文件）

### 关联 commit
- 无（NAS 修改）

---

## v42 (2026-08-21) — 修复 btn-auto 默认 active

### 变更
- 🐛 修复 `static/index.html` **btn-auto 默认 active**（违反用户偏好"默认不该 active"）
- 🔧 删除 line 684 `class="active"`，加 `triggerPrefetchNext3()` 函数 + timeupdate 兜底

### 关联 commit
- `bbfd300` v42: btn-auto 默认不选中（v41 active 违反用户偏好）

---

## v41 (2026-08-21) — 预缓存只在 btn-auto active 时触发

### 变更
- 🔧 预缓存逻辑加 btn-auto active 条件（不连播不缓存）
- ✨ 点击连播按钮立即预缓存后面 3 集

### 关联 commit
- `a6b08d0` v41: 预缓存只在 btn-auto active 时触发（不连播不缓存）；点连播按钮立即预缓存后面 3 集

---

## v40 (2026-08-21) — 预缓存触发去掉 btn-auto 条件

### 变更
- 🔧 预缓存触发去掉 btn-auto 条件限制（用户手动切下一集也想命中预缓存）
- 🔧 currentQuality 空时默认 video_5（1080p）

### 关联 commit
- `2dfba96` v40: 预缓存触发去掉 btn-auto 条件限制

---

## v39 (2026-08-21) — 回到 v36 接管 fullscreen

### 变更
- 🔧 修 v37 mFsContainer 底层方案失败（WebView hardware layer 永远 opaque 挡住视频画面）
- ✨ 回到 v36 接管 fullscreen + custom view 加到 root 最上层

### 关联 commit
- `9fb1c08` v39: 回到 v36 接管 fullscreen + custom view 加到 root 最上层

---

## v38 (2026-08-21) — 退出全屏用 SCREEN_ORIENTATION_UNSPECIFIED

### 变更
- 🔧 退出全屏用 SCREEN_ORIENTATION_UNSPECIFIED 跟随重力感应
- 🔧 修复 v37 强制 PORTRAIT 被重力转回 LANDSCAPE 产生中间状态错乱

### 关联 commit
- `36549da` v38: 退出全屏用 SCREEN_ORIENTATION_UNSPECIFIED 跟随重力感应

---

## v37 (2026-08-21) — 修 v36 全屏 UI 覆盖 + meta 写入

### 变更
- 🐛 修 v36 全屏 UI 覆盖（custom view 加到 root 底层 + WebView 透明 + player-wrap fs-mode 透明）
- ✨ `timeupdate` 改 `currentTime > 30` 触发预缓存（避免依赖 video.duration）
- 🔧 `/api/play` 净化完成后写 `{vid}_h264.mp4.meta.json`（含 sid/title/ep_no/quality/processed_at）
- 🔧 同时删除 `{vid}.mp4` raw_path 加密文件

### 关联 commit
- `f228306` v37 fix: Bridge.toggleFs 不再引用 mOriginalOrientation
- `aa3d2a2` v37: 修 v36 全屏 UI 覆盖

---

## v36 (2026-08-21) — 真正接管 WebView fullscreen

### 变更
- ✨ 真正接管 WebView fullscreen（onShowCustomView 不再 no-op，避免 WebView 报"不支持全屏"）
- 🔧 applyImmersive 抽成外部方法（WebChromeClient 可访问）

### 关联 commit
- `83bdb43` v36 fix: applyImmersive 抽成外部方法（WebChromeClient 调用）
- `5c69cce` v36: 真正接管 WebView fullscreen

---

## v35 (2026-08-21) — 修 v33 预缓存命名冲突

### 变更
- 🐛 修复 v33 把 `CACHE_DIR` 改 `/data/cache/videos` 后，stream_manager 写 `{vid}.mp4` 与 v28 时代 raw_path 同名冲突
- 🔧 `_clean_v28_raw_path()` 启动清理无 meta 伴生的 `{vid}.mp4`
- 🔧 `/api/play` 命中检查加 `cached_meta.exists()`（只有 stream_manager 写 meta）
- 🔧 `stream_manager.start` 缓存命中检查同样加 meta 标记

---

## v34 (2026-08-21) — 定时扫描隐藏 WebView 内部 toast

### 变更
- ✨ 定时扫描隐藏 WebView 内部"当前环境不支持全屏"toast
- 🔧 autoplay 保持 true 体验好；JS 主动 hide 不让它视觉出现

### 关联 commit
- `8f546d8` v34: 定时扫描隐藏 WebView 内部"当前环境不支持全屏"toast

---

## v33 (2026-08-21) — 时间戳清理 + 预缓存

### 变更
- ✨ 替代 v29 LRU：按视频处理时间戳清理
- ✨ `hongguo_core.write_meta` 自动注入 `processed_at = time.time()`
- ✨ `_do_cache_clean_by_age()` 每 30 分钟按 `processed_at` 删超 1 小时
- ✨ `_do_cache_clean_backup()` 每 24 小时按 mtime 删超 24 小时兜底
- ✨ `_resolve_cache_dir()` 默认值改 `/data/cache/videos`
- ✨ `/api/play` 加预缓存命中检查
- ✨ `/api/cache/range` 后台异步下完 + 净化 + 存盘
- ✨ 客户端自动预缓存后 3 集（timeupdate 60% 时触发）

---

## v29 (2026-08-21) — LRU 缓存清理

### 变更
- ✨ `_cache_cleaner_loop` 后台线程，每 1800s 扫 `/data/cache/videos/*.mp4`
- 🔧 用户偏好上限 2 GB（`CACHE_MAX_BYTES=2048 MB`）
- 🔧 `CACHE_KEEP_RATIO=0.7`（清到 1.4 GB）
- 🔧 启动线程的入口是 `server.run_server(port, open_browser, host)`
- 🔧 `run_server` 加 `host` 参数，默认 `127.0.0.1`，NAS 调用必须传 `"0.0.0.0"`

---

## v28 (2026-08-21) — CENC 解密真因

### 变更
- 🐛 修复 **CENC 解密真因**：`hongguo_core.resolve_video_url()` 从 spade_a 派生 `content_key`（32 hex），但 `server.py /api/play` 默认分支没把 content_key 通过 ffmpeg `-decryption_key` 传过去
- ✅ server.py 加 `-decryption_key $content_key` + 不强制转 H.264（容器 CPU 软转爆表）
- 🔧 改用 WebView `<video>` 元素（Chrome 内核自带 HEVC 解码，不依赖系统 MediaCodec）

---

## v24 (2026-08-21) — HEVC 净化 (mp4_sanitize.py)

### 变更
- ✨ `mp4_sanitize.py` 纯 Python MP4 box 重写
- ✨ 剔除 saio/saiz/senc/cslg 等字节系非标 box（CDN 防盗链伪加密）
- ✨ 重写 stco/co64 偏移
- ✨ 输出 faststart（moov 前置）
- 🐛 修复 **moov header bug**：第一次写漏了 8 字节 moov 头，导致顶层只有 trak/mvhd/udta 无 moov
- ✅ 顶层 boxes = [ftyp, free?, moov, mdat]
- ✅ 字符串 `saio/saiz/senc/cslg` 全文件计数 == 0

---

## v22 (2026-08-20) — 安卓 + NAS 架构分离

### 变更
- 🏗️ **架构重构**：原方案安卓 APK 内嵌 Chaquopy + Python 后端，APK 80MB+
- ✅ 新架构：NAS Docker 容器跑 Flask（python:3.11-slim，端口 8000）+ 安卓 App 只做壳（WebView + ExoPlayer media3 1.4.1 + JS bridge `hgBridge.playExo`）
- ✅ 数据流：安卓 `MainActivity.java` → `loadUrl(http://<NAS_IP>:8000/)` → NAS Flask → CDN 下载 → `mp4_sanitize.py` 净化 → ffmpeg faststart → Flask send_file（Range 支持）→ 安卓 ExoPlayer 直连 NAS 同源 HTTP

---

## v1 (2026-07-XX) — 初始版本

### 变更
- ✨ 初版：单 exe 内嵌 Python 后端 + pywebview 窗口
- ✨ 签名 API（流沙）+ CDN 视频下载
- ✨ MP4 净化器

---

## v50 (2026-08-22) — 智能去重预缓存 + PC v50 升级 + 上手文档

### 变更
- ✨ **NAS HTML 智能去重预缓存**：`triggerPrefetchNext3` 改成 async，先查 `/api/cache` 已有 sid+ep_no，跳过已缓存的，只请求未下过的（连续区间合并为单次 range 请求）
- 🐛 **fromEp 边界修正**：`currentEp + 1` → `currentEp + 2`（之前会把当前集也缓存下）
- ✨ **用户场景验证**：ep1→缓存 2,3,4；ep2→3,4 已有，只下 5；ep3→4,5 已有，只下 6 ✓
- 🐛 **NAS HTML triggerPrefetchNext3 ReferenceError**（v47 遗留 bug）：函数原本定义在 `bindPlayer` IIFE 内，顶层 `playEpisode` 调用时找不到。把函数提到顶层 + 所有调用 try/catch 兜底
- ✨ **PC HTML v50 升级**：替换 v47 的 `triggerPrefetchNext3`（IIFE 内）→ `triggerPrefetchNext2`（顶层，预取 2 集）
- ✨ **PC 用 `<link rel="prefetch">`**：把 CDN URL 预热到浏览器磁盘缓存，**不入 PC 硬盘**（和安卓 stream_manager 写硬盘策略不同）
- ✨ **PC btn-auto 默认 active**（安卓默认关）
- ✨ **GitHub Actions 自动打 PC**（`.github/workflows/build-pc.yml`，windows-latest + PyInstaller + electron-builder）
- ✨ **`pc/发布版/更新后端.bat`**：跳过 NSIS 重打包，直接覆盖安装目录的 `红果后端.exe`
- ✨ **`nas-backend/deploy.sh`**：SSH 上一键 git pull + docker build + up
- 📚 **`docs/AGENT_CONTEXT.md`**：AI Agent 上手指南，含 10 大踩坑清单（必读！）
- 📚 **`README.md` 更新**：版本 v50 + 加入 AGENT_CONTEXT.md 链接 + 修正仓库名 `hongguo-monorepo`

### 已知问题
- 23:00 后 ISP 晚高峰 git push 经常失败（TLS handshake），需要重试
- 本地 npm install 受沙箱 EBUSY 限制会卡死，已建议用 Actions 自动打

### 关联 commit
- `688be74` NAS HTML triggerPrefetchNext3 移到顶层
- `5f05aa7` 一键部署脚本 + DEPLOY.md 修正
- `b2ae6f8` GitHub Actions PC 构建 workflow
- `102501e` v50 智能去重 + PC HTML v50 升级
- `af1f588` PC 一键更新后端 bat
- `1cd2fdc` 恢复 npm install 误删的 package-lock.json

---

*日志结束。*