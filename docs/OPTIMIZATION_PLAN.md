# 优化任务清单（交接文档）

> **受众**：接手后续优化的 AI Agent / 开发者。
> **用法**：按"批次 A → B → C → D → E"顺序执行；每个任务自带现状锚点、方案步骤、坑位提示和验收标准，可独立完成并提交。


> **📋 进度（v52 截止 2026-09-03）**
>
> | 任务 | 状态 | 版本 |
> |------|------|------|
> | T1 播放链路流式化 | ⏸️ 用户跳过（动主链路风险） | — |
> | T2 mp4_sanitize 流式重写 | ⏳ 未做（byte-identical 验证成本高） | — |
> | T3 waitress | ✅ | v52 |
> | T4 全局 Session | ✅ | v52 |
> | T5 browse 上限 | ✅ | v52 |
> | T6 详情页 JSON 解析 | ⏳ 未做（需真网验证） | — |
> | T7 resolve_with_fallback | ✅ | v52 |
> | T8 冒烟测试 + CI | ✅ | v52 |
> | T9 CDN 白名单 | ✅ | v52 |
> | 智谱 AI 搜剧 | ✅ | v52 |
>
> v52 实际完成 7/9（仅 T1/T2/T6 暂缓）。详见 CHANGELOG.md v52 章节。


> **基线**：本文档基于 v51 提交 `3e73259` 编写，行号为该版本的参考位置（会漂移），**定位以函数名/注释标记为准**。
> **上游文档**：项目背景先读 [`AGENT_CONTEXT.md`](AGENT_CONTEXT.md) 和 [`ARCHITECTURE.md`](ARCHITECTURE.md)；v51 之前的变更看 [`CHANGELOG.md`](CHANGELOG.md)。

---

## 一、基线状态：v51 已完成的内容（勿重复做）

v51（2026-09-02，commit `3e73259`）已完成以下修复与优化，**接手前先确认这些行为已存在**：

| 类别 | 内容 |
|------|------|
| 修复 | `/api/play` 缓存路径统一 `hongguo_core.CACHE_DIR()`（原硬编码 `/data` 导致 PC 版 404） |
| 修复 | mp4 净化从 `python /app/...` 子进程改为**进程内** `import mp4_sanitize`（`server._sanitize_mp4_file()` + `stream_manager._worker`） |
| 修复 | 缓存清理统一为 **24 小时**单策略（原 7 天主清理被 24h 保底清理废掉）；清理线程动态用 `CACHE_DIR()` |
| 修复 | `.raw.mp4` 删除条件写反、`/api/cache` POST 不删 meta、前端切 tab 清搜索框（`$('kw')`）、封面 URL `escapeHtml` |
| 优化 | `/api/play` 删除多余的二次 faststart remux；`build_series_index` 6 链并发 + 落盘 `cache/series_index.json`（6h TTL）；解析重试 8×3s → 3×2s |
| 结构 | 双份 `hongguo_core.py` 合并（两端 byte-identical）；`mp4_sanitize.py` 补齐到 `pc/backend/`；新增 `scripts/sync_core.py`；`backend.spec` 加 `mp4_sanitize` hiddenimport |
| 安全 | `.gitignore` 行尾注释 bug 修复；`config.json`/`prefs.json` 已移出 git 跟踪（文件保留在磁盘） |

---

## 二、工作流铁律（每个任务都要遵守）

