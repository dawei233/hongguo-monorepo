# -*- coding: utf-8 -*-
"""
红果漫剧 PC 播放器 - 核心后端
提供: 设备管理 / 搜索 / 剧集详情 / 视频流式解密
"""
import os
import re
import sys
import json
import time
import base64
import binascii
import hashlib
import subprocess
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import quote, urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

# ─── 路径配置 ────────────────────────────────────────────────────────────────
def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)

if _is_frozen():
    # 打包运行: 资源在 _MEIPASS(只读), 数据写到 exe 同目录
    APP_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    DATA_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
    # 安卓（Chaquopy）：数据写到 app 私有目录（由 app_server 注入）
    _data_env = os.environ.get("HONGGUO_DATA_DIR")
    if _data_env:
        DATA_DIR = Path(_data_env)
    else:
        DATA_DIR = APP_DIR

LIUSHEN_DIR = APP_DIR / "liushen"
CONFIG_PATH = DATA_DIR / "config.json"
SETTINGS_PATH = DATA_DIR / "settings.json"

# ─── 可配置缓存目录 ──────────────────────────────────────────────────────
_SETTINGS: Dict[str, Any] = {}


def load_settings() -> Dict[str, Any]:
    """读取 settings.json（缓存目录等用户配置）"""
    global _SETTINGS
    try:
        if SETTINGS_PATH.exists():
            _SETTINGS = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        _SETTINGS = {}
    return _SETTINGS


def _resolve_cache_dir() -> Path:
    """缓存目录：settings.json 里 cache_dir 自定义优先，否则默认 DATA_DIR/cache/videos。
    注意：默认放在 videos/ 子目录，与 server.py /api/play 和 /local-video/<vid>.mp4
    服务端下载+净化产物路径一致，方便预缓存（/api/cache/range）和 /api/play 复用。"""
    custom = str(_SETTINGS.get("cache_dir") or "").strip()
    if custom:
        try:
            d = Path(custom)
            d.mkdir(parents=True, exist_ok=True)
            return d
        except Exception:
            pass
    d = DATA_DIR / "cache" / "videos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def set_cache_dir(path: str) -> Path:
    """运行中修改缓存目录：写 settings.json 并更新全局 CACHE_DIR。
    传空字符串 = 清除自定义设置，恢复默认 DATA_DIR/cache"""
    global _SETTINGS, CACHE_DIR
    path = (path or "").strip()
    if not path:
        _SETTINGS.pop("cache_dir", None)
        try:
            SETTINGS_PATH.write_text(json.dumps(_SETTINGS, ensure_ascii=False),
                                     encoding="utf-8")
        except Exception:
            pass
        CACHE_DIR = _resolve_cache_dir()
        return CACHE_DIR
    try:
        d = Path(path)
        d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise RuntimeError(f"缓存目录不可用: {e}")
    _SETTINGS["cache_dir"] = path
    try:
        SETTINGS_PATH.write_text(json.dumps(_SETTINGS, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception:
        pass
    CACHE_DIR = d
    return CACHE_DIR


load_settings()
CACHE_DIR = _resolve_cache_dir()
TEMP_DIR = DATA_DIR / "temp"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)


def clean_temp_at_start() -> int:
    """启动时清理临时目录中残留的视频文件，返回清理数量"""
    n = 0
    for f in TEMP_DIR.glob("*.mp4"):
        try:
            f.unlink()
            n += 1
        except Exception:
            pass
    return n

if str(LIUSHEN_DIR) not in sys.path:
    sys.path.insert(0, str(LIUSHEN_DIR))
from flurl.core import core_sixgod  # noqa: E402

# ffmpeg 二进制：优先应用自带，其次插件目录，最后系统 PATH
FFMPEG_CANDIDATES = [
    APP_DIR / "bin" / "ffmpeg.exe",
    DATA_DIR / "bin" / "ffmpeg.exe",
    APP_DIR.parent / "short-drama-downloader" / "源码_开源版" / "插件" / "ffmpeg.exe",
    APP_DIR.parent / "short-drama-downloader" / "短剧下载神器开源版" / "_internal" / "ffmpeg.exe",
]

def find_ffmpeg() -> str:
    for cand in FFMPEG_CANDIDATES:
        if cand.exists():
            return str(cand)
    return "ffmpeg"  # 系统 PATH 兜底

FFMPEG = find_ffmpeg()

USER_AGENT = (
    "com.phoenix.read/71332 (Linux; U; Android 16; zh_CN; 25053RT47C; "
    "Build/BP2A.250605.031.A3; Cronet/TTNetVersion:04657795 2026-01-23 "
    "QuicVersion:c67e9834 2025-09-08)"
)
WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
SITE_BASE = "https://hongguoduanju.com"

VIDEO_MODEL_URL_TEMPLATE = (
    "https://api5-normal-sinfonlineb.fqnovel.com/novel/player/multi_video_model/v1/"
    "?iid={install_id}&device_id={device_id}&ac=wifi&channel=update_64&aid=8662"
    "&app_name=novelread&version_code=71332&version_name=7.1.3.32"
    "&device_platform=android&os=android&ssmix=a&device_type=25053RT47C"
    "&device_brand=Redmi&language=zh&os_api=36&os_version=16"
    "&manifest_version_code=71332&resolution=1280*2772&dpi=520"
    "&update_version_code=71332&host_abi=arm64-v8a&dragon_device_type=phone"
    "&pv_player=71332&compliance_status=0&need_personal_recommend=1"
    "&player_so_load=1&is_android_pad_screen=0"
)

# ─── 设备管理 ────────────────────────────────────────────────────────────────

def load_config() -> Dict[str, str]:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if data.get("device_id") and data.get("install_id"):
                return data
        except Exception:
            pass
    return {}


def save_config(cfg: Dict[str, str]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def build_device_payload() -> Dict[str, Any]:
    """构造设备注册请求体（参照 device_register.py）"""
    import random, string
    from flurl.utils import UUID, generate_android_id, gzip_compress, md5  # noqa

    def rand_str(n):
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

    openudid = generate_android_id()
    dev_info = {
        "device": {
            "os": "Android", "device_platform": "android", "device_type": "MI 12",
            "device_brand": "Xiaomi", "os_api": "29", "os_version": "10",
            "openudid": openudid, "resolution": "1440*2392", "dpi": "560",
            "cdid": UUID(), "uuid": UUID(), "clientudid": UUID(),
            "rom": f"EMUI-{rand_str(13)}", "rom_version": rand_str(2),
        },
        "app": {
            "channel": "douyin-ls-sm-xz-and-20", "version_code": "320900",
            "version_name": "32.9.0", "manifest_version_code": "320901",
            "update_version_code": "32909900", "okhttp_version": "4.2.210.13-douyin",
        },
        "extra": {
            "userAgent": (f"com.ss.android.ugc.aweme/320901 (Linux; U; Android 10; zh_CN; MI 12; "
                          f"Build/MMB29M; Cronet/TTNetVersion:9ac8d95c 2024-11-25 QuicVersion:3f326df4 2024-11-14)"),
            "cookies": "",
        },
    }
    # 简化：直接复用 post_device_register 的关键请求
    from flurl.request_params import generate_url_params
    from flurl.ttEncryptorUtil import ttEncrypt

    itime = round(time.time() * 1000)
    post_data_obj = {
        "magic_tag": "ss_app_log",
        "header": {
            "display_name": "抖音", "update_version_code": dev_info["app"]["update_version_code"],
            "manifest_version_code": dev_info["app"]["manifest_version_code"], "aid": 8662,
            "channel": dev_info["app"]["channel"], "package": "com.ss.android.ugc.aweme",
            "app_version": dev_info["app"]["version_name"], "version_code": dev_info["app"]["version_code"],
            "sdk_version": "3.7.3-rc.53-douyin-bugfix", "sdk_target_version": 29,
            "os": dev_info["device"]["os"], "os_version": dev_info["device"]["os_version"],
            "os_api": dev_info["device"]["os_api"], "device_model": dev_info["device"]["device_type"],
            "device_brand": dev_info["device"]["device_brand"], "device_manufacturer": "Google",
            "device_category": "phone", "cpu_abi": "arm64-v8a", "release_build": f"{UUID()}",
            "density_dpi": dev_info["device"]["dpi"], "display_density": "mdpi",
            "resolution": dev_info["device"]["resolution"], "language": "zh",
            "mac": "", "timezone": 8, "access": "wifi", "not_request_sender": 0,
            "carrier": "CHINA MOBILE", "mcc_mnc": "46007",
            "rom": dev_info["device"]["rom"], "rom_version": dev_info["device"]["rom_version"],
            "sig_hash": md5(UUID()), "openudid": dev_info["device"]["openudid"],
            "clientudid": dev_info["device"]["clientudid"], "sim_serial_number": [],
            "region": "CN", "tz_name": "Asia/Shanghai", "tz_offset": 28800, "sim_region": "cn",
        },
        "_gen_time": itime,
    }
    from flurl.utils import gzip_compress
    gz = gzip_compress(json.dumps(post_data_obj).encode("utf-8"))
    post_data = ttEncrypt(gz)

    params = generate_url_params(dev_info, {})
    req_url = f"https://log.snssdk.com/service/2/device_register/?{urlencode(params)}"
    headers = {
        "content-type": "application/octet-stream;tt-data=a",
        "accept-encoding": "gzip", "user-agent": dev_info["extra"]["userAgent"],
        "host": "log.snssdk.com", "connection": "Keep-Alive",
    }
    resp = requests.post(req_url, headers=headers, data=post_data, timeout=15)
    obj = resp.json()
    return {"device_id": str(obj["device_id"]), "install_id": str(obj["install_id"])}


def urlencode(params: Dict[str, str]) -> str:
    from urllib.parse import urlencode as ue
    return ue(params)


def ensure_device() -> Dict[str, str]:
    """确保有可用设备身份，没有就注册一个新的"""
    cfg = load_config()
    if cfg:
        return cfg
    # 安卓（Chaquopy）：log.snssdk.com 注册接口会按 TLS 指纹拒绝（OpenSSL 版本差异），
    # 直接使用内置固定 device_id 绕过注册（已验证可正常解析播放）
    if os.environ.get("HONGGUO_DATA_DIR"):
        cfg = {"device_id": "REDACTED_DEVICE_ID", "install_id": "REDACTED_INSTALL_ID"}
        save_config(cfg)
        print(f"[device] 安卓内置固定 device_id={cfg['device_id']}")
        return cfg
    print("[device] 正在注册新设备 ...")
    for attempt in range(3):
        try:
            cfg = build_device_payload()
            if cfg.get("device_id") and cfg.get("install_id"):
                save_config(cfg)
                print(f"[device] 注册成功 device_id={cfg['device_id']}")
                return cfg
        except Exception as e:
            print(f"[device] 注册失败 attempt={attempt+1} err={e}")
            time.sleep(1)
    raise RuntimeError("设备注册失败，请检查网络")


def build_liushen_device() -> Dict[str, str]:
    cfg = ensure_device()
    return {
        "device_id": cfg["device_id"], "iid": cfg["install_id"], "install_id": cfg["install_id"],
        "device_brand": "Redmi", "device_model": "25053RT47C", "device_type": "25053RT47C",
        "device_manufacturer": "Xiaomi", "os_version": "16", "version_name": "7.1.3.32",
        "ua": USER_AGENT,
    }


# ─── 签名请求 ────────────────────────────────────────────────────────────────

def sign_json_request(url: str, body_obj: Dict[str, Any]) -> Tuple[str, Dict[str, str], bytes]:
    body_text = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))
    body_data = json.loads(body_text)
    body_bytes = body_text.encode("utf-8")
    ts = str(int(time.time() * 1000))
    base_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json; charset=utf-8,application/x-protobuf",
        "Content-Type": "application/json; charset=UTF-8",
        "x-xs-from-web": "0", "x-ss-req-ticket": ts, "x-tt-request-tag": "t=0;n=0",
        "sdk-version": "2", "passport-sdk-version": "50561", "x-vc-bdturing-sdk-version": "3.7.2.cn",
    }
    url_parts = urlparse(url)
    base_url = f"{url_parts.scheme}://{url_parts.netloc}{url_parts.path}"
    params = dict(parse_qs(url_parts.query, keep_blank_values=True))
    params = {k: v[0] for k, v in params.items()}
    sign_headers, sign_url = core_sixgod(
        surl=base_url, params=params, data=body_data,
        devices=build_liushen_device(), header=base_headers, log=False,
    )
    return sign_url, sign_headers, body_bytes


