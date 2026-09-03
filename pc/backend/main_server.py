# -*- coding: utf-8 -*-
"""Electron 版后端入口：启动 Flask 服务（复用 server.py / hongguo_core.py）

由 Electron 主进程 (main.js) 以子进程方式拉起。
端口默认 5137（与 Edge 版 5127 区分，可同时运行互不干扰）。

v46: 启动时尝试从 NAS (<NAS_IP>:8000) 同步 manual_series.json,
让 PC 端能搜到 quickapp 录入的字节火山内部剧集（如 100 集漫剧版）。
"""
# v47: PyInstaller 打包后 Python 默认 stdout 编码是 gbk,碰到 unicode(如 ⚠ \u26a0、
# 中文 emoji 等)会在 print() / 日志输出时抛 UnicodeEncodeError,让 ffmpeg stream 链崩,
# 前端收到 {"ok": false, "error": "..."} 显示"播放失败"。
# 在所有 print 之前(import Flask 之前)强制把 stdout/stderr 换成 UTF-8 + errors='replace',
# 保证后续所有 traceback / 日志 / jsonify error 字符串都能扛住任意字符。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    # PyInstaller frozen / 旧 Python 没 reconfigure 时, fallback 用 TextIOWrapper 包一层
    import io
    for _name in ("stdout", "stderr"):
        _s = getattr(sys, _name, None)
        if _s is not None and hasattr(_s, "buffer"):
            try:
                setattr(sys, _name, io.TextIOWrapper(_s.buffer, encoding="utf-8",
                                                    errors="replace", line_buffering=True))
            except Exception:
                pass

import os
import json
import time
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# v46: 启动时从 NAS 同步手工索引(quickapp 录入的剧集)
# MANUAL_PATH 必须和 hongguo_core.DATA_DIR/cache/manual_series.json 保持一致
# 打包模式: DATA_DIR = exe 同目录,所以用 sys.executable 而不是 __file__(后者指向 _MEIPASS 临时目录)
# NAS_URL: 你的 NAS 后端地址(安卓/平板端也指向它),通过环境变量 NAS_URL 覆盖
NAS_URL = os.environ.get("NAS_URL", "http://192.168.1.100:8000")
_DATA_ROOT = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) \
              else os.path.dirname(os.path.abspath(__file__))
MANUAL_PATH = os.path.join(_DATA_ROOT, "cache", "manual_series.json")


def sync_manual_from_nas():
    """尝试从 NAS 拉取 /api/manual_series,写入本地 cache"""
    try:
        req = urllib.request.Request(NAS_URL + "/api/manual_series",
                                      headers={"User-Agent": "hongguo-pc/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        items = data.get("items") or []
        if items:
            os.makedirs(os.path.dirname(MANUAL_PATH), exist_ok=True)
            with open(MANUAL_PATH, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            print(f"[backend] 从 NAS 同步 manual_series.json: {len(items)} 部", flush=True)
            return True
        print(f"[backend] NAS 返回 manual 为空,跳过同步", flush=True)
        return False
    except Exception as e:
        print(f"[backend] 同步 NAS manual 失败({type(e).__name__}): {e}", flush=True)
        return False


def periodic_sync():
    """每 5 分钟同步一次,保证 PC 端 NAS 新录入的 quickapp 立即可见"""
    while True:
        try:
            sync_manual_from_nas()
        except Exception as e:
            print(f"[backend] periodic sync error: {e}", flush=True)
        time.sleep(300)


# 启动时同步一次(后台,不阻塞 Flask 启动)
sync_thread = threading.Thread(target=sync_manual_from_nas, daemon=True)
sync_thread.start()
# 周期同步
periodic_thread = threading.Thread(target=periodic_sync, daemon=True)
periodic_thread.start()

from server import app  # noqa: E402  # 在 sync 之后 import,确保 MANUAL_PATH 加载后 server.py 才读

if __name__ == "__main__":
    port = int(os.environ.get("HONGGUO_PORT", "5137"))
    print(f"[backend] 红果漫剧后端服务: http://127.0.0.1:{port}", flush=True)
    # 不自动开浏览器（窗口由 Electron 管理）
    # v52: 优先使用 waitress，缺失则回退 Flask dev server
    try:
        from waitress import serve
        print(f"[backend] 使用 waitress 生产服务器 (threads=8)", flush=True)
        serve(app, host="127.0.0.1", port=port, threads=8, ident="hongguo-pc-backend")
    except ImportError:
        print(f"[backend] waitress 未安装,回退 Flask dev server", flush=True)
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True)