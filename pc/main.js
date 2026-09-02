// 红果漫剧 · Electron 版主进程
// 架构：Electron 壳 + Python 后端（复用现有签名/搜索/播放/缓存逻辑）
// 关闭窗口 = 退出整个程序（杀掉 Python 后端，零残留进程）
const { app, BrowserWindow, dialog, Menu } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const PORT = 5137; // 独立端口（Edge 版用 5127，互不影响）
const BASE_URL = `http://127.0.0.1:${PORT}`;

let pyProc = null;
let win = null;
let quitting = false;

// 轮询等待 Python 后端就绪
function waitForServer(timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;
    const check = () => {
      const req = http.get(BASE_URL + '/api/ping', (r) => { r.resume(); resolve(true); });
      req.on('error', () => {
        if (Date.now() > deadline) reject(new Error('后端启动超时'));
        else setTimeout(check, 300);
      });
      req.setTimeout(3000, () => req.destroy());
    };
    check();
  });
}

// 启动 Python 后端：
// - 开发模式：直接跑 backend/ 里的 Python 代码（需本机 Python + 依赖）
// - 打包模式：运行打包后的 红果后端.exe（后续用 PyInstaller 单独打，或 electron-builder extraResources）
function startBackend() {
  const fs = require('fs');
  const spawnOpts = {
    env: { ...process.env, HONGGUO_PORT: String(PORT), WEBVIEW_OFF: '1' },
    stdio: 'ignore',
    // Windows 上 detached 子进程会脱离 job object，父进程退出时不会被自动清理，
    // 这是之前多次启动后 5137 端口被一堆 红果后端.exe 残留的根因。
    // 强制 detached:false 让子进程绑到 Electron 的 job object（Windows 自动结束进程树）。
    windowsHide: true,
    detached: false,
  };
  // 打包模式：后端 exe 由 electron-builder 放进 resources/backend/
  const resBackend = path.join(process.resourcesPath, 'backend', '红果后端.exe');
  if (fs.existsSync(resBackend)) {
    pyProc = spawn(resBackend, [], spawnOpts);
    pyProc.on('exit', () => { pyProc = null; });
    return;
  }
  // 开发模式：backend/ 下的源码（优先用 hongguo-pc/app/venv 的 Python）
  const backendDir = path.join(__dirname, 'backend');
  const bundledExe = path.join(backendDir, '红果后端.exe');
  let cmd, args;
  if (fs.existsSync(bundledExe)) {
    cmd = bundledExe;
    args = [];
  } else {
    const venvPy = path.join(__dirname, '..', 'hongguo-pc', 'app', 'venv', 'Scripts', 'python.exe');
    cmd = fs.existsSync(venvPy) ? venvPy : (process.platform === 'win32' ? 'python' : 'python3');
    args = [path.join(backendDir, 'main_server.py')];
  }
  pyProc = spawn(cmd, args, spawnOpts);
  pyProc.on('exit', () => { pyProc = null; });
}

function killBackendTree() {
  if (!pyProc || !pyProc.pid) { pyProc = null; return; }
  const pid = pyProc.pid;
  if (process.platform === 'win32') {
    // Windows 上 pyProc.kill() 经常只发 SIGTERM 但 PyInstaller onefile windowed exe 不响应，
    // 导致后端进程残留。改用 taskkill /F /T 强杀 + 杀进程树（包括任何派生的 python 子进程）。
    try {
      require('child_process').execFileSync('taskkill', ['/F', '/T', '/PID', String(pid)],
        { stdio: 'ignore', windowsHide: true });
    } catch (e) { /* 进程可能已退出，忽略 */ }
  } else {
    try { pyProc.kill('SIGKILL'); } catch (e) {}
  }
  pyProc = null;
}