# ─── 搜索 / 详情（官网 H5）────────────────────────────────────────────────────

def fetch_text(url: str, timeout: int = 20) -> str:
    resp = requests.get(url, headers={"User-Agent": WEB_UA,
                                      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                      "Accept-Language": "zh-CN,zh;q=0.9",
                                      "Accept-Encoding": "identity",
                                      "Referer": "https://hongguoduanju.com/"}, timeout=timeout)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


# ─── 官方分类 API 搜索（核心）────────────────────────────────────────────────

# 分类 API 缓存（避免每次全量抓取）
_INDEX_CACHE: Dict[str, Any] = {"data": None, "time": 0}
INDEX_TTL = 600  # 内存缓存 10 分钟；过期后优先读落盘缓存（INDEX_DISK_TTL），都不新鲜才重抓
INDEX_PAGES = 21  # 每页24部，21页覆盖约500部全量
# v51: 索引落盘缓存——全量索引要抓 126 页（2 tab × 3 排序 × 21 页），原先只有内存缓存，
# 重启或 TTL 过期就在请求线程里同步重抓几十秒。落盘后重启秒加载，6 小时内不重抓。
INDEX_DISK_PATH = DATA_DIR / "cache" / "series_index.json"
INDEX_DISK_TTL = 6 * 3600  # 落盘缓存 6 小时

CATEGORY_API = "https://hongguoduanju.com/api/category/page"
# tab: 1=短剧(VIDEO), 2=漫剧(COMIC)
TAB_VIDEO = "1"
TAB_COMIC = "2"
TAB_ANIME = "anime"  # 沙雕/搞笑动画（suggestion 关键词聚合，无官方分类）
# 分类 API 的 sort_type 排序维度：1=综合, 2=热度, 3=更新（4+ 都 fallback 到同一批）。
# 不同 sort_type 返回的剧集不同，遍历去重可把短剧索引从 ~500 部扩到 ~800+ 部。
INDEX_SORTS = [1, 2, 3]
# 沙雕动画关键词（suggestion 全站搜索聚合，创作者名 + 动画类型词）
ANIME_KEYWORDS = [
    "虾仁", "沙雕", "炫语", "鸭鸭", "小鹿", "奶龙", "搞笑动画", "沙雕动画",
    "爆笑", "幽默", "欢乐", "搞笑一家人", "节奏盒子", "熊猫人", "萌系",
    "搞笑AI", "AI动画", "动态漫画", "猪猪", "羊驼", "柴犬", "柯基", "狐狸",
    "企鹅", "老虎", "狮子", "仓鼠", "龙猫", "兕子", "小苦瓜", "稚语",
    "爆笑虫子", "汪汪", "开心超人", "猪猪侠", "搞笑短剧", "动画短片",
    "欢乐动画", "沙雕恋爱", "沙雕修仙", "沙雕日常", "搞笑日常", "萌宝动画",
    "动物动画", "Q版动画",
]
# 标题/标签特征过滤：命中任一才算沙雕/搞笑动画（过滤掉搜索词误匹配的普通短剧）
ANIME_FILTER_RE = (
    r"(虾仁|沙雕|炫语|鸭鸭|小鹿|奶龙|稚语|兕子|小苦瓜|节奏盒子|熊猫人|"
    r"搞笑|爆笑|暴笑|幽默|欢乐|动画|AI|萌|龙娘|奶包|猪猪|羊驼|柴犬|"
    r"柯基|狐狸|企鹅|汪汪|开心超人)"
)


# 手工录入的剧集索引（quickapp / 字节内部 sid 等 web search 搜不到的剧）
# 文件格式: JSON 数组,每项 { series_id, title, cover, episode_text, tags, desc, episode_cnt, vid_list, type }
MANUAL_SERIES_PATH = DATA_DIR / "cache" / "manual_series.json"
_MANUAL_CACHE: Dict[str, Any] = {"data": None, "time": 0}
MANUAL_TTL = 300  # 5 分钟内存缓存


