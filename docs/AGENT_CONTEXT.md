# AI Agent 上手指南（Agent Context）

> **目的**：让新接手的 AI Agent 在 5 分钟内理解整个项目、知道东西在哪、知道什么坑绝对不能再踩。
>
> 这是 monorepo 的「新人手册」，读完这个 + `README.md` + `ARCHITECTURE.md` 就能开干。
>
> **🎯 带着优化任务来的？直接读 [`OPTIMIZATION_PLAN.md`](OPTIMIZATION_PLAN.md)** —— v51 已完成项 + 待办任务清单（T1-T9，含锚点/方案/坑/验收标准）和工作流铁律。

---

## 1. 项目一句话

**红果漫剧** = 把红果视频平台（短剧/漫剧）的 Web 前端封装成 3 个客户端：
- **PC 桌面版**（Electron + 本地 Python 后端）— 独立运行
- **Android 平板版**（WebView 壳）— 连 NAS 后端
- **NAS 后端**（Docker Flask）— 安卓/PC 共享（PC 版可选连 NAS 也可选本地后端）

外部用户只看得到一个 WebView/浏览器壳，但里面所有的视频解密、签名、净化都是 NAS 后端在做。

---

## 2. 目录速查

```
hongguo-monorepo/
├── README.md                  ← 用户视角的入口
├── docs/
│   ├── ARCHITECTURE.md        ← 架构图、数据流、技术栈
│   ├── CHANGELOG.md           ← v1 → v50 完整变更日志
│   ├── DEPLOY.md              ← NAS 部署 + 升级 + 故障排查
│   └── AGENT_CONTEXT.md       ← 本文件（新 Agent 必读）
├── nas-backend/               ← NAS 服务端（Flask + PyInstaller 复用）
│   ├── server.py              ← Flask 主入口（8000 端口，0.0.0.0）
│   ├── app_nas.py             ← Docker 容器入口（设 HONGGUO_DATA_DIR=/data）
│   ├── hongguo_core.py        ← 视频签名 + 搜索 + 详情 + 解析（核心库）
│   ├── mp4_sanitize.py        ← MP4 box 净化器（剥 saio/saiz/senc/cslg）
│   ├── liushen/               ← 流沙签名 SDK（不要改）
│   ├── static/index.html      ← 安卓 + NAS 共用的前端（80KB+）
│   ├── Dockerfile             ← python:3.11-slim + ffmpeg
│   ├── docker-compose.yml     ← 端口 8000，挂载 ./data:/data
│   └── deploy.sh              ← 一键部署脚本
├── pc/                        ← PC 版（Electron + 内嵌 Python 后端）
│   ├── main.js                ← Electron 主进程（开 BrowserWindow + 中文菜单）
│   ├── preload.js             ← IPC bridge
│   ├── package.json           ← electron-builder NSIS 配置
│   ├── backend/               ← 内嵌的 Python 后端（PyInstaller 打成 exe）
│   │   ├── backend.spec       ← PyInstaller 配置
│   │   ├── main_server.py     ← Flask 入口
│   │   ├── server.py          ← 服务层（与 nas-backend 同一份历史）
│   │   ├── hongguo_core.py    ← 核心（同上）
│   │   ├── static/index.html  ← PC 版前端（和 NAS HTML 有差异！）
│   │   └── 红果后端.exe       ← 打包好的后端（gitignore）
│   └── 发布版/
│       ├── 红果漫剧_安装版_Setup.exe  ← NSIS 安装包（gitignore）
│       ├── 红果漫剧Electron版/        ← 解包目录（调试用，gitignore）
│       └── 更新后端.bat               ← 跳过 NSIS 重打包的覆盖脚本
├── android/                   ← Android APK（WebView 壳）
│   ├── android/app/src/main/
│   │   ├── java/com/hongguo/manju/MainActivity.java  ← WebView 入口
│   │   ├── assets/index.html  ← 离线 fallback（实际不加载，WebView 连 NAS）
│   │   └── res/...
│   └── 发布版/红果漫剧_安卓版.apk  ← gitignore
├── .github/workflows/
│   ├── build-android.yml      ← ubuntu-latest 自动打 APK
│   └── build-pc.yml           ← windows-latest 自动打 NSIS（v50+ 新增）
└── .gitignore                 ← 排除 node_modules / bin / dist_backend / 发布产物
```