function buildMenu() {
  // 显式中文菜单（Windows 上 Electron 默认菜单是英文，自定义保证中文）
  const template = [
    {
      label: '文件',
      submenu: [
        { label: '刷新页面', accelerator: 'Ctrl+R', click: () => win && win.webContents.reload() },
        { label: '强制刷新', accelerator: 'Ctrl+Shift+R', click: () => win && win.webContents.reloadIgnoringCache() },
        { type: 'separator' },
        { label: '退出', accelerator: 'Ctrl+W', click: () => app.quit() },
      ],
    },
    {
      label: '编辑',
      submenu: [
        { label: '复制', accelerator: 'Ctrl+C', role: 'copy' },
        { label: '粘贴', accelerator: 'Ctrl+V', role: 'paste' },
        { label: '全选', accelerator: 'Ctrl+A', role: 'selectAll' },
      ],
    },
    {
      label: '视图',
      submenu: [
        { label: '全屏切换', accelerator: 'F11', click: () => {
            if (!win) return;
            win.setFullScreen(!win.isFullScreen());
          } },
        { label: '缩放重置', accelerator: 'Ctrl+0', role: 'resetZoom' },
        { label: '放大', accelerator: 'Ctrl+Plus', role: 'zoomIn' },
        { label: '缩小', accelerator: 'Ctrl+-', role: 'zoomOut' },
      ],
    },
    {
      label: '窗口',
      submenu: [
        { label: '最小化', role: 'minimize' },
        { label: '关闭', role: 'close' },
      ],
    },
    {
      label: '帮助',
      submenu: [
        { label: '开发者工具', accelerator: 'F12', click: () => win && win.webContents.openDevTools({ mode: 'detach' }) },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  buildMenu();

  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: '#0f1115',
    title: '红果漫剧 · 电脑版',
    autoHideMenuBar: true, // 顶部菜单栏（文件/编辑/...）自动隐藏，Alt 键临时显示
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  // 注册前端 IPC：原生窗口全屏切换（不进 HTML5 fullscreen，但通过 fs-mode class
  // 让 .topbar/.ctrl-bar 在播放页内被隐藏，鼠标活动时显示）
  require('electron').ipcMain.handle('hg:toggleFs', () => {
    if (!win) return false;
    win.setFullScreen(!win.isFullScreen());
    return win.isFullScreen();
  });
  require('electron').ipcMain.handle('hg:isFs', () => !!(win && win.isFullScreen()));
  // 全屏状态变化时通知前端，让其切换 body.fs-mode
  win.on('enter-full-screen', () => {
    win.webContents.send('fs-changed', true);
  });
  win.on('leave-full-screen', () => {
    win.webContents.send('fs-changed', false);
  });
  // ESC 退出全屏（Electron 原生全屏不像 HTML5 那样默认响应 ESC，需手动拦截）
  win.webContents.on('before-input-event', (event, input) => {
    if (input.type === 'keyDown' && input.key === 'Escape' && win && win.isFullScreen()) {
      win.setFullScreen(false);
      event.preventDefault();
    }
  });
  win.loadURL(BASE_URL);
  // 窗口关闭 = 退出整个应用（连同 Python 后端，零残留）
  win.on('closed', () => {
    win = null;
    if (!quitting) app.quit();
  });
}

// 单实例锁：防止用户多次双击 exe 生成多个 Electron + 后端进程
// 第二次启动时直接激活第一次的窗口，立刻退出
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(async () => {
    startBackend();
    try {
      await waitForServer();
    } catch (e) {
      dialog.showErrorBox('红果漫剧启动失败', String(e));
      app.quit();
      return;
    }
    createWindow();
  });
}

app.on('window-all-closed', () => {
  killBackendTree();
  // 兜底清理本机任何残留的同名后端进程（其他版本安装残留 / 上次崩溃没清掉的）
  if (process.platform === 'win32') {
    try {
      require('child_process').execFileSync('taskkill',
        ['/F', '/IM', '红果后端.exe', '/T'],
        { stdio: 'ignore', windowsHide: true });
    } catch (e) {}
  }
  quitting = true;
  app.quit();
});

app.on('will-quit', () => {
  killBackendTree();
});