def _load_manual_series() -> List[Dict[str, Any]]:
    """读取手工录入的剧集索引(优先于 web suggestion,搜索字节火山内部 sid 用)"""
    now = time.time()
    if _MANUAL_CACHE["data"] is not None and (now - _MANUAL_CACHE["time"]) < MANUAL_TTL:
        return _MANUAL_CACHE["data"]
    try:
        if MANUAL_SERIES_PATH.exists():
            data = json.loads(MANUAL_SERIES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _MANUAL_CACHE["data"] = data
                _MANUAL_CACHE["time"] = now
                return data
    except Exception as e:
        print(f"[manual_series] load error: {e}", flush=True)
    _MANUAL_CACHE["data"] = []
    _MANUAL_CACHE["time"] = now
    return []


def add_manual_series(item: Dict[str, Any]) -> int:
    """POST /api/manual_series 调用:新增一条手工索引

    item 必须包含: series_id, title, type(短剧/漫剧)
    可选: cover, episode_text, tags, desc, episode_cnt, vid_list, source_url(quickapp 链接)
    返回当前总数
    """
    if not item.get("series_id") or not item.get("title"):
        raise ValueError("series_id 和 title 必填")
    item.setdefault("type", "漫剧")
    item.setdefault("cover", "")
    item.setdefault("episode_text", f"全{item.get('episode_cnt', '?')}集")
    item.setdefault("tags", "")
    item.setdefault("desc", "")
    item.setdefault("episode_cnt", 0)
    item.setdefault("vid_list", [])
    item.setdefault("source", "manual")

    items = _load_manual_series()
    # 覆盖已存在的 series_id
    sid = str(item["series_id"])
    items = [it for it in items if str(it.get("series_id")) != sid]
    items.insert(0, item)
    MANUAL_SERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANUAL_SERIES_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    # 失效缓存
    _MANUAL_CACHE["data"] = None
    _MANUAL_CACHE["time"] = 0
    print(f"[manual_series] added sid={sid} title={item['title']} (total={len(items)})", flush=True)
    return len(items)


def fetch_category_page(page_num: int, tab: str = TAB_VIDEO, sort_type: int = 1) -> List[Dict[str, Any]]:
    """调用官方分类 API 抓一页剧集数据"""
    try:
        resp = requests.get(
            CATEGORY_API,
            params={"page_num": page_num, "tab": tab, "sort_type": sort_type},
            headers={"User-Agent": WEB_UA, "Accept": "application/json",
                     "Referer": f"{SITE_BASE}/category",
                     # 不要 gzip：上游偶发返回截断/错乱的 gzip body 会让 urllib3 自动解压
                     # 抛 zlib "Error -3 incorrect header check"，影响首次加载推荐 + 搜索
                     "Accept-Encoding": "identity"},
            timeout=15,
        )
        data = resp.json()
        if data.get("isSuccess"):
            return data.get("recommendList") or []
    except Exception as e:
        print(f"[search][api] tab={tab} page={page_num} error={e}")
    return []


def _load_index_disk(now: float) -> Optional[List[Dict[str, Any]]]:
    """v51: 从落盘缓存读剧集索引（新鲜才返回）"""
    try:
        if INDEX_DISK_PATH.exists():
            d = json.loads(INDEX_DISK_PATH.read_text(encoding="utf-8"))
            if (isinstance(d, dict) and d.get("time")
                    and now - float(d["time"]) < INDEX_DISK_TTL
                    and isinstance(d.get("items"), list) and d["items"]):
                print(f"[search] 索引从落盘缓存加载: {len(d['items'])} 部", flush=True)
                return d["items"]
    except Exception as e:
        print(f"[search] 索引落盘缓存读取失败: {e}")
    return None


def _save_index_disk(items: List[Dict[str, Any]], ts: float) -> None:
    try:
        INDEX_DISK_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(INDEX_DISK_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"time": ts, "items": items}, f, ensure_ascii=False)
        os.replace(tmp, INDEX_DISK_PATH)
    except Exception as e:
        print(f"[search] 索引落盘失败: {e}")


def build_series_index(force: bool = False) -> List[Dict[str, Any]]:
    """全量抓取分类 API（短剧+漫剧×多种排序），构建剧集索引（内存 + 落盘双层缓存）

    tab 过滤由调用方（search_series）完成，索引始终包含全量内容。
    遍历 INDEX_SORTS 多种排序维度去重，尽量覆盖分类页能拿到的全部剧集。
    v51: 6 条抓取链（2 tab × 3 排序）并发执行（链内仍串行 + 0.05s 限速），
    首次构建从串行 ~126 次请求降到约 1/6 耗时；结果落盘，重启不用重抓。
    """
    now = time.time()
    if not force and _INDEX_CACHE["data"] and (now - _INDEX_CACHE["time"]) < INDEX_TTL:
        return _INDEX_CACHE["data"]

    if not force:
        disk = _load_index_disk(now)
        if disk is not None:
            _INDEX_CACHE["data"] = disk
            _INDEX_CACHE["time"] = now
            return disk

    chains = [(t, label, sort)
              for t, label in ((TAB_VIDEO, "短剧"), (TAB_COMIC, "漫剧"))
              for sort in INDEX_SORTS]

    def _chain(chain) -> List[tuple]:
        t, label, sort = chain
        pages: List[tuple] = []
        for page_num in range(1, INDEX_PAGES + 1):
            page_items = fetch_category_page(page_num, t, sort)
            if not page_items:
                break
            pages.append((label, page_items))
            time.sleep(0.05)
        return pages

    items: List[Dict[str, Any]] = []
    seen: set = set()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(6, len(chains))) as pool:
        # pool.map 按提交顺序返回，条目顺序与旧版串行实现一致
        for pages in pool.map(_chain, chains):
            for label, page_items in pages:
                for it in page_items:
                    sid = str(it.get("series_id") or "")
                    if not sid or sid in seen:
                        continue
                    seen.add(sid)
                    items.append({
                        "series_id": sid,
                        "title": it.get("series_name") or "",
                        "cover": it.get("series_cover") or "",
                        "episode_text": it.get("episode_right_text") or f"全{it.get('episode_cnt', '')}集",
                        "tags": " / ".join(it.get("tags") or []) or "",
                        "desc": it.get("series_intro") or "",
                        "episode_cnt": it.get("episode_cnt") or 0,
                        "vid_list": it.get("vid_list") or [],
                        "type": label,  # 短剧/漫剧
                    })

    _INDEX_CACHE["data"] = items
    _INDEX_CACHE["time"] = now
    _save_index_disk(items, now)
    print(f"[search] 索引构建完成: {len(items)} 部 ({', '.join(sorted(set(it['type'] for it in items)))})")
    return items


# 沙雕/搞笑动画索引（suggestion 关键词聚合，无官方分类接口）
_ANIME_CACHE: Dict[str, Any] = {"data": [], "time": 0}
ANIME_TTL = 1800  # 30 分钟


def build_anime_index(force: bool = False) -> List[Dict[str, Any]]:
    """沙雕/搞笑动画聚合：suggestion 关键词正向搜索 + 剧名反向搜索，去重缓存

    返回条目结构同 search_suggestion（series_id/title/cover/tags/vid_list...），
    type 标为"沙雕动画"，与"漫剧"（小说漫改）区分开。
    用 ANIME_FILTER_RE 过滤标题/标签，避免搜索词误匹配到普通短剧。
    两轮：
      1) 正向：ANIME_KEYWORDS（创作者名+类型词）逐词搜索
      2) 反向：用已聚合剧名当关键词再搜（发现同系列/续集/同作者）
    """
    now = time.time()
    if not force and _ANIME_CACHE["data"] and (now - _ANIME_CACHE["time"]) < ANIME_TTL:
        return _ANIME_CACHE["data"]

    import re as _re
    _flt = _re.compile(ANIME_FILTER_RE)
    items: List[Dict[str, Any]] = []
    seen: set = set()

    def _absorb(hits):
        for it in hits:
            sid = it.get("series_id") or ""
            if not sid or sid in seen:
                continue
            hay = f"{it.get('title') or ''} {it.get('tags') or ''}"
            if not _flt.search(hay):
                continue
            seen.add(sid)
            it["type"] = "沙雕动画"
            items.append(it)

    # 1) 正向关键词
    for kw in ANIME_KEYWORDS:
        _absorb(search_suggestion(kw))
        time.sleep(0.15)

    # 2) 反向：用已聚合剧名再搜（每部取前 12 个作为种子）
    seed_titles = [it.get("title") for it in items if it.get("title")][:30]
    for title in seed_titles:
        _absorb(search_suggestion(title[:12]))
        time.sleep(0.15)

    _ANIME_CACHE["data"] = items
    _ANIME_CACHE["time"] = now
    print(f"[search] 沙雕动画索引构建完成: {len(items)} 部")
    return items