---

## 3. 关键技术栈

| 层 | 技术 | 版本 |
|---|---|---|
| 后端 | Python | 3.11 (Docker slim) |
| 后端框架 | Flask | 3.0 |
| 后端加密 | pycryptodome / gmssl / snowland_smx | (liushen 自带) |
| 后端打包 | PyInstaller | 6.10 |
| 前端 | 原生 HTML + JS + CSS | 无框架（80KB 单文件） |
| PC 壳 | Electron | 33.x |
| PC 打包 | electron-builder | 26.x（NSIS 安装器） |
| 安卓壳 | Android WebView + Java | min SDK 24 |
| 容器 | Docker Compose | python:3.11-slim + ffmpeg |

**流沙签名**（liushen/）是接入红果视频的关键，**绝对不要动**，改坏了整个签名链路就废了。

---

## 4. 用户环境

| 设备 | IP | 角色 |
|---|---|---|
| NAS（飞牛 OS fnOS） | <NAS_IP>:8000 | Docker 后端 + 缓存存储 |
| 平板 | <LAN_IP> | 安卓 APK |
| 电脑本体 | <LAN_IP> | 开发 + PC 版安装位置 |
| GitHub | dawei233/hongguo-monorepo | 私有仓库 |

- **SSH NAS**：`admin@<NAS_IP> -p <SSH_PORT>`，sudo 密码 = admin 密码
- **远程 sudo 正确姿势**：`sudo -S -p '' bash -c '整条命令'`（`-S` 从 stdin 读密码、`-p ''` 关提示、`bash -c` 包裹避免 `&&` 只作用于第一条）

---

## 5. ⚠️ 绝对不能踩的坑（重点！）

### 坑 1：HTML 改了 ≠ 立即生效

**NAS 后端**用 Dockerfile `COPY . .` 把 `static/index.html` **烧进镜像**，git pull **不会**让运行中的容器加载新 HTML。

**正确流程**：
```bash
ssh admin@<NAS_IP> -p <SSH_PORT>
cd /vol5/1000/Docker/hongguo
sudo git pull                    # 1. 拉新代码
cd nas-backend
sudo docker compose build --no-cache   # 2. 重新构建镜像（关键：--no-cache）
sudo docker compose up -d              # 3. 重启容器
```

或者直接：`bash nas-backend/deploy.sh`

**PC 版**：`pc/backend/static/index.html` 用 PyInstaller 打进 `红果后端.exe`，必须重打后端 exe + electron-builder 重打 NSIS。或者用 `pc/发布版/更新后端.bat` 直接覆盖安装目录里的 exe（推荐，省得重打 NSIS）。

### 坑 2：函数作用域（v47 血泪教训）

`bindPlayer` IIFE 内部定义的函数（比如 `triggerPrefetchNext3`），从 **顶层函数**（比如 `playEpisode`）调用会抛 `ReferenceError: function is not defined`。

**判断方法**：看函数是在 IIFE `(function bindPlayer() { ... })()` 内还是外。顶层 `function playEpisode()` 调用的所有函数必须在**顶层**定义。

**复盘**：v47 把 `triggerPrefetchNext3` 加在 `bindPlayer` IIFE 里，PC 用户和安卓用户都遇到过「开启连播后切下一集报错」的问题。修复就是把函数提到顶层 + 所有调用 try/catch。

### 坑 3：MP4 净化后必须清缓存

`/api/play` 里 `if not h264_path.exists() or st_size<1024` 才重新净化。**改了 `mp4_sanitize.py` 或 ffmpeg 命令后必须**：

