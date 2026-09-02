# -*- coding: utf-8 -*-
"""NAS 后端入口：启动 Flask（0.0.0.0:8000），数据目录 /data"""
import os
import sys

# 数据目录（Docker volume /data 或环境变量）
os.environ.setdefault("HONGGUO_DATA_DIR", "/data")
os.makedirs(os.environ["HONGGUO_DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"[nas] Flask 启动: 0.0.0.0:{port}, 数据目录: {os.environ['HONGGUO_DATA_DIR']}")
    # 走 run_server() 而不是直接 app.run()：会启动清理临时缓冲 +
    # LRU 缓存清理后台线程（v29+）。host="0.0.0.0" 让 NAS 局域网可访问。
    server.run_server(port=port, open_browser=False, host="0.0.0.0")