def search_suggestion(keyword: str) -> List[Dict[str, Any]]:
    """官方 suggestion 实时搜索（可搜到索引外的任意剧）

    接口: /incent_resource/suggestion?web_id=&query=&count=&app_id=8662
    返回的 suggest_list 中 video_data 含 series_id / vid_list 等
    """
    try:
        web_id = str(int(time.time() * 1000))[-16:]  # 简单 web_id
        resp = requests.get(
            f"{SITE_BASE}/incent_resource/suggestion",
            params={"web_id": web_id, "query": keyword, "count": 10, "app_id": "8662"},
            headers={"User-Agent": WEB_UA, "Accept": "application/json",
                     "Referer": f"{SITE_BASE}/search",
                     "Accept-Encoding": "identity"},
            timeout=15,
        )
        data = resp.json()
    except Exception as e:
        print(f"[search][suggestion] error={e}")
        return []

    items: List[Dict[str, Any]] = []
    seen: set = set()
    for sug in data.get("suggest_list") or []:
        vd = sug.get("video_data") or {}
        sid = str(vd.get("series_id") or "")
        name = sug.get("name") or vd.get("series_name") or ""
        # 过滤合集/无有效 series_id 的条目
        if not sid or not name or sid in seen:
            continue
        if not sid.isdigit() or len(sid) < 15:
            continue
        seen.add(sid)
        # 判断类型：tags 含"小说漫改"或 vid 前缀特征 → 漫剧
        tags = vd.get("tags") or vd.get("category_list") and [c.get("name", "") for c in vd.get("category_list", [])] or []
        tag_str = " / ".join(tags) if isinstance(tags, list) else str(tags)
        vids = vd.get("vid_list") or []
        is_manju = "漫改" in tag_str or "动画" in tag_str or (vids and str(vids[0]).startswith("76"))
        items.append({
            "series_id": sid,
            "title": name,
            "cover": vd.get("series_cover") or "",
            "episode_text": vd.get("episode_right_text") or f"全{vd.get('episode_cnt', '')}集",
            "tags": tag_str,
            "desc": vd.get("series_intro") or "",
            "episode_cnt": vd.get("episode_cnt") or 0,
            "vid_list": vids,
            "type": "漫剧" if is_manju else "短剧",
        })
    return items


def _all_recommend_index() -> List[Dict[str, Any]]:
    """推荐/全部：分类索引（短剧+漫剧）∪ 沙雕动画聚合，去重"""
    base = build_series_index()
    sids = {it.get("series_id") for it in base}
    extra = [it for it in build_anime_index() if it.get("series_id") not in sids]
    return base + extra


def search_series(keyword: str, page: int = 1, tab: str = "") -> Dict[str, Any]:
    """搜索：官方 suggestion 实时搜索优先 + 本地索引兜底

    tab: ""=推荐（全量聚合，含沙雕动画）, "1"=短剧, "2"=漫剧
    """
    keyword = (keyword or "").strip()

    # 0) 优先：手工录入的剧集（quickapp / 字节内部 sid 等 web search 搜不到的）
    # 从 /data/cache/manual_series.json 加载,关键词命中即返回
    manual = _load_manual_series()
    if manual and keyword:
        kw_lower = keyword.lower()
        manual_hits = []
        for it in manual:
            if tab == TAB_COMIC and it.get("type") != "漫剧":
                continue
            if tab == TAB_VIDEO and it.get("type") != "短剧":
                continue
            title = (it.get("title") or "").lower()
            tags = (it.get("tags") or "").lower()
            if kw_lower in title or kw_lower in tags:
                manual_hits.append(it)
        if manual_hits:
            per_page = 24
            start = (page - 1) * per_page
            paged = manual_hits[start:start + per_page]
            return {"items": paged, "total": len(manual_hits), "page": page,
                    "source": "manual", "message": f"手工收录 {len(manual_hits)} 部（web 搜索搜不到的内容）"}

    if keyword:
        # 1) 先走官方 suggestion 实时搜索（能搜到索引外的任意剧）
        api_hits = search_suggestion(keyword)
        if api_hits:
            if tab == TAB_VIDEO:
                api_hits = [it for it in api_hits if it.get("type") == "短剧"]
            elif tab == TAB_COMIC:
                api_hits = [it for it in api_hits if it.get("type") == "漫剧"]
            if api_hits:
                per_page = 24
                start = (page - 1) * per_page
                paged = api_hits[start:start + per_page]
                return {"items": paged, "total": len(api_hits), "page": page,
                        "source": "official", "message": f"官方搜索到 {len(api_hits)} 部"}

    # 2) 兜底：本地全量索引 + 智能匹配
    if tab == TAB_VIDEO:
        index = [it for it in build_series_index() if it.get("type") == "短剧"]
    elif tab == TAB_COMIC:
        # 漫剧 = 官方小说漫改(75部) + 沙雕动画(~90部)，同属"漫"类内容
        index = [it for it in build_series_index() if it.get("type") == "漫剧"]
        sids = {it.get("series_id") for it in index}
        index = index + [it for it in build_anime_index() if it.get("series_id") not in sids]
    else:
        # 推荐 = 短剧 + 漫剧 + 沙雕动画 全量聚合
        index = _all_recommend_index()

    if not keyword:
        matched = index
    else:
        kw_lower = keyword.lower()
        scored: List[tuple] = []
        for it in index:
            title = it.get("title") or ""
            desc = it.get("desc") or ""
            tags = it.get("tags") or ""
            haystack = f"{title} {desc} {tags}"
            if kw_lower in haystack.lower():
                score = 0
                if kw_lower in title.lower():
                    score += 100  # 标题命中权重最高
                if kw_lower in tags.lower():
                    score += 30
                if kw_lower in desc.lower():
                    score += 10
                # 标题前缀/全字匹配加分
                if title.lower().startswith(kw_lower):
                    score += 50
                if title.lower() == kw_lower:
                    score += 80
                scored.append((score, it))
        scored.sort(key=lambda x: -x[0])
        matched = [it for _, it in scored]

    # 分页
    per_page = 24
    start = (page - 1) * per_page
    paged = matched[start:start + per_page]
    if keyword and not matched:
        hot = search_series("", 1, tab)
        return {"items": hot["items"], "total": hot["total"], "page": page,
                "fallback": True, "message": f"未找到「{keyword}」，已展示{('漫剧' if tab == TAB_COMIC else '短剧' if tab == TAB_VIDEO else '')}热门"}

    # 无关键词浏览模式：本地索引耗尽后，从 sitemap 全量目录增量抓取补充
    # （实现"边下拉边刷新"：滚动到索引末尾 → 同步抓取详情页 → 返回新剧）
    if not keyword:
        if start < len(matched):
            total = len(matched) + _browse_remaining()
            return {"items": matched[start:start + per_page], "total": total, "page": page,
                    "source": "index"}
        else:
            # 全量目录浏览：从已推送游标继续增量返回（缓存抓多少返回多少，
            # 前端持续重试滚动，体验"边下拉边刷新"）
            # v48 修复：之前用全局共享游标 _BROWSE_SENT —— 平板先下拉把它推到底，
            # PC 再下拉时 extras 为空 → PC 端只能看到几百部/漫剧几十部。
            # 改成用前端传的 page 参数计算 offset，各客户端互不影响。
            global _BROWSE_ITEMS
            browse_start = (page - 1) * per_page - len(matched)
            if browse_start < 0:
                browse_start = 0
            browse_ensure(browse_start + per_page, max_fetch=24, max_wait=15.0)
            extras = _BROWSE_ITEMS[browse_start:browse_start + per_page]
            total = len(matched) + _browse_remaining()
            return {"items": extras, "total": total, "page": page,
                    "source": "browse", "message": "全量目录持续抓取中…"}

    return {"items": paged, "total": len(matched), "page": page}


# ─── 全量浏览：sitemap 增量目录（边下拉边刷新）─────────────────────────

