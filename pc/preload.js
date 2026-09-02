// 渲染进程预加载脚本（当前页面全用 fetch 调本地 API，无需额外桥接，留作扩展）
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('hongguoDesktop', {
  isDesktop: true,
  platform: process.platform,
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
  },
});
// 原生窗口全屏 API（替代 HTML5 fullscreen —— 触发 BrowserWindow 全屏，
// 前端用 body.fs-mode 隐藏 topbar / ctrl-bar）
contextBridge.exposeInMainWorld('hgFs', {
  toggle: () => ipcRenderer.invoke('hg:toggleFs'),
  isFs: () => ipcRenderer.invoke('hg:isFs'),
  onChange(cb) {
    ipcRenderer.removeAllListeners('fs-changed');
    ipcRenderer.on('fs-changed', (_e, fs) => cb(!!fs));
  },
});
