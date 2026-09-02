# -*- coding: utf-8 -*-
"""红果漫剧 PC 播放器 - Flask 服务层"""
import os
import sys
import json
import subprocess
import threading
import time
import webbrowser
import urllib.request
from pathlib import Path
from urllib.parse import quote

from flask import Flask, jsonify, request, send_file, send_from_directory, Response
import requests as _requests
from urllib.parse import urlparse, parse_qs, quote

APP_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    APP_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
sys.path.insert(0, str(APP_DIR))

import hongguo_core
from hongguo_core import (
    search_series, get_series_detail, resolve_video_url,
    fetch_category_page, TAB_VIDEO, TAB_COMIC,
    stream_manager, TEMP_DIR, DATA_DIR, ensure_device, clean_temp_at_start,
    set_cache_dir, add_manual_series, _load_manual_series,
)
# 缓存目录动态引用（set_cache_dir 会更新 hongguo_core.CACHE_DIR）
CACHE_DIR = lambda: hongguo_core.CACHE_DIR

# 退出时先清理 Edge 实例进程树（由 main.py 挂载）
_on_shutdown = None

# 用户数据（收藏/播放记录）——存后端文件，不依赖浏览器 profile
PREFS_PATH = DATA_DIR / "prefs.json"
_prefs_lock = threading.Lock()

# v49: NAS 同步 —— 观看记录/收藏以 NAS 为权威（安卓/PC 共享），本地 prefs.json 作为离线缓存
# NAS_URL: 你的 NAS 后端地址，通过环境变量 NAS_URL 覆盖
NAS_URL = os.environ.get("NAS_URL", "http://192.168.1.100:8000")
# NAS 可用性缓存（10 秒内不重复探测，避免每次 GET 都超时等 5s）
_NAS_OK = {"v": None, "t": 0.0}