_BROWSE_LOCK = threading.Lock()
_SITEMAP_IDS: List[str] = []
_SITEMAP_CURSOR = 0             # 下一个待抓的 sitemap 位置
_BROWSE_ITEMS: List[Dict[str, Any]] = []
_BROWSE_INDEX: Dict[str, Any] = {}
_BROWSE_SKIP: set = set()       # 已尝试但无效的 ID，避免重复抓
_BROWSE_SENT = 0                # 已推送给前端的 browse 条目数（增量游标）


def _browse_dir() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "cache")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _browse_load_sitemap(force: bool = False):
    """加载全量 series_id 队列：sitemap.xml → 23 个分片 → 去重 ID（缓存到 cache/sitemap_ids.json）"""
    global _SITEMAP_IDS
    if _SITEMAP_IDS and not force:
        return
    path = os.path.join(_browse_dir(), "sitemap_ids.json")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                _SITEMAP_IDS = json.load(f)
            if _SITEMAP_IDS:
                print(f"[browse] 从缓存加载全量目录: {len(_SITEMAP_IDS)} ID")
                return
    except Exception:
        pass
    ids: set = set()
    try:
        r = requests.get(f"{SITE_BASE}/sitemap.xml",
                         headers={"User-Agent": WEB_UA, "Accept-Encoding": "identity"}, timeout=25)
        shards = re.findall(r"<loc>([^<]+/index\d+\.xml)</loc>", r.text)
        print(f"[browse] sitemap 分片: {len(shards)}")
        for sh in shards:
            try:
                rr = requests.get(sh,
                                  headers={"User-Agent": WEB_UA, "Accept-Encoding": "identity"}, timeout=25)
                ids |= set(re.findall(r"detail\?series_id=(\d+)", rr.text))
            except Exception:
                continue
    except Exception as e:
        print(f"[browse] sitemap 加载失败: {e}")
    _SITEMAP_IDS = list(ids)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_SITEMAP_IDS, f)
    except Exception:
        pass
    print(f"[browse] sitemap 全量目录: {len(_SITEMAP_IDS)} ID")


def _browse_load_cache():
    global _BROWSE_ITEMS, _BROWSE_INDEX
    path = os.path.join(_browse_dir(), "browse.json")
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                _BROWSE_ITEMS = json.load(f)
            _BROWSE_INDEX = {it["series_id"]: it for it in _BROWSE_ITEMS if it.get("series_id")}
    except Exception:
        pass


def _browse_save_cache():
    path = os.path.join(_browse_dir(), "browse.json")
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_BROWSE_ITEMS, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _browse_fetch_one(sid: str):
    """轻量抓取单个剧集元信息（标题/封面/集数/简介，不解析 vid_list——点击播放时再完整解析）"""
    if sid in _BROWSE_INDEX or sid in _BROWSE_SKIP:
        return None
    try:
        url = f"{SITE_BASE}/detail?series_id={sid}"
        html = fetch_text(url, timeout=10)
        meta = extract_series_meta(html, sid)
        title = meta.get("title") or ""
        if not title:
            _BROWSE_SKIP.add(sid)
            return None
        return {
            "series_id": sid,
            "title": title,
            "cover": meta.get("cover") or "",
            "episode_text": f"全{meta.get('episode_cnt', '')}集" if meta.get("episode_cnt") else "",
            "tags": "",
            "desc": (meta.get("desc") or "")[:200],
            "episode_cnt": meta.get("episode_cnt") or 0,
            "vid_list": [],
            "type": "",
        }
    except Exception:
        _BROWSE_SKIP.add(sid)
        return None


def browse_ensure(count: int, max_fetch: int = 24, max_wait: float = 15.0) -> int:
    """确保 browse 缓存至少有 count 条（滚动驱动：并发抓取，单次最多 max_fetch 个、最长 max_wait 秒）
    v48: 支持多轮抓取——count 可能很大（客户端翻页 offset），一轮抓 24 个不够，
    循环抓直到达到 count / sitemap 末尾 / max_wait 超时。
    """
    global _SITEMAP_CURSOR
    _browse_load_sitemap()
    if not _SITEMAP_IDS:
        return len(_BROWSE_ITEMS)
    deadline = time.time() + max_wait
    while True:
        with _BROWSE_LOCK:
            if len(_BROWSE_ITEMS) >= count:
                return len(_BROWSE_ITEMS)
            if _SITEMAP_CURSOR >= len(_SITEMAP_IDS):
                return len(_BROWSE_ITEMS)
            if time.time() >= deadline:
                return len(_BROWSE_ITEMS)
            # 收集本轮要尝试的 ID（最多 max_fetch 个）
            batch: List[str] = []
            while len(batch) < max_fetch and _SITEMAP_CURSOR < len(_SITEMAP_IDS):
                sid = _SITEMAP_IDS[_SITEMAP_CURSOR]
                _SITEMAP_CURSOR += 1
                batch.append(sid)
        if not batch:
            return len(_BROWSE_ITEMS)
        # 并发抓取（4 线程），显著提升滚动刷新速度
        results: List = []
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(_browse_fetch_one, sid) for sid in batch]
            for f in futs:
                try:
                    results.append(f.result(timeout=12))
                except Exception:
                    pass
        added = 0
        for it in results:
            if it and it.get("series_id") not in _BROWSE_INDEX:
                _BROWSE_ITEMS.append(it)
                _BROWSE_INDEX[it["series_id"]] = it
                added += 1
        if len(_BROWSE_ITEMS) % 30 < len(results):
            _browse_save_cache()
        if added == 0:
            return len(_BROWSE_ITEMS)


def browse_series(offset: int, limit: int) -> List[Dict[str, Any]]:
    """返回 browse 缓存分段；缓存不足时同步补抓"""
    _browse_load_sitemap()
    if _SITEMAP_IDS:
        browse_ensure(offset + limit)
    return _BROWSE_ITEMS[offset:offset + limit]


def _browse_remaining() -> int:
    """sitemap 全量目录剩余可抓数量（用于前端 total 展示）
    v48: 不能用 _SITEMAP_CURSOR —— 多客户端共享后端，平板先滚动会把 sitemap
    游标消费完 → _browse_remaining() 变 0 → PC/漫剧 tab 的 total 变成几百/几十
    → 前端 listEnded 误判"已全部加载"。改用 sitemap 总数 - 已抓缓存数（只增不减）。
    """
    _browse_load_sitemap()
    if not _SITEMAP_IDS:
        return 0
    return max(0, len(_SITEMAP_IDS) - len(_BROWSE_ITEMS))


# 启动时恢复浏览缓存 + 后台线程持续预抓全量目录
def _browse_background_worker():
    """后台持续抓取 browse 缓存（并发预抓：用户滚动本地索引期间，
    全量目录已在后台积累，滚到 browse 区时缓存基本就绪）"""
    from concurrent.futures import ThreadPoolExecutor
    while True:
        try:
            _browse_load_sitemap()
            if not _SITEMAP_IDS:
                time.sleep(10)
                continue
            with _BROWSE_LOCK:
                if _SITEMAP_CURSOR >= len(_SITEMAP_IDS):
                    time.sleep(5)
                    continue
                batch = []
                while len(batch) < 8 and _SITEMAP_CURSOR < len(_SITEMAP_IDS):
                    batch.append(_SITEMAP_IDS[_SITEMAP_CURSOR])
                    _SITEMAP_CURSOR += 1
            results = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                futs = [pool.submit(_browse_fetch_one, sid) for sid in batch]
                for f in futs:
                    try:
                        results.append(f.result(timeout=12))
                    except Exception:
                        pass
            for it in results:
                if it and it.get("series_id") not in _BROWSE_INDEX:
                    with _BROWSE_LOCK:
                        if it["series_id"] not in _BROWSE_INDEX:
                            _BROWSE_ITEMS.append(it)
                            _BROWSE_INDEX[it["series_id"]] = it
                            if len(_BROWSE_ITEMS) % 30 < 8:
                                _browse_save_cache()
        except Exception:
            pass
        time.sleep(0.05)


_browse_load_cache()
threading.Thread(target=_browse_background_worker, daemon=True).start()