1. **共用文件三件套**（`nas-backend/hongguo_core.py`、`nas-backend/mp4_sanitize.py`、`nas-backend/static/index.html`）：以 `nas-backend` 为基线改，改完必须跑 `python scripts/sync_core.py` 同步到 `pc/backend`；提交前 `python scripts/sync_core.py --check` 必须通过。
2. **`server.py` 有两份且有意不同**：PC 版（`pc/backend/server.py`）比 NAS 版多 NAS prefs 同步逻辑（`_nas_*` 函数、`api_prefs`）。除该差异段外两端应保持一致——**改一端必须镜像改另一端**。当前两端除 NAS 同步段外完全相同，可用 `diff <(grep -v "_nas_\|NAS_URL" nas-backend/server.py) <(grep -v "_nas_\|NAS_URL" pc/backend/server.py)` 核对。
3. **提交信息**：`vNN: 类型: 描述`（下一个版本号 **v52**，用完递增）；`docs/CHANGELOG.md` 追加新章节（绝不修改旧章节）。
4. **验证底线**：`python -m py_compile` 所有改动的 .py；用 Flask `test_client` 跑冒烟测试（见任务 T8，做完后直接 `pytest`）；推送后两条 GitHub Actions（安卓 APK + PC 安装版）必须绿。
5. **不要碰的雷区**：
   - 设备注册 `build_device_payload()` 的 POST 不允许加自动重试（签名请求重放会被拒绝）
   - `/api/play` 缓存命中判断依赖 `{vid}.mp4 + {vid}.mp4.meta.json` 伴生关系，别破坏
   - NAS 上 `entrypoint.sh` 的 DoH hosts 注入是绕 DNS 劫持的关键，别删
   - `pc/backend/config.json` / `prefs.json` 含设备身份/用户数据，永不入库

---

## 三、待办任务

### 批次 A（零行为风险，建议一次提交完成）

#### T3. Flask dev server → waitress 生产级 WSGI

- **现状**：三端全跑 `app.run(threaded=True)`（Werkzeug 开发服务器）。`nas-backend/server.py:924`（`run_server()`）、`pc/backend/main_server.py:92`。项目曾为 dev server 的 206+chunked 缺陷写过 workaround（`_stream_partial` 注释）。
- **方案**：
  1. `nas-backend/requirements.txt` 和 `pc/backend/requirements.txt` 加 `waitress==3.0.2`
  2. `server.run_server()`：用 `waitress.serve(app, host=host, port=port, threads=8)` 替换 `app.run(...)`；包 try/except ImportError 兜底回退 `app.run`（NAS Docker 里装了 waitress 必走 waitress）
  3. `pc/backend/main_server.py` 末尾同样替换
  4. `pc/backend/backend.spec` 的 `hiddenimports` 加 `"waitress"`
- **坑**：替换后必须实测 `/stream/<fname>` 边下边播（任务进行中 2% 起播）在 waitress 下行为正常；`_stream_partial` 返回的是 200 + chunked，理论上没问题，但要过一遍。
- **验收**：本地起服务，`/api/ping` 200；播放一集到 `/stream` 路径可拖动；两条 CI 绿。

#### T4. 裸 requests → 全局 Session + 自动重试

- **现状**：`hongguo_core.py` 约 10 处、`server.py` 约 4 处裸 `requests.get/post`，每次新建 TCP+TLS，无连接复用无重试。
- **方案**：
  1. `hongguo_core.py` 模块级新增：
     ```python
     from requests.adapters import HTTPAdapter
     from urllib3.util.retry import Retry
     SESSION = requests.Session()
     SESSION.mount("https://", HTTPAdapter(max_retries=Retry(
         total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503],
         allowed_methods=["GET"])))
     SESSION.mount("http://", HTTPAdapter(max_retries=同上))
     ```
  2. 全项目 `requests.get` → `SESSION.get`、`requests.post` → `SESSION.post`，**白名单例外**：`build_device_payload()` 里的设备注册 POST（`log.snssdk.com`）保持原样——签名请求自动重放会被拒。
  3. Session 是线程安全的（urllib3 连接池），Flask threaded 模式下可直接用。
- **验收**：`grep -n "requests\.\(get\|post\)" nas-backend/*.py` 仅剩设备注册一处；冒烟测试通过。

#### T9. `/api/cdn` 代理加域名白名单

- **现状**：`server.py:578`（`api_cdn_proxy`）对 `u=` 参数不设限——局域网任何设备可把 NAS 当任意 HTTP 代理用。
- **方案**：模块级定义白名单后缀元组，起步用 `("hongguoduanju.com", "fqnovel.com", "zjcdn.com", "douyinpic.com", "bytecdn.cn", "reading-videocdn")`（后两个是 CDN host 片段，用 `in` 匹配 netloc）；不匹配返回 403 + JSON error。实际播放时观察后端日志 `[play] ... 源: <url>`，发现新 CDN 域名就补进去。
- **验收**：`/api/cdn?u=https://www.baidu.com` 返回 403；正常播放链路不受影响。

