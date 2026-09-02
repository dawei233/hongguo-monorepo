# PC 版（hongguo-electron）

> Windows 桌面独立应用，基于 Electron + Python 后端 + NSIS 安装版

---

## 目录结构

```
pc/
├── main.js                  # Electron 主进程入口
├── preload.js               # 渲染进程预加载（暴露 IPC API）
├── package.json             # electron-builder 配置（NSIS）
├── package-lock.json
├── backend/                 # Python 后端（Flask + 签名 + 视频下载）
│   ├── main_server.py       # 后端 HTTP 服务入口（端口 5137）
│   ├── hongguo_core.py      # 视频签名 + 搜索 + 详情解析（共用核心库）
│   ├── static/
│   │   └── index.html       # 前端 UI（与 nas-backend/static/index.html 同步）
│   ├── liushen/             # 流沙签名 SDK 依赖
│   ├── bin/                 # ffmpeg 二进制（PyInstaller 捆绑，gitignore）
│   ├── dist_backend/        # PyInstaller 单文件后端 exe（gitignore）
│   ├── hongguo.spec         # 旧 PyInstaller spec（hongguo-pc 老版本，保留）
│   ├── backend.spec         # 当前 PyInstaller spec（打 红果后端.exe）
│   ├── cache/, temp/        # 运行时临时目录（gitignore）
│   └── ffmpeg_err_*.log     # ffmpeg 错误日志（gitignore）
├── fix_modules.py           # 修复 npm 模块问题（PyInstaller hook）
├── fix_modules.sh
├── 发布版/                  # 最终交付物
│   ├── 红果漫剧_安装版_Setup.exe   # NSIS 安装版
│   └── 红果漫剧Electron版/          # win-unpacked 目录版（gitignore）
└── README.md                # 本文件
```

---

## 启动流程

```
用户双击 红果漫剧_安装版_Setup.exe
  ↓
安装向导（NSIS） → 选择安装路径
  ↓
生成桌面快捷方式（"红果漫剧"）
  ↓
用户点击图标启动
  ↓
1. Electron 主进程启动 (main.js)
2. spawn 子进程 红果后端.exe（捆绑 ffmpeg + 签名库 + Flask）
3. 后端监听 127.0.0.1:5137，提供 static/index.html + /api/*
4. Electron 创建 BrowserWindow
5. loadURL('http://127.0.0.1:5137/')
6. 用户看到 UI + 交互
```

---

## 关键技术点

### Electron 全屏方案（v36+）

走 `BrowserWindow.setFullScreen(true)`（不进 HTML5 fullscreen），前端通过 IPC `fs-changed` 事件切 `body.fs-mode`，CSS 让 `.topbar/.ctrl-bar` 默认 `opacity:0; transform:translateY(-100%)`，`attachAutoHideCtrls()` 监听 `mousemove/wheel/keydown/mousedown` 加 `.fs-show` 类（显示），3 秒不动移除。

### Python 后端 PyInstaller 打包

- spec 文件 `backend.spec` 输出单文件 exe `红果后端.exe`（99MB）
- electron-builder `extraResources` 字段读取：`backend/dist_backend/红果后端.exe` → 打包到 `resources/backend/红果后端.exe`
- 运行时 Electron 主进程 spawn 这个 exe

### NSIS 安装器

- `oneOne:false` + `perMachine:false` + `allowToChangeInstallationDirectory:true`（可选安装路径）
- `installerLanguages:["zh_CN"]` + `language:"2052"`（中文安装向导）
- 创建桌面/开始菜单快捷方式
- 卸载时清理所有文件

---

## 打包流程（开发到发布）

```bash
# 1. 确保前端 HTML 与 NAS 版同步
cp ../nas-backend/static/index.html backend/static/index.html

# 2. 重新打 Python 后端 exe
cd backend
../app/venv/Scripts/python.exe -m PyInstaller backend.spec \
  --workpath /tmp/build \
  --distpath dist
cp dist/红果后端.exe ../backend/dist_backend/红果后端.exe

# 3. 打 NSIS 安装版（绕开沙箱，输出到 /tmp）
cd ..
sed -i 's|"output": "release"|"output": "/tmp/hg_electron_build/release"|' package.json
node node_modules/electron-builder/out/cli/cli.js --win nsis
cp /tmp/hg_electron_build/release/红果漫剧_安装版_Setup_1.0.0.exe \
   发布版/红果漫剧_安装版_Setup.exe
sed -i 's|"output": "/tmp/hg_electron_build/release"|"output": "release"|' package.json

# 4. 本地测试
npm start

# 5. commit
cd ..
git add pc/
git commit -m "vXX: PC 版打包"
```

---

## 依赖安装

```bash
# 首次 setup
cd pc
npm install   # 安装 electron + electron-builder + electron-fuses

# Python 后端打包环境（与 hongguo-pc 共享）
# 在 ../hongguo-pc/app/venv/ 已装 PyInstaller
```

---

## 调试

```bash
# 主进程日志
npm start
# 输出在 terminal

# 渲染进程日志
# 在浏览器开发者工具（F12 或 自动打开 devtools）
# → Console 看 JS 错误

# 后端日志
# spawn 时会打印到主进程 terminal
```

---

## 关联文档

- [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — 整体架构
- [`../docs/CHANGELOG.md`](../docs/CHANGELOG.md) — v1-v49 变更
- [`../docs/DEPLOY.md`](../docs/DEPLOY.md) — 打包发布流程
- [`../README.md`](../README.md) — monorepo 总入口