def extract_vid_list(html_text: str, series_id: str = "") -> List[str]:
    """从详情页提取 vid_list（每集 video_id）

    详情页包含大量推荐位/相关剧的 vid_list（40+ 个），必须精确关联到当前剧。
    策略：遍历当前 series_id 每次出现的位置，取其后的第一个 vid_list，
    取最后一次出现（主剧数据在页面尾部）；兜底取页面最后一个 vid_list。
    """
    if series_id:
        best: List[str] = []
        for m in re.finditer(r'"series_id":\s*"?\d*' + re.escape(series_id) + r'"?', html_text):
            after = html_text[m.end():]
            vm = re.search(r'"vid_list":\[([^\]]+)\]', after)
            if vm:
                best = re.findall(r'"(\d{10,25})"', vm.group(1))
        if best:
            return best
    # 兜底：取最后一个 vid_list（主剧数据通常位于页面尾部）
    matches = list(re.finditer(r'"vid_list":\[[^\]]+\]', html_text))
    if matches:
        return re.findall(r'"(\d{10,25})"', matches[-1].group(0))
    return []


def extract_series_meta(html_text: str, series_id: str = "") -> Dict[str, Any]:
    """从详情页提取剧集元信息（必须按 series_id 关联，避免抓到推荐位）"""
    meta = {}
    # 1) 标题/简介：用 meta description（最稳，描述里的《》书名号就是本剧）
    m = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', html_text)
    if m:
        meta["desc"] = m.group(1)
        tm = re.search(r'《([^》]+)》', meta["desc"])
        if tm:
            meta["title"] = tm.group(1)
    # 兜底标题：页面任何位置的《》书名号
    if "title" not in meta:
        m = re.search(r'《([^》]+)》', html_text)
        if m:
            meta["title"] = m.group(1)
    # 2) 封面（高可靠路径）：当前 series_id 之后第一个 preload-as-image 的 href
    if series_id:
        sm = re.search(r'series_id="?' + re.escape(series_id) + r'"?', html_text)
        if sm:
            after = html_text[sm.end():]
            link = re.search(r'<link[^>]+rel="preload"[^>]+as="image"[^>]+href="([^"]+)"', after)
            if link:
                meta["cover"] = link.group(1)
    # 3) 封面兜底：og:image
    if "cover" not in meta:
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html_text)
        if m:
            meta["cover"] = m.group(1)
    # 4) 兜底：最后一个 series_cover（主剧数据通常在页面尾部，比第一个稳）
    if "cover" not in meta:
        covers = re.findall(r'"series_cover":"([^"]+)"', html_text)
        if covers:
            meta["cover"] = covers[-1].replace("\\u002F", "/").replace("\\/", "/")
    # 5) episode_cnt 按 sid 关联
    if series_id:
        sm = re.search(r'series_id="?' + re.escape(series_id) + r'"?', html_text)
        if sm:
            cn = re.search(r'"episode_cnt":(\d+)', html_text[sm.end():])
            if cn:
                meta["episode_cnt"] = int(cn.group(1))
    if "episode_cnt" not in meta:
        m = re.search(r'"episode_cnt":(\d+)', html_text)
        if m:
            meta["episode_cnt"] = int(m.group(1))
    meta.setdefault("title", "")
    meta.setdefault("desc", "")
    meta.setdefault("cover", "")
    return meta


def get_series_detail(series_id: str) -> Dict[str, Any]:
    """获取剧集详情：元信息 + 每集 vid（传入 series_id 给提取函数避免抓到推荐位）

    v46: 优先查 manual 索引(quickapp / 字节内部 sid),命中即返回 manual 数据 + vid_list,
    否则走 web `/detail?series_id=X` 兜底。
    """
    # 0) 优先:manual 索引命中
    manual = _load_manual_series()
    sid = str(series_id)
    for it in manual:
        if str(it.get("series_id")) == sid:
            meta = {
                "title": it.get("title", ""),
                "cover": it.get("cover", ""),
                "episode_text": it.get("episode_text", ""),
                "tags": it.get("tags", ""),
                "desc": it.get("desc", ""),
                "episode_cnt": it.get("episode_cnt", 0),
                "source": "manual",
            }
            vids = list(it.get("vid_list") or [])
            if not vids and it.get("series_id"):
                vids = [it["series_id"]]
            meta["series_id"] = sid
            meta["episodes"] = [{"no": i + 1, "vid": vid} for i, vid in enumerate(vids)]
            return meta

    # 1) 兜底:web 端详情(对字节火山内部 sid 不适用,但兼容其他场景)
    url = f"{SITE_BASE}/detail?series_id={series_id}"
    html = fetch_text(url)
    meta = extract_series_meta(html, series_id)
    vids = extract_vid_list(html, series_id)
    meta["series_id"] = series_id
    meta["episodes"] = [{"no": i + 1, "vid": vid} for i, vid in enumerate(vids)]
    return meta


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


# ─── 视频解析 ────────────────────────────────────────────────────────────────

def b64_decode_padded(s: str) -> bytes:
    s = s.strip()
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    try:
        return base64.b64decode(s)
    except Exception:
        return base64.urlsafe_b64decode(s)


