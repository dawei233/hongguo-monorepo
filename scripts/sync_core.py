# -*- coding: utf-8 -*-
"""同步三端共用文件：nas-backend（基线）→ pc/backend

背景：hongguo_core.py / mp4_sanitize.py / static/index.html 在 NAS 与 PC 两端各放一份，
历史靠人肉 cp 同步，漏 cp 就出隐性 bug（v50 前 hongguo_core 已漂移过一次）。
改动共用代码后跑一次本脚本即可；server.py 不在列表里——PC 版有意多出
NAS prefs 同步逻辑（v49+），两端各自维护。

用法:
  python scripts/sync_core.py            # 把基线文件同步到 pc/backend
  python scripts/sync_core.py --check    # 只检查是否一致（CI 用），不一致退出码 1
"""
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAIRS = [
    ("nas-backend/hongguo_core.py", "pc/backend/hongguo_core.py"),
    ("nas-backend/mp4_sanitize.py", "pc/backend/mp4_sanitize.py"),
    ("nas-backend/static/index.html", "pc/backend/static/index.html"),
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(check: bool = False) -> int:
    missing = 0
    changed = 0
    for src_rel, dst_rel in PAIRS:
        src, dst = ROOT / src_rel, ROOT / dst_rel
        if not src.exists():
            print(f"[缺失] 基线文件不存在: {src}")
            missing += 1
            continue
        if dst.exists() and sha256(src) == sha256(dst):
            continue
        changed += 1
        if check:
            print(f"[不同步] {src_rel} → {dst_rel}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"[已同步] {src_rel} → {dst_rel}")
    if missing:
        return 2
    if check:
        print(f"{'FAIL: ' + str(changed) + ' 个文件待同步' if changed else 'OK: 全部一致'}")
        return 1 if changed else 0
    print("OK: 同步完成")
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