```bash
sudo docker exec hongguo bash -c 'rm -f /data/cache/videos/*.mp4 /data/cache/videos/*.mp4.meta.json /data/cache/videos/*.raw.mp4'
```

不删旧缓存，新代码不生效。

### 坑 4：CENC 解密密钥（v28 教训）

`hongguo_core.resolve_video_url()` 从 spade_a 派生 `content_key`（32 hex 字符）。`server.py /api/play` 调 ffmpeg 时**必须传 `-decryption_key $content_key`**，否则 mdat 是密文播放失败。

历史：v22-v27 一直在修 MP4 box，但忘了传密钥，都是徒劳。

### 坑 5：PyInstaller workpath 路径冲突

本地打包时 `python -m PyInstaller backend.spec --workpath _pytmp_$$`（**全新 workpath**），避免沙箱清理拦截。

输出路径默认在 spec 所在目录的 `dist/`（不是 `dist_backend/`），需要 cp 到 `dist_backend/红果后端.exe`。

### 坑 6：electron-builder NSIS 输出路径

默认输出 `release/`，但 `release\win-unpacked.tmp` 这种空目录的 `fs.rm` 会被沙箱 trash 机制拒绝（PowerShell `SendToRecycleBin` 抛 FileNotFound）。

**解决**：`package.json` 里 `directories.output` 改成 `%TEMP%\hongguo-build\release`（属 OS_TMP_DIRS，`shouldUseNativeDelete=true` 放行）。打包完后 cp 回 `pc/发布版/`。

### 坑 7：晚间 23:00 后 GitHub 不稳

多次 `git push` 失败：`schannel: failed to receive handshake`。是 ISP 晚高峰问题，不是配置问题。

**应对**：
- 改完代码后立即 commit（不阻塞），push 多次重试
- 如果完全不通，告诉用户晚点自己 push

### 坑 8：npm install 卡死（沙箱 EBUSY）

`node_modules/electron-builder/node_modules/fs-extra/LICENSE` 这种文件会被 Windows 文件锁占住，npm 反复 retry 后失败。**不要浪费时间重试**，直接用 `pc/发布版/更新后端.bat` 跳过重打包流程。

或者直接 push 代码用 **GitHub Actions 自动打**（`.github/workflows/build-pc.yml` 已配）。

### 坑 9：安卓 APK 内 assets/index.html 是冗余的

`MainActivity.loadServerPage(url)` 直接 `webView.loadUrl(url)` 加载 NAS URL，**不读 assets**。APK 里那份 index.html 只是个 fallback（NAS 连不上时用）。

所以**只改 nas-backend/static/index.html 就够了**，不用同步到 `android/.../assets/`。

### 坑 10：PC HTML ≠ NAS HTML（v50 起）

两个 `static/index.html` 是不同的文件，要保持**同一份 v50 baseline 但 PC 版有 3 处特殊**：
1. `btn-auto` 默认 `class="active"`（PC 默认连播开，安卓默认关）
2. 用 `triggerPrefetchNext2`（不是 `Next3`）— PC 只预热 2 集
3. prefetch 用 `<link rel="prefetch">`（浏览器缓存）— 不入 PC 硬盘

不要无脑 cp NAS HTML 到 PC HTML。

---

## 6. 常用命令速查

### 改前端后的部署

| 改了什么 | 做什么 |
|---|---|
| `nas-backend/static/index.html` | NAS 上跑 `deploy.sh` |
| `pc/backend/static/index.html` | `python -m PyInstaller backend.spec --workpath _pytmp_$$ --distpath dist_backend --clean`，再跑 `更新后端.bat` 或重打 NSIS |
| `android/.../assets/index.html` | 通常不用改（WebView 不读）；若改了就 git push，等 Actions 自动打 APK |
| `server.py` / `mp4_sanitize.py` / `hongguo_core.py` | NAS 上跑 `deploy.sh` + 清缓存 |
| `main.js` / `package.json` | PC 本地 `npm run dist:nsis` 或 Actions 自动打 |