def decrypt_spade_url(b64_str: str, key_seed: bytes) -> str:
    raw = b64_decode_padded(b64_str)
    if len(raw) < 5 or raw[0] != 0xA8 or raw[2] != 0x01 or raw[3] != 0x00:
        return ""
    cipher_data = raw[4:]
    cipher_len = (len(cipher_data) // 16) * 16
    cipher_data = cipher_data[:cipher_len]
    constants = bytes([
        0x4D, 0xD4, 0xC2, 0xE6, 0xB8, 0x31, 0x62, 0x09, 0x0E, 0x52, 0xB3, 0xC7, 0xA6, 0x73, 0x3B, 0xA4,
        0x1C, 0xB2, 0x46, 0x2B, 0x82, 0x9A, 0xB5, 0x8A, 0x19, 0x6B, 0x39, 0xDB, 0x57, 0x17, 0x75, 0x24,
        0xF4, 0x9B, 0xAF, 0x7F, 0x08, 0xE8, 0xD6, 0x8D, 0x26, 0xA7, 0x2E, 0x37, 0xC1, 0xA9, 0x5A, 0x2F,
        0x1F, 0x05, 0xA5, 0x18, 0x92, 0xAE, 0xF2, 0x94, 0x97, 0x32, 0xB6, 0x2A, 0x38, 0xAA, 0xDD, 0x58,
    ])
    try:
        from Crypto.Cipher import AES  # PC 版（pycryptodome）
    except ImportError:
        # 安卓版：cryptography（有 Android wheel）
        from cryptography.hazmat.primitives.ciphers import Cipher as _C
        from cryptography.hazmat.primitives.ciphers import algorithms as _A
        from cryptography.hazmat.primitives.ciphers import modes as _M
        class _AesCbc:
            def __init__(self, key, iv):
                self._c = _C(_A.AES(key), _M.CBC(iv)).decryptor()
            def decrypt(self, data):
                return self._c.update(data)
        AES = type("AES", (), {
            "MODE_CBC": "CBC",
            "new": staticmethod(lambda key, mode, iv: _AesCbc(key, iv)),
        })
    h1 = hashlib.sha512(key_seed).digest()
    h2 = hashlib.sha512(h1 + constants).digest()
    aes_key = h2[:16]
    iv = h2[16:32]
    cipher = AES.new(aes_key, AES.MODE_CBC, iv=iv)
    plaintext = cipher.decrypt(cipher_data)
    if plaintext:
        pad = plaintext[-1]
        if 1 <= pad <= 16 and pad <= len(plaintext):
            plaintext = plaintext[:-pad]
    return plaintext.rstrip(b"\x00").decode("utf-8", errors="replace")


def derive_content_key(spade_b64: str) -> bytes:
    s = spade_b64.strip()
    m = 4 - len(s) % 4
    if m != 4:
        s += "=" * m
    raw = base64.b64decode(s)
    if len(raw) < 3:
        raise ValueError("spade_a too short")
    v6 = raw[0] ^ raw[1] ^ raw[2]
    v8 = len(raw) - v6 + 47
    if v8 <= 0 or v8 > len(raw) * 2:
        raise ValueError("v8 out of range")
    if 1 + v8 > len(raw):
        v8 = len(raw) - 1
    if v8 < 33:
        raise ValueError("v8 too small")
    v13 = bytearray(raw[1:1 + v8])
    vA, vB = 85, 246
    for i in range(v8):
        popcnt = bin(i).count("1")
        if i & 1:
            v24 = vA
            vA = v13[i]
        else:
            v24 = vB
            vB = v13[i]
        v25 = v24 ^ v13[i]
        v26 = -21 - popcnt
        v13[i] = (v26 + v25) & 0xFF
    hex_str = bytes(v13[1:33]).decode("ascii")
    return binascii.unhexlify(hex_str)


def resolve_video_url(vid: str, quality: str = "") -> Dict[str, Any]:
    """完整解析: vid -> 真实视频URL + content_key

    quality: 目标清晰度 key（如 "1080p"/"720p"/"540p"），空 = 最高档
    返回含 qualities 全档位列表（供前端画质选择）
    """
    cfg = ensure_device()
    target_url = VIDEO_MODEL_URL_TEMPLATE.format(
        install_id=quote(cfg["install_id"], safe=""), device_id=quote(cfg["device_id"], safe=""))
    post_payload = {
        "biz_param": {
            "detail_page_version": 0, "device_level": 3, "disable_digg_stat": False,
            "need_all_video_definition": True, "need_mp4_align": False,
            "use_os_player": False, "use_server_dns": False, "video_platform": 1024,
        },
        "mixed_video_id_map": {"1004": [vid]},
    }
    signed_url, headers, body = sign_json_request(target_url, post_payload)
    resp = requests.post(signed_url, headers=headers, data=body, timeout=25)
    data = resp.json()
    entry = (data.get("data") or {}).get(vid)
    if not entry:
        raise RuntimeError(f"视频解析失败: {json.dumps(data, ensure_ascii=False)[:200]}")
    vm_raw = entry.get("video_model")
    vm = json.loads(vm_raw) if isinstance(vm_raw, str) else vm_raw

    fb = vm.get("fallback_api")
    fb_url = fb if isinstance(fb, str) else (fb.get("fallback_api") if isinstance(fb, dict) else "")

    resp2 = requests.get(fb_url, headers={"User-Agent": USER_AGENT}, timeout=25)
    d2 = resp2.json()
    video_data = d2.get("video_info", {}).get("data", {})
    if not video_data:
        raise RuntimeError("fallback_api 返回异常")
    key_seed_raw = b64_decode_padded(video_data.get("key_seed", ""))
    vl = video_data.get("video_list", {})
    if not vl:
        raise RuntimeError("无可用视频流")

    # 所有档位按清晰度降序
    def _height(kv):
        return kv[1].get("vheight") or 0
    sorted_items = sorted(vl.items(), key=_height, reverse=True)
    qualities = []
    for key, item in sorted_items:
        h = item.get("vheight") or 0
        label = f"{h}p" if h else str(key)
        qualities.append({
            "key": key,
            "label": label,
            "height": h,
            "size": item.get("size") or 0,
        })

    # 选择目标档位：匹配 quality（支持 "1080p" 或 1080 或原始 key），否则最高
    target_key = ""
    if quality:
        q = str(quality).lower()
        for key, item in sorted_items:
            h = item.get("vheight") or 0
            if q == str(key).lower() or q == f"{h}p" or q == str(h):
                target_key = key
                break
    if not target_key:
        target_key = sorted_items[0][0]

    best_item = vl[target_key]
    real_url = best_item.get("main_url", "")
    if key_seed_raw and len(real_url) > 10:
        try:
            dec = decrypt_spade_url(real_url, key_seed_raw)
            if dec:
                real_url = dec
        except Exception:
            pass

    content_key = None
    spade_a = best_item.get("spade_a", "")
    if spade_a:
        try:
            content_key = derive_content_key(spade_a)
        except Exception:
            content_key = None

    return {
        "vid": vid,
        "url": real_url,
        "content_key": content_key.hex() if content_key else None,
        "quality": f"{best_item.get('vheight', 0)}p",
        "quality_key": target_key,
        "qualities": qualities,
        "total_size": best_item.get("size") or 0,
        "duration": vm.get("video_duration", 0),
        "cover": best_item.get("cover") or vm.get("origin_cover") or vm.get("cover_url") or "",
    }


# ─── 视频流式下载（后台任务）─────────────────────────────────────────────────

class VideoStreamManager:
    """管理视频下载任务：ffmpeg 解密到本地文件，前端边下边播"""

    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._resolve_cache: Dict[str, Dict[str, Any]] = {}

    def _resolve_cached(self, vid: str, quality: str = "") -> Optional[Dict[str, Any]]:
        """解析档位信息（内存缓存，用于缓存命中时给前端画质菜单）"""
        key = f"{vid}:{quality}"
        if key in self._resolve_cache:
            return self._resolve_cache[key]
        try:
            info = resolve_video_url(vid, quality)
            self._resolve_cache[key] = info
            return info
        except Exception:
            return None

    @staticmethod
    def cache_name(vid: str, quality: str = "") -> str:
        """v42 统一缓存文件名：固定 {vid}.mp4（不再按 quality 加后缀）
        画质由请求参数 quality 决定（URL 解析时用），不在文件名里区分
        这样 /api/play 和 /api/cache/range 写到同一个文件，命中检查一致
        """
        return f"{vid}.mp4"

    @staticmethod
    def meta_path(directory: Path, fname: str) -> Path:
        return directory / (fname + ".meta.json")

    @staticmethod
    def write_meta(directory: Path, fname: str, meta: Dict[str, Any]) -> None:
        try:
            import json as _json
            import time as _time
            # 自动注入 processed_at（视频被缓存/解密净化完成的时间），让清理线程按时间戳清理
            if "processed_at" not in meta:
                meta["processed_at"] = _time.time()
            (directory / (fname + ".meta.json")).write_text(
                _json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def read_meta(directory: Path, fname: str) -> Dict[str, Any]:
        try:
            import json as _json
            p = directory / (fname + ".meta.json")
            if p.exists():
                return _json.loads(p.read_text(encoding="utf-8") or "{}")
        except Exception:
            pass
        return {}

    def start(self, vid: str, quality: str = "", persist: bool = False,
              sid: str = "", ep_no: int = 0, title: str = "") -> Dict[str, Any]:
        """开始下载任务。
        - quality: 清晰度 key（""=最高档）
        - persist: True=写到 CACHE_DIR（持久缓存）; False=写到 TEMP_DIR（默认播放缓冲，下轮启动清空）
        - sid/ep_no/title: 缓存元数据（调用方知道时传入，写到 .meta.json 用于显示剧名）
        """
        fname = self.cache_name(vid, quality)
        target_dir = CACHE_DIR if persist else TEMP_DIR
        target_path = target_dir / fname
        temp_path = TEMP_DIR / fname
        # 缓存命中（仅持久缓存）：直接 ready，但同步/补写一次 meta
        # v35: 必须 meta.json 存在才算命中——v28 时代 raw_path（{vid}.mp4 无 meta）是加密文件，
        # 错误地当成净化产物会导致播放失败。看到没 meta 的旧文件直接删了重建
        meta_p = self.meta_path(target_dir, fname)
        if persist and target_path.exists() and target_path.stat().st_size > 100000:
            if not meta_p.exists():
                # 没 meta = 不是 stream_manager 写的净化产物，删了让 worker 重下
                print(f"[stream_manager] {fname} 存在但无 meta，"
                      f"视为残留（旧版 raw_path 或损坏），删除并重新下载", flush=True)
                try:
                    target_path.unlink()
                except Exception:
                    pass
                # 不 return，继续往下走 worker 下载流程
            else:
                task = {"vid": vid, "quality": quality, "fname": fname, "persist": True,
                        "status": "ready", "progress": 100, "percent": 100,
                        "file": str(target_path), "error": None, "qualities": [], "total_size": 0}
                info = self._resolve_cached(vid, quality)
                if info:
                    task["qualities"] = info.get("qualities") or []
                    task["total_size"] = info.get("total_size") or 0
                    task["quality"] = info.get("quality") or quality
                # 补写 meta（如果有新元数据）
                if sid or ep_no or title:
                    self.write_meta(target_dir, fname, {
                        "sid": sid, "vid": vid, "title": title,
                        "ep_no": ep_no, "quality": quality,
                        "mtime": target_path.stat().st_mtime if target_path.exists() else 0,
                    })
                return task
        # 主动缓存 + temp 已有成品 → 直接复制（不重新下载）
        if persist and (not target_path.exists() or target_path.stat().st_size < 100000) \
                and temp_path.exists() and temp_path.stat().st_size > 100000:
            try:
                import shutil
                shutil.copy2(str(temp_path), str(target_path))
                # 同时删 temp 释放空间
                try:
                    temp_path.unlink()
                except Exception:
                    pass
                task = {"vid": vid, "quality": quality, "fname": fname, "persist": True,
                        "status": "ready", "progress": 100, "percent": 100,
                        "file": str(target_path), "error": None, "qualities": [], "total_size": 0}
                info = self._resolve_cached(vid, quality)
                if info:
                    task["qualities"] = info.get("qualities") or []
                    task["total_size"] = info.get("total_size") or 0
                if sid or ep_no or title:
                    self.write_meta(target_dir, fname, {
                        "sid": sid, "vid": vid, "title": title,
                        "ep_no": ep_no, "quality": quality,
                        "mtime": target_path.stat().st_mtime if target_path.exists() else 0,
                    })
                return task
            except Exception as e:
                # 复制失败，回退到走 worker 重新下载
                pass
        key = fname
        with self._lock:
            if key in self._tasks:
                # 同 vid+qkey 的任务已存在
                existing = self._tasks[key]
                # 旧任务已失败 → 删掉重建（让 retry 能真正重跑，否则永远返回旧 error 状态）
                if existing.get("status") == "error":
                    del self._tasks[key]
                else:
                    if persist and not existing.get("persist") and existing.get("status") == "ready":
                        # 历史任务已 ready 在 temp，要切到 persist：复制到 cache
                        src = existing.get("file")
                        if src and Path(src).exists():
                            try:
                                import shutil
                                shutil.copy2(src, str(target_path))
                                try: Path(src).unlink()
                                except Exception: pass
                                existing["file"] = str(target_path)
                                existing["persist"] = True
                                if sid or ep_no or title:
                                    self.write_meta(target_dir, fname, {
                                        "sid": sid, "vid": vid, "title": title,
                                        "ep_no": ep_no, "quality": quality,
                                        "mtime": target_path.stat().st_mtime if target_path.exists() else 0,
                                    })
                                return existing
                            except Exception:
                                pass
                    existing["persist"] = persist
                    return existing
            task = {"vid": vid, "quality": quality, "fname": fname, "persist": persist,
                    "status": "downloading", "progress": 0, "percent": 0,
                    "file": None, "error": None, "qualities": [], "total_size": 0,
                    "sid": sid, "ep_no": ep_no, "title": title}
            self._tasks[key] = task
        threading.Thread(target=self._worker,
                         args=(vid, fname, target_path, task), daemon=True).start()
        return task

    def _worker(self, vid: str, fname: str, out_file: Path, task: Dict[str, Any]) -> None:
        try:
            quality = task.get("quality", "")
            info = resolve_video_url(vid, quality)
            task["qualities"] = info.get("qualities") or []
            task["total_size"] = info.get("total_size") or 0
            # 记录实际使用的档位 label（如 "1080p"），供前端同步显示
            task["quality"] = info.get("quality") or quality
            cmd = [FFMPEG, "-y"]
            if info.get("content_key"):
                cmd.extend(["-decryption_key", info["content_key"]])
            # v42: 改用 +faststart（普通 MP4，moov 前置）。
            # 之前的 frag_keyframe+empty_moov 是分片 MP4，文件管理器读不到缩略图，
            # 而且 WebView 加载时偶尔报"视频加载失败"。改成 faststart 后和 /api/play 产物一致。
            # 注：stream_manager 只用于持久缓存（/api/cache/add + /api/cache/range），
            # 前端不会用它做边下边播，所以 faststart 不会影响播放能力。
            cmd.extend(["-i", info["url"], "-c", "copy",
                        "-movflags", "+faststart", str(out_file)])
            # stderr 用临时文件收集（避免管道阻塞，且失败时能拿到 ffmpeg 错误信息）
            # 注意：subprocess.Popen 不接受 str 路径作为 stderr（必须传文件对象或 fd）
            import tempfile
            err_path = tempfile.mktemp(prefix="ffmpeg_err_", suffix=".log", dir=str(DATA_DIR))
            err_file = open(err_path, "wb")  # 文件对象（不是 str 路径）
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=err_file,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            total_size = info.get("total_size") or 0
            while proc.poll() is None:
                if out_file.exists():
                    size_mb = out_file.stat().st_size / 1024 / 1024
                    with self._lock:
                        t = self._tasks.get(fname)
                        if t:
                            t["progress"] = min(round(size_mb, 1), 999)
                            if total_size:
                                t["percent"] = min(round(size_mb * 1024 * 1024 / total_size * 100), 100)
                time.sleep(0.3)
            rc = proc.wait()
            err_file.close()  # 关闭文件对象后才能读取
            err_str = ""
            try:
                with open(err_path, "rb") as f:
                    err_str = f.read().decode("utf-8", errors="replace")[-500:]
                os.unlink(err_path)
            except Exception:
                pass
            if rc != 0 or not out_file.exists() or out_file.stat().st_size < 100000:
                raise RuntimeError(f"ffmpeg 失败(返回码{rc}): {err_str}" if err_str else f"ffmpeg 失败(返回码{rc})")
            # v43: ffmpeg decrypt+copy 后跑 mp4_sanitize 二次净化
            # 否则产物里还残留 sgpd/sbgp/saiz/senc 等 CENC box（CDN 源是 CENC-style 加密），
            # WebView demuxer 看到 saio 找不到 senc 会报"视频加载失败"。
            # /api/play 流程已经做过同样的净化，这里补上保持一致。
            # v51: 改为进程内调用——原来 `python /app/mp4_sanitize.py` 子进程
            # 只在容器里可用，PC 版（无 /app 目录）必然失败且被静默吞掉。
            san_path = str(out_file) + ".san.mp4"
            try:
                import mp4_sanitize as _mp4san
                if _mp4san.sanitize_mp4(str(out_file), san_path):
                    import shutil
                    shutil.move(san_path, str(out_file))
                    print(f"[stream_manager] sanitize 完成: {out_file.name}", flush=True)
                else:
                    print(f"[stream_manager] [WARN] sanitize 失败 (保留原文件)", flush=True)
            except Exception as _san_e:
                print(f"[stream_manager] [WARN] sanitize 失败 (保留原文件): {_san_e}", flush=True)
            with self._lock:
                t = self._tasks.get(fname)
                if t:
                    t["status"] = "ready"
                    t["progress"] = 100
                    t["percent"] = 100
                    t["file"] = str(out_file)
            # 持久缓存时写 meta.json（让缓存列表显示剧名+集数）
            if task.get("persist") and task.get("sid"):
                self.write_meta(out_file.parent, fname, {
                    "sid": task["sid"], "vid": vid, "title": task.get("title", ""),
                    "ep_no": task.get("ep_no", 0), "quality": quality,
                    "mtime": out_file.stat().st_mtime,
                })
        except Exception as e:
            with self._lock:
                t = self._tasks.get(fname)
                if t:
                    t["status"] = "error"
                    t["error"] = str(e)

    def get(self, vid: str, quality: str = "") -> Dict[str, Any]:
        fname = self.cache_name(vid, quality)
        with self._lock:
            return dict(self._tasks.get(fname, {"vid": vid, "quality": quality,
                                                "fname": fname, "status": "unknown"}))

    def get_by_fname(self, fname: str) -> Optional[Dict[str, Any]]:
        """按缓存文件名查任务（/stream 边下边播时判断文件是否仍在写入）"""
        with self._lock:
            t = self._tasks.get(fname)
            return dict(t) if t else None


stream_manager = VideoStreamManager()


if __name__ == "__main__":
    print("红果漫剧后端自检:")
    print(f"  ffmpeg: {FFMPEG}")
    cfg = ensure_device()
    print(f"  设备: device_id={cfg['device_id']}")
    # 测试搜索
    r = search_series("离婚", 1)
    print(f"  搜索'离婚': {len(r['items'])} 条")
    if r["items"]:
        sid = r["items"][0]["series_id"]
        print(f"  详情测试 series_id={sid}")
        d = get_series_detail(sid)
        print(f"    标题={d.get('title')} 集数={len(d.get('episodes', []))}")
        if d.get("episodes"):
            vid = d["episodes"][0]["vid"]
            print(f"  视频解析 vid={vid}")
            info = resolve_video_url(vid)
            print(f"    url={info['url'][:80]}... key={'有' if info['content_key'] else '无'}")