#### T5. browse 后台抓取器设上限

- **现状**：`hongguo_core.py:953`（`_browse_background_worker`）对 sitemap 里 47 万+ ID 永不停歇地抓详情页（4 并发），持续压官方站点、白耗 NAS 带宽。sitemap ID 全量缓存在 `cache/sitemap_ids.json`（首次运行已抓到 474168 个）。
- **方案**：worker 循环内加常量 `BROWSE_PREFETCH_LIMIT = 2000`，当 `_SITEMAP_CURSOR >= min(BROWSE_PREFETCH_LIMIT, len(_SITEMAP_IDS))` 时 `time.sleep(3600)` 长眠（保持进程内不退出，便于将来调参）；用户滚动触发的 `browse_ensure()` 按需抓取逻辑**保持不变**（它没有上限，靠 `max_wait` 限流）。
- **验收**：启动后日志显示抓取到 2000 条后停止新增；前端滚动到 browse 区仍能继续加载。

---

### 批次 B（测试护栏，先于 C/D 做）

#### T8. 固化冒烟测试 + CI job

- **现状**：v51 交接近似手写的冒烟测试（未入库）。项目零测试。
- **方案**：
  1. 新建 `tests/test_smoke.py`（pytest），移植以下用例（跑之前 `os.environ["HONGGUO_DATA_DIR"] = tempfile.mkdtemp()`，`sys.path.insert(0, nas-backend 目录)`，**绝不触碰仓库内 cache/**）：
     - 路由可用性：`/api/ping`、`/api/settings` GET/PUT、`/api/cache` GET/POST、`/api/manual_series`、`/api/prefs`
     - 清理器 24h 策略：造 4 个假文件（超 24h 带 meta、新鲜带 meta、超 24h 无 meta raw、孤儿 meta），调 `server._do_cache_clean()` 断言删留
     - 索引落盘：monkeypatch `hongguo_core.fetch_category_page` 返回假数据，`build_series_index(force=True)` 后断言 `cache/series_index.json` 存在；清 `_INDEX_CACHE` 再调断言 0 网络请求、内容一致
     - `_sanitize_mp4_file` 对非 MP4 输入安全返回 False
  2. **注意**：不要测试真实 `/api/search`——它会触发 `build_anime_index` → `search_suggestion` 打真网。如要测搜索，连 `search_suggestion` 一起 monkeypatch。
  3. 新建 `.github/workflows/test.yml`：ubuntu-latest + Python 3.11，`pip install -r nas-backend/requirements.txt -r nas-backend/liushen/requirements.txt pytest`，跑 `pytest tests/ -v` 和 `python scripts/sync_core.py --check`。两件事都要过。
- **验收**：本地 pytest 绿；CI 新 job 绿；故意改乱 `pc/backend/hongguo_core.py` 时 `--check` 能 fail。

---

### 批次 C（结构性，中风险）

#### T2. mp4_sanitize 流式重写（内存 O(moov)）

- **现状**：`nas-backend/mp4_sanitize.py:213`（`sanitize_mp4`）把整个文件 `bytearray(f.read())` 读进内存。v51 改为进程内调用后，几百 MB 视频 = Flask 主进程几百 MB 内存尖峰。解析逻辑（`clean_stbl`/`clean_container`，操作 moov 字节）本身没问题。
- **方案**：只重写 I/O 层，解析层不动：
  1. 用文件句柄 + seek 逐个读顶层 box 头（8/16 字节），不再整体读入
  2. 只把 moov box 读进内存（几 MB），ftyp/其他顶层 box/mdat 全部用 1MB chunk 从输入流拷贝到输出
  3. 输出顺序保持现状：ftyp → 其他顶层 box → 新 moov（带 delta 重写后的 stco/co64）→ mdat
  4. **保持 `sanitize_mp4(input_path, output_path) -> bool` 签名与 CLI `python mp4_sanitize.py in out` 不变**（server/hongguo_core 以函数方式调用，Docker/文档以 CLI 方式引用）
- **坑**：delta 计算依赖"新 moov 大小预估→重算→可能重做一遍"的现有逻辑（`sanitize_mp4` 步骤 1-3），流式化后这段逻辑照旧（moov 在内存里可以重算两遍）。
- **验收**：用 ffmpeg 生成 ~500MB 测试文件（`ffmpeg -f lavfi -i testsrc=duration=3600 -c:v libx264 out.mp4`），进程内 sanitize 峰值内存 < 100MB；输出与旧实现（git 历史里 v51 版本）对同一输入 byte-identical；T8 测试过。

#### T7. 抽取 `resolve_with_fallback()` 纯函数

- **现状**：`api_play`（`nas-backend/server.py:308`）里"三段找源"逻辑（want_hq 重试 3×2s → player 页 scraping 720p → App 接口逐档位兜底）约 60 行和缓存命中/下载/异常回退搅在一起；这段代码在两份 server.py 中完全重复。
- **方案**：
  1. 在 `hongguo_core.py` 新增：
     ```python
     def resolve_with_fallback(vid: str, sid: str = "", quality: str = "",
                               no_hevc: bool = False) -> Dict[str, Any]:
         """按优先级解析可播放 URL：App 签名 HQ → player 页 H.264 → App 逐档位兜底。
         返回 {"url": str, "source": str, "info": resolve_video_url 的返回或近似结构}
         全部失败 raise RuntimeError(用户可读的错误信息)。"""
     ```
     把现有三段逻辑（含 `hgweb/reading-videocdn/v11-/v26-/v3-hgweb` 域名白名单校验、3×2s 重试、`no_hevc` 分支）原样搬入
  2. `api_play` 的 `mode != "download"` 分支改为调用它，`used_source`/`info` 从返回值取
  3. 两份 server.py 同步替换后，这段代码在两端彻底消重
- **坑**：player 页 scraping 那段用 `_requests.get` 抓 `hongguoduanju.com/player/{sid}/{vid}`，搬入 hongguo_core 后用 SESSION（T4 已做）+ `WEB_UA`；`err_msg` 的 no_hevc 文案原样保留。
- **验收**：`api_play` 分支显著变短；真机播一集 HQ 源、一集只走 player 页兜底的源（可用不存在 HQ 的老剧试）；两端 sync 校验过。

---

### 批次 D（最大重构，收益最大，单独一个版本做）

#### T1. 播放链路流式化：分片 MP4 起播 + 统一 stream_manager

- **现状**：`/api/play` 默认模式同步执行"下载整集 → ffmpeg 解密+remux → 进程内净化"全部完成才返回（`nas-backend/server.py:308` 的 `mode != "download"` 分支，前端干等几十秒~几分钟）。且"下载+解密+净化"逻辑存在**两份**：`api_play` 内联一份、`stream_manager._worker`（`hongguo_core.py:1463`）一份，历史上已因双份维护出过 bug（v42 净化被跳过）。根因：`+faststart` 文件没写完没有 moov，不能边下边播，只能同步等。
- **目标**：点开一集 **5 秒内起播**；消除双份实现。
- **方案**：
  1. `stream_manager` 增加 streamable 输出模式：worker 的 ffmpeg 参数改为 `-movflags frag_keyframe+empty_moov+default_base_moof`（分片 MP4，写一点就能播）。注意产物**不能**直接当持久缓存终态（缩略图/文件管理器兼容差，v42 注释有记录）。
  2. 下载完成后：若 `persist=True`，后台把分片产物 remux 成 faststart 终态（`ffmpeg -i {vid}.mp4 -c copy -movflags +faststart {vid}.fs.mp4` 成功后原子替换），再写 meta.json。净化（T2 的进程内版本）在 remux 前对分片产物执行。
  3. `/api/play` 重写：删除内联下载+净化整段代码，流程收敛为——缓存命中检查（保持现有 `{vid}.mp4 + meta` 判断）→ `stream_manager.start(vid, quality, persist=False, ...)` → **立即**返回 `{"direct": False, "task": {..., "url": "/stream/{fname}"}}`（等价于今天 `mode=download` 的行为）。前端已在轮询 `/api/task` 并渲染下载进度（`pollProgress` + 大号百分比 UI）。
  4. 前端（`static/index.html`）：`playEpisode` 的 `.then` 里，当 `t.status === 'downloading' && t.url` 时**直接 `startPlay(t.url, vid)` 边下边播**（`/stream/_stream_partial` 已支持阻塞式流式转发），不再干等 ready；ready 后 `ensureFullPlay` 已有"已在播放则不打断"的逻辑。
- **坑**（重要）：
  - **Windows 文件占用**：remux 终态时若 `<video>` 还在流式读分片文件，`os.replace` 会 PermissionError。处理：remux 到 `{vid}.fs.mp4` 新文件 → 尝试 `os.replace` → 失败则保留分片文件可播状态、标记任务待重试（下次访问或 60s 后重试一次）。绝对不能播放中途删源文件。
  - 分片 MP4 的 `_stream_partial` 读取路径照旧可用（它只管读文件尾部等待，不解析格式）。
  - `mode=download` 语义保留（显式强制走持久缓存下载）。
  - 画质切换、`no_hevc`、缓存命中秒开路径全部回归测试。
- **验收**：NAS 上点开未缓存剧集 5 秒内出画面；连播预取（`triggerPrefetchNext3`）照常命中缓存秒开；`/api/cache` 列表、清理、删除不受影响；PC + 安卓双端真机过；CI 绿。

---

### 批次 E（增强，可最后做）

#### T6. 详情页解析：内嵌 JSON 优先，正则降级为兜底

- **现状**：`hongguo_core.py:996`（`extract_vid_list`）、`hongguo_core.py:1019`（`extract_series_meta`）靠正则启发式（《》书名号、最后一个 series_cover、series_id 后第一个 preload…），页面结构一变就碎。同为 SSR 页面的 quickapp 解析（`server.py` 的 `api_quickapp_parse`）已示范正确做法：抠 `window._ROUTER_DATA = {...}` JSON 块 `json.loads`。
- **方案**：详情页（`hongguoduanju.com/detail?series_id=X`）先探测内嵌 JSON 状态块（可能是 `_ROUTER_DATA` 或其他全局变量），命中则直接取 `series_data`/`chapter_ids`/`vid_list`；未命中走现有正则路径兜底。**需要真网验证**（本机可达 hongguoduanju.com），把真实页面的 JSON 结构 dump 到测试 fixture。
- **验收**：3 部已知剧（短剧/漫剧/manual quickapp 各一）detail 解析结果与 v51 版本一致；JSON 缺失页面走兜底不炸。

---

## 四、明确不做的事（防止过度工程）

| 诱惑 | 为什么不做 |
|------|-----------|
| 前端上 React/Vue + 构建链 | 摧毁"NAS 改一处、三端生效、APK 不重打包"的核心优势。最多拆 html+js+css 三个文件，仍无构建 |
| Flask → FastAPI 重写 | 当前规模 Flask 够用，重写纯风险。想清爽用 Blueprint 拆文件即可（可选项，不排期） |
| 缓存元数据换 SQLite | 千级文件、顺序读写，sidecar meta.json 透明可手删，SQLite 是过度设计 |
| APK 换 ExoPlayer 原生播放 | 失去"前端零重打包"。除非 HEVC 硬解覆盖出问题，WebView 壳是正确取舍 |
| prefs 增量合并/CRDT | 双端并发写个人场景撞不上，500ms 防抖够用 |
| 共用代码换 git submodule/symlink | Windows symlink 要开发者模式，PyInstaller 对外部包路径麻烦。`sync_core.py` + CI check 是当前规模最优解 |

---

## 五、完成定义（整体 DoD）

- [ ] 批次 A-E 全部完成，每个批次一个（或多个）独立 commit，格式 `vNN: 类型: 描述`
- [ ] `pytest tests/` 全绿；`python scripts/sync_core.py --check` 全绿
- [ ] NAS 真机：未缓存剧集 5 秒起播、缓存命中秒开、24h 清理生效、连续看 5 集无感切换
- [ ] PC 真机：同上 + PyInstaller 打包版验证 waitress/净化/路径
- [ ] 安卓真机：HEVC 硬解正常、连播预取生效
- [ ] 两条 GitHub Actions（PC 安装版 + 安卓 APK）绿
- [ ] `docs/CHANGELOG.md` 按版本追加；本文档中已完成任务打勾或移除