### 测试 / 调试

```bash
# 看 NAS 容器日志
ssh admin@<NAS_IP> -p <SSH_PORT>
sudo docker logs hongguo --tail 50 -f

# 测试 API
curl http://<NAS_IP>:8000/api/search?keyword=短剧
curl http://<NAS_IP>:8000/api/cache  # 看持久缓存列表

# 本地 PC 后端调试（不打包直接跑）
cd pc/backend
python main_server.py   # 监听 5137 端口
# 然后 cd .. && npm start  # Electron 启动会自动连 5137
```

### Git 工作流

```bash
# 工作流程
cd /f/software/WorkBuddy/2026-08-18-17-02-22/hongguo-monorepo
git status
git diff
git add -A
git commit -m "fix(v50+): <改动说明>"
git push origin main      # 晚间可能失败，多重试
```

commit prefix：`feat`/`fix`/`chore`/`docs`/`ci`/`refactor`/`perf`

---

## 7. 用户特征

- **高效直接**，不要绕弯子，先给结论再列选项
- **中文交流**，技术词可中英混
- **修 bug 时**：先看错误日志 → 自己定位 → 自己动手改，不要让用户复述
- **测 APK 用截图**反馈，期待 AI 从错误日志中自主定位
- **习惯**：授予项目目录文件修改权限，期望 AI 直接动手
- **GitHub**：`gh` CLI 已登录，操作 `dawei233/*` 仓库无需额外认证
- **常见需求**：
  - 修视频播放 bug（连播、缓存、解密、画质）
  - 加新功能（缓存策略、UI、过滤）
  - 改打包流程（PyInstaller、electron-builder、NSIS）
  - NAS 容器重建 + 缓存清理
  - 安卓 APK 重新打包 + 测试

---

## 8. 接手后第一步该看什么

按顺序读完：

1. `README.md`（2 分钟，用户视角）
2. `docs/ARCHITECTURE.md`（10 分钟，模块关系）
3. `docs/CHANGELOG.md`（5 分钟扫一遍最近的 v45 → v50 变更）
4. 本文件第 5 节「绝对不能踩的坑」（10 分钟，重中之重）

然后：

5. `git log --oneline -20` 看最近 commit
6. 跑一遍 `git pull` 确保本地最新
7. SSH 到 NAS 跑 `sudo docker ps | grep hongguo` 看容器在不在

---

## 9. 紧急情况

- **NAS 服务挂了**：SSH 上去 `sudo docker ps` → `sudo docker logs hongguo --tail 100` → 重启容器
- **缓存清理不生效**：检查 `/data/cache/videos/` 是否有权限（飞牛 OS 默认 admin 有）
- **GitHub Actions 失败**：看 Actions 页面报错，通常是 PyInstaller 缺模块或 npm 网络问题
- **平板打不开 App**：先确认 NAS 通（`curl http://<NAS_IP>:8000/api/cache`），不通就是 NAS 问题
- **PC 安装版报错**：先看 `红果后端.exe` 是否替换，再看 `app.asar` 是不是旧的

---

## 10. 备注

- **历史仓库**：`dawei233/hongguo-manju`（已 archived，老仓库）— 不要再去那改东西
- **老本地目录**：`hongguo-electron/`、`hongguo-nas-backend/`、`hongguo-android/` 都在 2026-08-22 清理掉了，不要重建
- **副项目目录**：`hongguo-pc/`（保留 venv 打包环境）+ `hongguo-monorepo/`（主仓）共存
- **备份产物**：`pc/发布版/红果漫剧_安装版_Setup.exe` 是 v50 之前的最后可用版本
- **内存中的项目记忆**：`.workbuddy/memory/2026-08-22.md` 完整记录了重构、bug 修复、踩过的所有坑