# -*- mode: python ; coding: utf-8 -*-
"""红果漫剧 Electron 版 - Python 后端 PyInstaller 打包配置
产物：红果后端.exe（Electron 壳拉起的独立后端进程，端口 5137）
"""
import os
import sys
from pathlib import Path

B = Path(SPECPATH)
_BIN = B / "bin"
# bin/ 在 .gitignore 里,GitHub Actions checkout 时不存在,加 exists() 判断兜底
BIN_FILES = [(str(f), "bin") for f in _BIN.iterdir() if f.is_file()] if _BIN.exists() else []

a = Analysis(
    [str(B / "main_server.py")],
    pathex=[str(B)],
    binaries=BIN_FILES,
    datas=[
        (str(B / "static"), "static"),
        (str(B / "liushen"), "liushen"),
    ],
    hiddenimports=[
        # flask 全集:PyInstaller 6.10 + flask 3.0.3 偶尔漏 lazy import,显式列出避免 ModuleNotFoundError
        "flask", "flask.app", "flask.blueprints", "flask.cli", "flask.config",
        "flask.ctx", "flask.globals", "flask.helpers", "flask.json", "flask.json.tag",
        "flask.logging", "flask.scaffold", "flask.sessions", "flask.signals",
        "flask.templating", "flask.testing", "flask.wrappers",
        "werkzeug", "werkzeug.wrappers", "werkzeug.routing", "werkzeug.serving",
        "werkzeug.datastructures", "werkzeug.exceptions", "werkzeug.http",
        "werkzeug.local", "werkzeug.security", "werkzeug.utils",
        "jinja2", "jinja2.ext", "jinja2.sandbox", "jinja2.utils",
        "markupsafe", "itsdangerous", "click",
        # 其他第三方
        "flurl.core", "flurl.sign_proto", "flurl.xgorgon", "flurl.helios",
        "flurl.medusa", "flurl.header", "flurl.utils", "flurl.request_params",
        "flurl.ttEncryptorUtil", "flurl.extra", "flurl.branch_one",
        "gmssl", "gmssl.sm3", "gmssl.sm2", "gmssl.sm4", "gmssl.func",
        "snowland_smx", "betterproto",
        "Crypto", "Crypto.Cipher", "Crypto.Cipher.AES", "Crypto.Util",
        "Crypto.Util.Padding", "Crypto.Util.Counter", "Crypto.Hash",
        "Crypto.Hash.SHA512", "Crypto.Random",
        "requests", "urllib3", "charset_normalizer", "certifi", "idna",
        "bs4",
        # v51: server/hongguo_core 改为进程内净化调用（原 python /app/... 子进程已废弃）
        "mp4_sanitize",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide2", "webview", "clr", "pythonnet"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="红果后端",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