def _nas_available() -> bool:
    """探测 NAS 是否可用（带 10s 缓存）"""
    now = time.time()
    if _NAS_OK["v"] is not None and now - _NAS_OK["t"] < 10:
        return _NAS_OK["v"]
    ok = False
    try:
        req = urllib.request.Request(NAS_URL + "/api/ping", headers={"User-Agent": "hongguo-pc/1.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    _NAS_OK.update({"v": ok, "t": now})
    return ok


def _nas_get_prefs() -> dict:
    """从 NAS 拉取 prefs；失败抛异常由调用方兜底"""
    req = urllib.request.Request(NAS_URL + "/api/prefs", headers={"User-Agent": "hongguo-pc/1.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read())
        return d.get("prefs") or {}


def _nas_post_prefs(prefs: dict) -> None:
    """推送 prefs 到 NAS（尽力，失败忽略）"""
    body = json.dumps(prefs, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(NAS_URL + "/api/prefs", data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "hongguo-pc/1.0"},
                                 method="POST")
    urllib.request.urlopen(req, timeout=5)


def _delete_with_meta(directory: Path, fname: str) -> bool:
    """删除缓存文件及其 .meta.json 伴生文件"""
    deleted = False
    for ext in ("", ".meta.json"):
        f = directory / (fname + ext)
        if f.exists():
            try:
                f.unlink()
                deleted = True
            except Exception:
                pass
    return deleted


def _sanitize_mp4_file(src: Path, dst: Path) -> bool:
    """进程内调用 mp4_sanitize 剔除非标 box（saio/saiz/senc/sgpd/sbgp）。
    v51: 替代 `python /app/mp4_sanitize.py` 子进程——/app 只存在于容器里，
    PC 版（尤其 PyInstaller 打包后）必然失败且被静默吞掉；进程内调用三端通用。"""
    try:
        import mp4_sanitize
        return bool(mp4_sanitize.sanitize_mp4(str(src), str(dst)))
    except Exception as e:
        print(f"[sanitize] 失败: {e}", flush=True)
        return False

app = Flask(__name__, static_folder=str(APP_DIR / "static"), static_url_path="")

# 页面心跳时间戳（前端每 3 秒调 /api/ping；watchdog 据此判断窗口是否假死）
LAST_PING = {"t": 0.0}


@app.route("/api/ping")
def api_ping():
    LAST_PING["t"] = time.time()
    return jsonify({"ok": True})


@app.route("/api/log")
def api_log():
    """前端 JS 错误上报（排错用）"""
    msg = request.args.get("msg", "")[:500]
    if msg:
        print(f"[前端JS] {msg}")
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET", "PUT"])
def api_settings():
    """应用设置：缓存目录自定义等。GET 查询，PUT 修改"""
    try:
        if request.method == "PUT":
            data = request.get_json(silent=True) or {}
            cache_dir = (data.get("cache_dir") or "").strip()
            # 总是调用：非空=设置自定义目录，空=清除自定义恢复默认
            try:
                set_cache_dir(cache_dir)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            return jsonify({"ok": True, "cache_dir": str(CACHE_DIR())})
        return jsonify({
            "ok": True,
            "cache_dir": str(CACHE_DIR()),
            "default_cache_dir": str(DATA_DIR / "cache"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """前端退出按钮：先清理 Edge 实例进程树，再延迟退出整个程序"""
    def _exit():
        try:
            if _on_shutdown:
                _on_shutdown()
        except Exception:
            pass
        time.sleep(0.5)
        try:
            os._exit(0)
        except Exception:
            pass
    threading.Thread(target=_exit, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/prefs", methods=["GET", "POST"])
def api_prefs():
    """用户数据（收藏/播放记录）持久化：NAS 为权威同步中心（安卓/PC 共享），
    本地 prefs.json 作为离线缓存。NAS 不可用时自动降级本地，恢复后下次同步。"""
    try:
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            # 只收我们认识的键，避免脏数据
            clean = {}
            for k in ("hg_fav", "hg_hist"):
                if k in data and isinstance(data[k], dict):
                    clean[k] = data[k]
            # 本地先写（离线可用）
            with _prefs_lock:
                PREFS_PATH.write_text(json.dumps(clean, ensure_ascii=False),
                                      encoding="utf-8")
            # 推送 NAS（尽力，失败不影响本端保存）
            if _nas_available():
                try:
                    _nas_post_prefs(clean)
                except Exception:
                    pass
            return jsonify({"ok": True})
        # GET：优先 NAS（权威），失败降级本地
        prefs = {}
        if _nas_available():
            try:
                prefs = _nas_get_prefs()
                # 同步本地副本
                if prefs:
                    with _prefs_lock:
                        PREFS_PATH.write_text(json.dumps(prefs, ensure_ascii=False),
                                              encoding="utf-8")
            except Exception:
                prefs = {}
        if not prefs:
            with _prefs_lock:
                if PREFS_PATH.exists():
                    try:
                        prefs = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
                    except Exception:
                        prefs = {}
        return jsonify({"ok": True, "prefs": prefs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# 模块加载时清理一次临时缓冲残留
_n_cleaned = clean_temp_at_start()
if _n_cleaned:
    print(f"[启动] 已清理临时视频残留 {_n_cleaned} 个")


@app.route("/")
def index():
    return send_from_directory(str(APP_DIR / "static"), "index.html")


# v35 启动清理：v28 时代的 raw_path（CDN 加密文件，文件名 {vid}.mp4，无 _h264 后缀）
# 和 stream_manager 的预缓存（解密+净化产物）重名冲突。清掉没有 meta.json 伴生的
# {vid}.mp4（这些是 v28 raw_path 加密文件），避免 /api/play 命中错误的"预缓存"
def _clean_v28_raw_path():
    """删除 v28 时代 raw_path 留下的加密文件（无 meta.json 伴生的 {vid}.mp4）。
    只有 stream_manager 会写 meta.json——所以没 meta 的都是 v28 时代的旧加密文件。"""
    cache_dir = CACHE_DIR()
    if not cache_dir.exists():
        return 0
    removed = 0
    freed = 0
    for f in cache_dir.glob("*.mp4"):
        # 跳过 _h264.mp4（净化产物路径不同）
        if f.stem.endswith("_h264"):
            continue
        # 检查是否有 meta.json 伴生（stream_manager 会写）
        meta = cache_dir / (f.name + ".meta.json")
        if not meta.exists():
            try:
                sz = f.stat().st_size
                f.unlink()
                removed += 1
                freed += sz
            except Exception:
                pass
    if removed:
        print(f"[v35_clean] 删除 v28 时代 raw_path 加密文件 {removed} 个，"
              f"释放 {freed/1048576:.1f} MB", flush=True)
    return removed


_clean_v28_raw_path()


@app.route("/api/search")
def api_search():
    keyword = request.args.get("keyword", "").strip()
    page = int(request.args.get("page") or 1)
    tab = request.args.get("tab", "").strip()  # ""=全部, 1=短剧, 2=漫剧
    try:
        result = search_series(keyword, page, tab)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# v46: 手工录入剧集(quickapp / 字节内部 sid,web search 搜不到的内容)
@app.route("/api/manual_series", methods=["GET", "POST"])
def api_manual_series():
    if request.method == "GET":
        return jsonify({"ok": True, "items": _load_manual_series(), "total": len(_load_manual_series())})
    # POST: 添加一条
    try:
        data = request.get_json(force=True, silent=True) or {}
        total = add_manual_series(data)
        return jsonify({"ok": True, "total": total})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# v46: 解析 quickapp 分享链接,返回 chapter_ids + 立即写入 manual_series
# 用法: POST /api/quickapp_parse {"url": "https://kylin.hainanyuyue.com/s/XXX/"}
@app.route("/api/quickapp_parse", methods=["POST"])
def api_quickapp_parse():
    import re as _re
    import urllib.request as _ur
    body = request.get_json(force=True, silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "url 必填"}), 400
    try:
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"})
        data = _ur.urlopen(req, timeout=20).read().decode("utf-8", errors="replace")
        # 找 _ROUTER_DATA JSON
        i = data.find("window._ROUTER_DATA = ")
        if i < 0:
            return jsonify({"ok": False, "error": "_ROUTER_DATA 未找到(URL 可能不是 quickapp 链接)"}), 400
        i += len("window._ROUTER_DATA = ")
        depth = 0
        start = end = i
        for k in range(i, min(i + 200000, len(data))):
            c = data[k]
            if c == "{":
                if depth == 0: start = k
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = k + 1
                    break
        router = json.loads(data[start:end])
        page = router["loaderData"]["video-animation-share_page"]["pageData"]
        sd = page["series_data"]
        chapter_ids = page.get("chapter_ids", []) or []
        item = {
            "series_id": chapter_ids[0] if chapter_ids else sd.get("video_id", ""),
            "title": sd.get("title", ""),
            "cover": sd.get("series_cover", ""),
            "episode_text": f"全{sd.get('serial_count', '?')}集",
            "tags": " / ".join(sd.get("category_list", [])) or sd.get("category", ""),
            "desc": sd.get("series_intro", "")[:300],
            "episode_cnt": sd.get("serial_count", 0),
            "vid_list": chapter_ids,
            "type": "漫剧",
            "source": "quickapp",
            "source_url": url,
            "play_url_first": sd.get("play_url", ""),
        }
        if not item["series_id"] or not item["title"]:
            return jsonify({"ok": False, "error": "解析结果缺 series_id 或 title"}), 400
        total = add_manual_series(item)
        return jsonify({"ok": True, "total": total, "item": item})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# v45: HTML 端 /api/category/page 直连（电脑版 HTML 调用的分类 API，
# NAS 后端之前没这个路由 → 推荐/分类加载失败 → UI 一片空白）。
# 实际 NAS 后端早就有 fetch_category_page() 函数（line 363 hongguo_core.py），
# 只是没包装成路由。补一个就行。
@app.route("/api/category/page")
def api_category_page():
    try:
        page_num = int(request.args.get("page_num") or 1)
        tab = request.args.get("tab", TAB_VIDEO).strip() or TAB_VIDEO
        sort_type = int(request.args.get("sort_type") or 1)
        items = fetch_category_page(page_num, tab, sort_type)
        return jsonify({"isSuccess": True, "recommendList": items})
    except Exception as e:
        return jsonify({"isSuccess": False, "error": str(e)}), 500


@app.route("/api/series")
def api_series():
    series_id = request.args.get("series_id", "").strip()
    if not series_id:
        return jsonify({"ok": False, "error": "缺少 series_id"}), 400
    try:
        detail = get_series_detail(series_id)
        return jsonify({"ok": True, **detail})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/play")
def api_play():
    """播放：默认返回 CDN 直链（浏览器原生缓冲，最快，支持 Range 拖进度条）；
    mode=download 时走 ffmpeg 下载到本地（兜底/缓存场景）。
    """
    vid = request.args.get("vid", "").strip()
    quality = request.args.get("quality", "").strip()
    mode = request.args.get("mode", "").strip()
    persist = request.args.get("persist", "").strip() in ("1", "true", "yes")
    sid = request.args.get("sid", "").strip()
    ep = int(request.args.get("ep", "0") or 0)
    title = request.args.get("title", "").strip()
    # 客户端能力声明（来自 APK hevc=yes/no）
    # no_hevc → 客户端不支持 HEVC 硬解 → 强制走 player 页 H.264 720p
    client_cap = request.args.get("client_cap", "").strip()
    no_hevc = "no_hevc" in client_cap
    if not vid:
        return jsonify({"ok": False, "error": "缺少 vid"}), 400

    # v48: 缓存优先 —— 不碰任何网络请求，已有净化产物（mp4 + meta.json）的集直接秒开。
    # 之前缓存检查在网络解析之后（要等 resolve_video_url 8 次重试 / 每次 25s timeout），
    # 导致"已有缓存，切下一集还得加载半天"。现在命中即返回 /local-video/{vid}.mp4。
    _cp = CACHE_DIR() / f"{vid}.mp4"
    _cm = CACHE_DIR() / f"{vid}.mp4.meta.json"
    if _cp.exists() and _cp.stat().st_size > 100000 and _cm.exists():
        # 补写一次 meta（带 sid/ep/title 时同步进度信息）
        if sid or ep or title:
            try:
                stream_manager.write_meta(CACHE_DIR(), f"{vid}.mp4", {
                    "sid": sid, "vid": vid, "title": title,
                    "ep_no": int(ep) if ep else 0,
                    "quality": quality,
                    "mtime": _cp.stat().st_mtime,
                })
            except Exception:
                pass
        print(f"[play] 缓存命中(秒开): {vid}.mp4 ({_cp.stat().st_size} 字节)", flush=True)
        return jsonify({"ok": True, "direct": True, "task": {
            "vid": vid, "status": "ready", "url": f"/local-video/{vid}.mp4",
            "quality": quality or "",
            "qualities": [],
            "total_size": _cp.stat().st_size,
            "error": None, "cached": True,
        }})

    # 直链优先（默认）：CDN 地址浏览器直接播，绕开 ffmpeg 单连接下载瓶颈
    if mode != "download":
        # 选择优先画质源
        # - HQ=1080p/App 源（HEVC 原生，需客户端支持 HEVC 硬解）
        # - 720p/player 源（H.264 720p 兼容性最强，所有设备都能播）
        # 客户端声明 no_hevc → 强制走 720p H.264（保证播放）；否则尝试 1080p HEVC
        # 逻辑修复版（v23+）：
        #   - want_hq=True（HQ 1080p）→ 先试 App 1080p HEVC 源
        #   - 任意路径找到 source → real_url 不为空
        #   - no_hevc=True → 直接跳过所有 HEVC 路径（包括 fallback），找不到 H.264 就报错
        want_hq = (not no_hevc) and (quality in ("1080p", "hq", "high", "") or quality.startswith("video_5"))
        real_url = ""
        used_source = ""
        # 步骤1：HQ= App 签名接口（1080p，可能 HEVC）— 仅 want_hq=True 时尝试
        if want_hq:
            try:
                import time as _t
                # v51: 8 次×3s 阻塞太久（最坏 3 分钟），收敛到 3 次×2s；
                # info 预初始化，避免首次 resolve 抛异常时 NameError
                info = {}
                for _try in range(3):
                    info = resolve_video_url(vid, quality)
                    url = info.get("url", "")
                    if url and any(k in url for k in ("hgweb", "reading-videocdn", "v11-", "v26-", "v3-hgweb")):
                        break
                    _t.sleep(2)
                if info.get("url"):
                    real_url = info["url"]
                    used_source = "app_sign_1080p"
                    print(f"[play] App HQ 源 ({info.get('quality')}): {real_url[:60]}")
            except Exception as _e:
                print(f"[play] App HQ 源失败: {_e}")
        # 步骤2：720p/player 页 H.264 源（兼容回退，必试）
        if not real_url and sid:
            try:
                _html = _requests.get(
                    f"https://hongguoduanju.com/player/{sid}/{vid}",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                             "Accept-Encoding": "identity"},
                    timeout=15).text
                import re as _re
                _m = _re.search(r'"main_url":"([^"]+)"', _html)
                if _m:
                    real_url = _m.group(1).replace("\\u002F", "/")
                    used_source = "player_720p"
                    info = {"quality": "720p", "qualities": [{"label": "720p", "key": "video_4", "height": 720}], "total_size": 0}
                    print(f"[play] player 页 H.264 源: {real_url[:60]}")
            except Exception as _e:
                print(f"[play] player 页解析失败: {_e}")
        # 步骤3：App 接口其他 quality（兜底） — 仅 no_hevc=False 时尝试
        if not real_url and not no_hevc:
            try:
                for _alt_q in ("720p", "576p", "480p", "360p"):
                    info = resolve_video_url(vid, _alt_q)
                    if info.get("url"):
                        real_url = info["url"]
                        used_source = f"app_sign_{_alt_q}"
                        print(f"[play] App {_alt_q} 兑底: {real_url[:60]}")
                        break
            except Exception as _e:
                print(f"[play] App 兑底失败: {_e}")
        # 失败兜底：找不到任何源
        if not real_url:
            err_msg = "无 720p/H.264 源（player 页解析失败）"
            if no_hevc:
                err_msg = "客户端不支持 HEVC，且本剧无可用 H.264 源。请用桌面端访问 PC 版"
            print(f"[play] 失败: {err_msg}", flush=True)
            return jsonify({"ok": False, "error": err_msg, "no_hevc_only": no_hevc}), 400
        if real_url:
            # 关键：把视频完整下载到 NAS 本地 → WebView 加载同源 HTTP（NAS 内网），
            # 绕开 WebView 直连 CDN 的所有防盗链/混合内容/TLS指纹问题
            try:
                # v35: 先看 stream_manager 预缓存（/api/cache/range 已经下好后续 3 集到持久缓存）
                # 注意：必须检查 meta.json 伴生文件！只有 stream_manager 写的文件才有 meta。
                # v28 时代的 raw_path（{vid}.mp4 无 meta）是 CDN 加密文件——**不能**当净化产物用！
                cached_path = CACHE_DIR() / f"{vid}.mp4"
                cached_meta = CACHE_DIR() / f"{vid}.mp4.meta.json"
                if (cached_path.exists() and cached_path.stat().st_size > 100000
                        and cached_meta.exists()):
                    print(f"[play] 命中预缓存: {cached_path} "
                          f"({cached_path.stat().st_size} 字节)，跳过下载+净化", flush=True)
                    return jsonify({"ok": True, "direct": True, "task": {
                        "vid": vid, "status": "ready",
                        "url": f"/local-video/{vid}.mp4",
                        "quality": info.get("quality") or "",
                        "qualities": info.get("qualities") or [],
                        "total_size": info.get("total_size") or 0,
                        "error": None, "cached": True,
                    }})
                # v35: 旧 raw_path（无 meta 的 {vid}.mp4 是 v28 加密文件）— 直接删了重新走下载+净化
                if cached_path.exists() and not cached_meta.exists():
                    try:
                        cached_path.unlink()
                        print(f"[play] 清掉 v28 raw_path 加密文件: {cached_path}", flush=True)
                    except Exception:
                        pass
                # v51: 统一用 hongguo_core.CACHE_DIR（尊重 settings.json 自定义目录）。
                # 原来硬编码 /data：PC 版没人设 NAS_DATA_DIR → Windows 上 Path("/data")
                # 指向盘符根 \data，产物与 /local-video 读的 CACHE_DIR 错位 → 404。
                local_dir = CACHE_DIR()
                local_dir.mkdir(parents=True, exist_ok=True)
                # v44: 恢复 v37 的"临时 + 净化"分离路径（v42 把 raw_path 和 h264_path
                # 改成同一个导致净化步骤永远被跳过——产物保留 CENC box，WebView 拒绝播放）
                raw_path = local_dir / f"{vid}.raw.mp4"  # CDN 加密文件（中间产物，可删除）
                h264_path = local_dir / f"{vid}.mp4"     # 净化产物（最终交付前端）
                if not h264_path.exists() or h264_path.stat().st_size < 1024:
                    if not raw_path.exists() or raw_path.stat().st_size < 1024:
                        with _requests.get(real_url, headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                            "Accept-Encoding": "identity",
                        }, stream=True, timeout=180) as r:
                            r.raise_for_status()
                            with open(raw_path, "wb") as f:
                                for chunk in r.iter_content(1 << 20):  # 1MB chunks
                                    if chunk:
                                        f.write(chunk)
                        print(f"[play] 下载完成: {raw_path} ({raw_path.stat().st_size} 字节)")
                # 文件下载完毕 → 走解密 + 净化 + faststart
                # 输出严格 ISO BMFF 兼容 MP4，ExoPlayer 严格 demuxer 也能解析
                if not h264_path.exists() or h264_path.stat().st_size < 1024:
                    codec_info = ""
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "stream=codec_name,width,height",
                         "-of", "csv=p=0", str(raw_path)],
                        capture_output=True, text=True, timeout=60,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    codec_info = probe.stdout.strip()
                    print(f"[play] 原文件编码: {codec_info} → 解密 + 净化 + faststart", flush=True)

                    # 关键：原文件 mdat 是 CENC 加密的（0 个 NAL start code），
                    # 不解密 ffmpeg 读 0 帧，ExoPlayer 报 ERROR_CODE_PARSING_CONTAINER_MALFORMED
                    content_key = info.get("content_key")
                    if content_key:
                        print(f"[play] 使用 content_key 解密 ({len(content_key)} hex chars)", flush=True)

                    # 步骤 A: ffmpeg 先解密（-decryption_key）+ 净化（剔除非标 box）+ faststart
                    # 用 ffmpeg 一次性完成：解密 → 重封装 → 净化 → faststart
                    ffmpeg_cmd = ["ffmpeg", "-y"]
                    if content_key:
                        ffmpeg_cmd.extend(["-decryption_key", content_key])
                    ffmpeg_cmd.extend([
                        "-i", str(raw_path),
                        "-c", "copy",         # 不重新编码，直接复制加密数据流（解密后的）
                        "-movflags", "+faststart",
                        str(h264_path)
                    ])
                    sanitize_proc = subprocess.run(
                        ffmpeg_cmd, capture_output=True, text=True, timeout=600,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if sanitize_proc.returncode != 0:
                        print(f"[play] ⚠ ffmpeg 失败 rc={sanitize_proc.returncode}", flush=True)
                        print(f"[play] stderr: {sanitize_proc.stderr[-400:]}", flush=True)
                        # 兜底：进程内净化（v51: 原来调 python /app/... 子进程，PC 版永远失败）
                        if not _sanitize_mp4_file(raw_path, h264_path):
                            print(f"[play] ⚠ sanitize 也失败", flush=True)

                    # 步骤 B: 净化（剔 ffmpeg remux 保留下来的 sgpd/sbgp/saio/senc 等非标 box）
                    # v51: 步骤 A 已经带 +faststart，删掉原先多余的"二次 faststart remux"——
                    # 那一遍把整个视频完整读+写一遍却什么都不改；净化也改为进程内调用。
                    if h264_path.exists() and h264_path.stat().st_size > 1024:
                        san2_path = Path(str(h264_path) + ".san2.mp4")
                        if _sanitize_mp4_file(h264_path, san2_path):
                            import shutil
                            shutil.move(str(san2_path), str(h264_path))
                            print(f"[play] 二次净化完成", flush=True)
                        else:
                            print(f"[play] ⚠ 二次净化失败 (保留 ffmpeg 产物)", flush=True)

                    # 注意：v28 不再强制转 H.264（容器 CPU 软转爆表 + 缺 QSV 库）
                    # 产物保持解密后的原 HEVC，由安卓端 WebView <video> 元素播（Chrome 内核硬解 HEVC）

                    print(f"[play] 净化产物: {h264_path} "
                          f"({h264_path.stat().st_size if h264_path.exists() else 0} 字节)",
                          flush=True)
                use_h264 = h264_path.exists() and h264_path.stat().st_size >= 1024
                serve_vid = vid  # v42: 统一文件名为 {vid}.mp4
                # v37 fix: 用 stream_manager.write_meta 而不是 VideoStreamManager.write_meta
                # （server.py 没 import VideoStreamManager 类，原版每次都 NameError → catch 走代理流）
                # 同时删除 raw_path（CDN 加密文件，中间产物，不该留在缓存里污染 UI）
                if use_h264:
                    try:
                        stream_manager.write_meta(local_dir, f"{vid}.mp4", {  # v42: 统一文件名
                            "sid": sid, "vid": vid, "title": title,
                            "ep_no": int(ep) if ep else 0,
                            "quality": info.get("quality") or quality,
                            "source": used_source,
                            "mtime": h264_path.stat().st_mtime,
                        })
                    except Exception as _me:
                        print(f"[play] 写 meta.json 失败: {_me}", flush=True)
                    # v51: 修复条件写反——原来 meta 存在时反而不删 raw，
                    # 导致 {vid}.raw.mp4 加密中间产物残留进 /api/cache 列表污染 UI
                    if raw_path.exists():
                        try: raw_path.unlink()
                        except Exception: pass
                return jsonify({"ok": True, "direct": True, "task": {
                    "vid": vid, "status": "ready", "url": f"/local-video/{serve_vid}.mp4",
                    "quality": info.get("quality") or "",
                    "qualities": info.get("qualities") or [],
                    "total_size": info.get("total_size") or 0,
                    "error": None,
                }})
            except Exception as e:
                print(f"[play] 本地下载失败，回退代理流: {e}")
                # 失败兜底走代理流
                proxy_url = f"/api/cdn?u={quote(real_url, safe='')}"
                return jsonify({"ok": True, "direct": True, "task": {
                    "vid": vid, "status": "ready", "url": proxy_url,
                    "quality": info.get("quality") or "",
                    "qualities": info.get("qualities") or [],
                    "total_size": info.get("total_size") or 0,
                    "error": None,
                }})
        # 6 次都拿不到友好节点 → 返回错误（前端 toast 显示，不死循环）
        return jsonify({"ok": False, "error": "暂无可用播放节点，请稍后重试"}), 503
    # 直链解析全部失败的 fallback（可能 return 没进来，下面 try 进 ffmpeg 分支）
    try:
        task = stream_manager.start(vid, quality, persist=persist,
                                    sid=sid, ep_no=ep, title=title)
        if task.get("fname"):
            task["url"] = f"/stream/{task['fname']}"
        return jsonify({"ok": True, "direct": False, "task": task})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cdn")
def api_cdn_proxy():
    """CDN 代理（无 Referer）：WebView 加载 /api/cdn?u=<原直链>，
    后端用 Python 抓 CDN（无 Referer → v26/v11 类 206）流式返回。
    透传 Range 支持拖进度条。绕开 WebView 直连 CDN 的环境差异（TLS 指纹等）。
    """
    target = request.args.get("u", "").strip()
    if not target or not target.startswith("http"):
        return "missing u", 400
    # 关键：不带 Referer（v26/v11 类 CDN 对无 Referer 请求返回 206；带 Referer 反而 403）
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept-Encoding": "identity",
    }
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header
    try:
        upstream = _requests.get(target, headers=headers, stream=True, timeout=30)
    except Exception as e:
        return f"upstream error: {e}", 502
    if upstream.status_code in (403, 404):
        upstream.close()
        return "upstream rejected", 502
    def gen():
        try:
            for chunk in upstream.iter_content(64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()
    passthrough = {k: v for k, v in upstream.headers.items()
                   if k.lower() in ("content-length", "content-range", "accept-ranges", "content-type")}
    return Response(gen(), status=upstream.status_code, headers=passthrough,
                    content_type=upstream.headers.get("Content-Type", "video/mp4"))


@app.route("/local-video/<vid>.mp4")
def local_video(vid):
    """提供 NAS 本地缓存的视频（WebView 加载同源 HTTP，绕开 CDN 防盗链）
    Flask send_file 默认支持 Range 响应（拖进度条 OK）。
    """
    # v33: 用 CACHE_DIR() 而非 hardcode 路径——和 stream_manager 持久缓存位置一致
    local_path = CACHE_DIR() / f"{vid}.mp4"
    if not local_path.exists() or local_path.stat().st_size < 1024:
        return "video not ready", 404
    return send_file(
        str(local_path),
        mimetype="video/mp4",
        conditional=True,  # 支持 If-Range / Range 自动 206
        as_attachment=False,
    )


@app.route("/api/cache/add")
def api_cache_add():
    """主动把视频加进持久缓存（后台异步下载，不阻塞前端）"""
    vid = request.args.get("vid", "").strip()
    quality = request.args.get("quality", "").strip()
    sid = request.args.get("sid", "").strip()
    ep = int(request.args.get("ep", "0") or 0)
    title = request.args.get("title", "").strip()
    if not vid:
        return jsonify({"ok": False, "error": "缺少 vid"}), 400
    try:
        task = stream_manager.start(vid, quality, persist=True,
                                    sid=sid, ep_no=ep, title=title)
        return jsonify({"ok": True, "task": task})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cache/range")
def api_cache_range():
    """批量缓存某个剧集的指定区间（from/to 都按 1 开始。全部=from=1 to=eps_cnt）"""
    sid = request.args.get("sid", "").strip()
    quality = request.args.get("quality", "").strip()
    try:
        fr = int(request.args.get("from", "1") or 1)
        to = int(request.args.get("to", "0") or 0)
    except Exception:
        return jsonify({"ok": False, "error": "from/to 格式错误"}), 400
    if not sid:
        return jsonify({"ok": False, "error": "缺少 sid"}), 400
    try:
        d = get_series_detail(sid)
    except Exception as e:
        return jsonify({"ok": False, "error": "获取剧集信息失败: " + str(e)}), 500
    eps = d.get("episodes") or []
    title = d.get("title") or ""
    if not eps:
        return jsonify({"ok": False, "error": "无可缓存的剧集"}), 400
    if to <= 0 or to > len(eps):
        to = len(eps)
    fr = max(1, fr)
    targets = []
    for i in range(fr - 1, to):
        ep = eps[i]
        task = stream_manager.start(ep["vid"], quality, persist=True,
                                    sid=sid, ep_no=i + 1, title=title)
        targets.append({"ep_no": i + 1, "vid": ep["vid"], "status": task["status"]})
    return jsonify({"ok": True, "title": title, "total": len(targets),
                    "submitted": targets})


@app.route("/api/task")
def api_task():
    vid = request.args.get("vid", "").strip()
    quality = request.args.get("quality", "").strip()
    task = stream_manager.get(vid, quality)
    # downloading 也返回流地址：分片 MP4 支持边下边播（前端 2% 即可开播）
    if task.get("fname"):
        task["url"] = f"/stream/{task['fname']}"
    return jsonify({"ok": True, "task": task})


@app.route("/stream/<path:filename>")
def stream_file(filename):
    """边下边播: cache/ 与 temp/ 都支持读取（cache 优先）。
    若对应下载任务仍在 downloading，用阻塞流式转发——
    读到文件尾部就等待 ffmpeg 继续写入，实现"下载 2% 即开播、边看边缓冲"。
    """
    for base in (CACHE_DIR(), TEMP_DIR):
        filepath = base / filename
        if filepath.exists():
            task = stream_manager.get_by_fname(filename)
            if task and task.get("status") == "downloading":
                return _stream_partial(filepath, task)
            return send_file(
                str(filepath),
                mimetype="video/mp4",
                conditional=True,
                max_age=0,
                download_name=filename,
            )
    return jsonify({"ok": False, "error": "文件不存在"}), 404


def _stream_partial(filepath: Path, task: dict):
    """阻塞流式：边读边等 ffmpeg 写入，直到任务完成（分片 MP4 边下边播的关键）"""
    def _parse_range(header: str) -> int:
        try:
            if header and header.startswith("bytes="):
                start = header.split("=", 1)[1].split("-", 1)[0].strip()
                return max(int(start), 0)
        except Exception:
            pass
        return 0

    offset = _parse_range(request.headers.get("Range", ""))
    size_now = filepath.stat().st_size
    # 请求起点超出当前已写大小 → 等待文件增长后再开始
    while offset >= size_now and task.get("status") == "downloading":
        time.sleep(0.5)
        try:
            size_now = filepath.stat().st_size
        except Exception:
            break

    def gen():
        pos = offset
        while True:
            try:
                size = filepath.stat().st_size
                if size > pos:
                    with open(filepath, "rb") as f:
                        f.seek(pos)
                        chunk = f.read(512 * 1024)
                        if chunk:
                            pos += len(chunk)
                            yield chunk
                            continue
                # 读到当前 EOF
                if task.get("status") in ("ready", "error"):
                    break  # 下载结束/失败 → 流结束
                time.sleep(0.4)
            except Exception:
                break

    # 注意：Werkzeug dev server 对 206+chunked(未知总长) 的组合支持有问题
    # （第一块后停止迭代、body 空）。边下边播阶段用 200 chunked 流式，
    # ready 后前端会切回 send_file（完整支持 Range/seek）。
    headers = {
        "Cache-Control": "no-store",
    }
    return Response(gen(), status=200, mimetype="video/mp4", headers=headers)


@app.route("/api/cache/file/<path:filename>", methods=["DELETE"])
def api_cache_delete_one(filename):
    """删除某个具体缓存视频（自动判断 cache 或 temp，连带删 .meta.json）"""
    deleted = False
    for base in (CACHE_DIR(), TEMP_DIR):
        if _delete_with_meta(base, filename):
            deleted = True
    if deleted:
        return jsonify({"ok": True, "removed": 1})
    return jsonify({"ok": False, "error": "文件不存在"}), 404


@app.route("/api/device")
def api_device():
    try:
        cfg = ensure_device()
        return jsonify({"ok": True, "device_id": cfg["device_id"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/cache", methods=["GET", "POST"])
def api_cache():
    """缓存管理：GET 查看，POST 清空（仅清空 cache/，temp/ 是临时缓冲）"""
    try:
        if request.method == "POST":
            # v51: 连带删除 .meta.json 伴生文件（原来只删 *.mp4，meta 残留成孤儿）
            cache_dir = CACHE_DIR()
            n = 0
            for f in list(cache_dir.glob("*.mp4")):
                if _delete_with_meta(cache_dir, f.name):
                    n += 1
            # 顺手清掉孤儿 meta（mp4 已不存在的）
            for m in cache_dir.glob("*.mp4.meta.json"):
                if not (cache_dir / m.name[:-len(".meta.json")]).exists():
                    try:
                        m.unlink()
                    except Exception:
                        pass
            return jsonify({"ok": True, "removed": n})
        files = []
        total = 0
        for f in sorted(CACHE_DIR().glob("*.mp4"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            size = f.stat().st_size
            total += size
            meta = stream_manager.read_meta(CACHE_DIR(), f.name) or {}
            files.append({
                "name": f.name, "size": size, "mtime": f.stat().st_mtime,
                "sid": meta.get("sid", ""), "vid": meta.get("vid", ""),
                "title": meta.get("title", ""), "ep_no": meta.get("ep_no", 0),
                "quality": meta.get("quality", ""),
            })
        return jsonify({"ok": True, "files": len(files),
                        "total_mb": round(total / 1048576, 1),
                        "list": files[:200]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ═══ 视频缓存清理（v33+，v51 统一为单一 24h 策略）═══════════════════════
# 保留策略（用户确认）：缓存 24 小时。
# - 优先按 meta.json 的 processed_at（缓存完成时间）判断；
# - 读不到 meta 时按文件 mtime 兜底（raw 中间产物 / 异常残留都能清掉）。
# v51 修复：
# - 原来"7 天主清理 + 24h mtime 保底清理"两条线并行，24h 保底先删，
#   7 天主清理永远轮不到 → v48 想保留缓存的目标实际没生效。现统一为 24h。
# - 原来 CACHE_VIDEOS_DIR 硬编码 /data：PC 版清理线程根本不工作（路径错位），
#   settings.json 自定义缓存目录也永不清。现动态用 CACHE_DIR()。
CACHE_CLEAN_INTERVAL_SEC = 1800          # 每 30 分钟扫一轮
CACHE_AGE_LIMIT_SEC = 24 * 3600          # 缓存保留 24 小时


def _delete_cache_file(f: Path) -> None:
    """删除缓存视频文件及其 .meta.json 伴生文件"""
    for victim in (f, f.with_suffix(f.suffix + ".meta.json"),
                   f.parent / (f.stem + ".meta.json")):
        try:
            if victim.exists() and victim.is_file():
                victim.unlink()
        except Exception:
            pass


def _read_processed_at(f: Path) -> float:
    """从 .meta.json 读 processed_at；读不到返回 0"""
    for candidate in (f.with_suffix(f.suffix + ".meta.json"),
                      f.parent / (f.stem + ".meta.json")):
        try:
            if candidate.exists():
                import json as _json
                meta = _json.loads(candidate.read_text(encoding="utf-8") or "{}")
                v = meta.get("processed_at")
                if isinstance(v, (int, float)) and v > 0:
                    return float(v)
        except Exception:
            pass
    return 0.0


def _do_cache_clean():
    """删除超过 24 小时的缓存视频（processed_at 优先，mtime 兜底）"""
    cache_dir = CACHE_DIR()  # v51: 动态取，尊重 settings.json 自定义缓存目录
    if not cache_dir.exists():
        return
    now = time.time()
    removed = 0
    freed = 0
    for f in cache_dir.glob("*.mp4"):
        if not f.is_file():
            continue
        processed_at = _read_processed_at(f)
        # 没 meta 或 processed_at=0 → 用文件 mtime 兜底
        ts = processed_at if processed_at > 0 else f.stat().st_mtime
        if now - ts < CACHE_AGE_LIMIT_SEC:
            continue  # 还年轻，跳过
        sz = f.stat().st_size
        _delete_cache_file(f)
        removed += 1
        freed += sz
    # 顺手清掉孤儿 meta（mp4 已不存在的）
    for m in cache_dir.glob("*.mp4.meta.json"):
        if not (cache_dir / m.name[:-len(".meta.json")]).exists():
            try:
                m.unlink()
            except Exception:
                pass
    if removed:
        print(f"[cache_cleaner] 清理 {removed} 个超 24h 缓存文件，"
              f"释放 {freed/1048576:.1f} MB", flush=True)


def _cache_cleaner_loop():
    """清理线程：每 30 分钟扫一轮，删除超 24h 的缓存"""
    time.sleep(60)  # 启动后先睡 60 秒
    while True:
        try:
            _do_cache_clean()
        except Exception as e:
            print(f"[cache_cleaner] err: {e}", flush=True)
        time.sleep(CACHE_CLEAN_INTERVAL_SEC)


def run_server(port: int = 5127, open_browser: bool = True, host: str = "127.0.0.1"):
    # 启动时清理临时缓冲（残留下次启动看会浪费空间）
    n = clean_temp_at_start()
    if n:
        print(f"已清理 {n} 个临时视频残留")
    # 启动缓存清理线程（每 30 分钟扫一轮，删超 24h 的缓存）
    threading.Thread(target=_cache_cleaner_loop, daemon=True).start()
    print(f"[cache_cleaner] 已启动：缓存保留 {CACHE_AGE_LIMIT_SEC // 3600}h，"
          f"扫描间隔 {CACHE_CLEAN_INTERVAL_SEC}s")
    if open_browser:
        def _open():
            import time
            time.sleep(1.0)
            webbrowser.open(f"http://127.0.0.1:{port}")
        threading.Thread(target=_open, daemon=True).start()
    print(f"红果漫剧播放器: http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    run_server()
