# -*- coding: utf-8 -*-
"""v52 冒烟测试：路由 / 清理器 24h / 索引落盘 / 净化 helper / CDN 白名单。

CI 跑法（参见 .github/workflows/test.yml）：
  1) pytest tests/ -v
  2) python scripts/sync_core.py --check

约束：必须用临时 HONGGUO_DATA_DIR（绝不触碰仓库内 cache/），且需 monkeypatch
网络出口（fetch_category_page / search_suggestion）避免触发真网。
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest


# ─── 一次性环境初始化（所有用例共享同一份临时后端） ──────────────────────────
@pytest.fixture(scope="session")
def backend():
    """启动后端一份，sys.path 注入 nas-backend,设置 HONGGUO_DATA_DIR 临时目录。"""
    tmp = Path(tempfile.mkdtemp(prefix="hongguo_test_"))
    os.environ["HONGGUO_DATA_DIR"] = str(tmp)
    # tests/ 在 nas-backend/ 下，parents[1] 就是 nas-backend 本身
    nas_dir = Path(__file__).resolve().parents[1]
    if not (nas_dir / "server.py").exists():
        pytest.skip(f"nas-backend/server.py 不存在: {nas_dir}")
    sys.path.insert(0, str(nas_dir))
    # 关闭后台 browseworker 的网络访问影响（连网后 SESSION 跑 sitemap 可能很慢或超时）
    # —— 通过 fixture 里 monkeypatch 处理（每个 case 单独 mock）
    import server  # noqa: F401
    import hongguo_core
    return {"tmp": tmp, "server": server, "core": hongguo_core}


@pytest.fixture(autouse=True)
def _no_real_network(backend, monkeypatch):
    """禁止测试触发真网：把 SESSION.get/post 都拦截掉（除非 case 自己另 mock）。"""
    import requests
    real_get = requests.Session.get
    real_post = requests.Session.post

    def fake_request(self, method, url, *a, **kw):
        # 默认兜底：返回空响应/空 JSON，让上层走 except 分支
        from unittest.mock import MagicMock
        m = MagicMock()
        m.status_code = 599
        m.text = ""
        m.content = b""
        m.json.side_effect = ValueError("mocked no network")
        m.headers = {}
        m.iter_content = lambda *a, **kw: iter([])
        m.raise_for_status = lambda: None
        return m

    monkeypatch.setattr(requests.Session, "get", fake_request)
    monkeypatch.setattr(requests.Session, "post", fake_request)
    yield


# ─── 路由 ────────────────────────────────────────────────────────────────
def test_ping(backend):
    c = backend["server"].app.test_client()
    r = c.get("/api/ping")
    assert r.json.get("ok") is True


def test_settings_get_put(backend):
    c = backend["server"].app.test_client()
    r = c.get("/api/settings")
    assert r.json.get("ok") is True and "cache_dir" in r.json
    r = c.put("/api/settings", json={"cache_dir": str(backend["tmp"] / "custom")})
    assert r.json.get("ok") is True
    assert "custom" in r.json.get("cache_dir", "")
    # 恢复默认
    c.put("/api/settings", json={"cache_dir": ""})


def test_cache_get_post(backend):
    c = backend["server"].app.test_client()
    r = c.get("/api/cache")
    assert r.json.get("ok") is True


def test_manual_series_get(backend):
    c = backend["server"].app.test_client()
    r = c.get("/api/manual_series")
    assert r.json.get("ok") is True and "items" in r.json


def test_prefs_get(backend):
    c = backend["server"].app.test_client()
    r = c.get("/api/prefs")
    assert r.json.get("ok") is True


# ─── CDN 白名单 (T9) ──────────────────────────────────────────────────────
def test_cdn_whitelist_blocks_unknown(backend):
    c = backend["server"].app.test_client()
    # 非白名单域名 → 403
    r = c.get("/api/cdn?u=https://www.baidu.com/test.mp4")
    assert r.status_code == 403
    assert "not allowed" in r.get_json().get("error", "")
    # 合法域名但连接超时（被 _no_real_network 拦截）→ 502 upstream error
    r = c.get("/api/cdn?u=https://v26-cold.douyinvod.com/test.mp4")
    assert r.status_code == 502
    # 缺 u 参数 → 400
    r = c.get("/api/cdn")
    assert r.status_code == 400


# ─── 缓存清理器 24h (T3/v51) ─────────────────────────────────────────────
def test_cache_cleaner_24h(backend):
    cache_dir = backend["core"].CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    old_ts = time.time() - 25 * 3600
    # 1) 超 24h 带 meta → 应删
    f1 = cache_dir / "v_old.mp4"
    f1.write_bytes(b"x" * 200000)
    f1.with_suffix(f1.suffix + ".meta.json").write_text(
        json.dumps({"processed_at": old_ts}), encoding="utf-8"
    )
    # 2) 新鲜带 meta → 保留
    f2 = cache_dir / "v_fresh.mp4"
    f2.write_bytes(b"x" * 200000)
    f2.with_suffix(f2.suffix + ".meta.json").write_text(
        json.dumps({"processed_at": time.time()}), encoding="utf-8"
    )
    # 3) 超 24h raw 中间产物 → 应删
    f3 = cache_dir / "v_old.raw.mp4"
    f3.write_bytes(b"x" * 200000)
    os.utime(f3, (old_ts, old_ts))
    # 4) 孤儿 meta → 应清
    orphan = cache_dir / "v_ghost.mp4.meta.json"
    orphan.write_text("{}", encoding="utf-8")

    backend["server"]._do_cache_clean()

    assert not f1.exists(), "超 24h 缓存应被清理"
    assert not f1.with_suffix(f1.suffix + ".meta.json").exists(), "伴生 meta 应一并删"
    assert f2.exists(), "新鲜缓存应保留"
    assert not f3.exists(), "超 24h raw 中间产物应被清理"
    assert not orphan.exists(), "孤儿 meta 应被清"


def test_cache_post_clears_meta(backend):
    cache_dir = backend["core"].CACHE_DIR
    (cache_dir / "v2.mp4").write_bytes(b"x" * 200000)
    (cache_dir / "v2.mp4.meta.json").write_text("{}", encoding="utf-8")
    c = backend["server"].app.test_client()
    r = c.post("/api/cache")
    assert r.json.get("ok") is True and r.json.get("removed", 0) >= 1
    assert not (cache_dir / "v2.mp4.meta.json").exists()


# ─── 进程内净化 helper ──────────────────────────────────────────────────
def test_sanitize_helper_safe_on_non_mp4(backend):
    cache_dir = backend["core"].CACHE_DIR
    bad = cache_dir / "bad.mp4"
    bad.write_bytes(b"not an mp4")
    ok = backend["server"]._sanitize_mp4_file(bad, cache_dir / "bad.out.mp4")
    assert ok is False, "非 MP4 输入应安全返回 False"


# ─── 索引落盘 (v51) ─────────────────────────────────────────────────────
def test_index_disk_cache(backend, monkeypatch):
    """build_series_index 落盘 + 二次调用 0 网络请求。"""
    calls = {"n": 0}

    def fake_fetch(page_num, tab, sort_type):
        calls["n"] += 1
        if page_num > 2:
            return []
        return [{
            "series_id": f"{tab}{sort_type}{page_num}{i:03d}",
            "series_name": f"剧{i}",
            "tags": ["x"],
            "episode_cnt": 10,
        } for i in range(24)]

    monkeypatch.setattr(backend["core"], "fetch_category_page", fake_fetch)

    # 第一次：force=True 触发构建
    backend["core"]._INDEX_CACHE = {"data": None, "time": 0}
    items = backend["core"].build_series_index(force=True)
    # 6 条链（2 tab × 3 sort）× 2 页有效 + 每链多打 1 次空页才 break = 6*3 = 18 次
    assert len(items) == 6 * 2 * 24, f"items={len(items)}"
    assert calls["n"] == 6 * 3, f"calls={calls['n']}"

    disk = backend["tmp"] / "cache" / "series_index.json"
    assert disk.exists(), "索引落盘文件应存在"

    # 第二次：清内存缓存,期望 0 网络请求(走落盘)
    backend["core"]._INDEX_CACHE = {"data": None, "time": 0}
    calls["n"] = 0
    items2 = backend["core"].build_series_index()
    assert items2 == items, "落盘加载结果应与构建一致"
    assert calls["n"] == 0, "落盘加载应零网络请求"


# ─── browse 上限 (T5) ────────────────────────────────────────────────────
def test_browse_prefetch_limit_constant(backend):
    assert hasattr(backend["core"], "BROWSE_PREFETCH_LIMIT")
    assert backend["core"].BROWSE_PREFETCH_LIMIT == 2000


# ─── AI 搜剧 (智谱) ──────────────────────────────────────────────────────
def test_ai_search_no_key_returns_502(backend):
    """未配置 ZHIPU_API_KEY 时,后端应返回 502 + 明确错误信息,前端可降级到普通搜索。"""
    # 确保环境变量清空
    os.environ.pop("ZHIPU_API_KEY", None)
    c = backend["server"].app.test_client()
    r = c.get("/api/search_ai?q=想看重生复仇类短剧")
    assert r.status_code == 502
    body = r.get_json()
    assert body.get("ok") is False
    assert "ZHIPU_API_KEY" in body.get("error", "")


def test_ai_search_empty_q_returns_400(backend):
    """q 为空时直接 400(不调 GLM)。"""
    c = backend["server"].app.test_client()
    r = c.get("/api/search_ai?q=")
    assert r.status_code == 400