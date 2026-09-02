# -*- mode: python ; coding: utf-8 -*-
"""红果漫剧电脑版 PyInstaller 打包配置"""
import sys
from pathlib import Path

APP_DIR = Path(SPECPATH)

a = Analysis(
    [str(APP_DIR / "main.py")],
    pathex=[str(APP_DIR)],
    binaries=[(str(APP_DIR / "bin"), "bin")],
    datas=[
        (str(APP_DIR / "static"), "static"),
        (str(APP_DIR / "liushen"), "liushen"),
    ],
    hiddenimports=[
        "flurl.core", "flurl.sign_proto", "flurl.xgorgon", "flurl.helios",
        "flurl.medusa", "flurl.header", "flurl.utils", "flurl.request_params",
        "flurl.ttEncryptorUtil", "flurl.extra", "flurl.branch_one",
        "gmssl", "gmssl.sm3", "gmssl.sm2", "gmssl.sm4", "gmssl.func",
        "snowland_smx", "betterproto",
        "Crypto", "Crypto.Cipher", "Crypto.Cipher.AES", "Crypto.Util",
        "Crypto.Util.Padding", "Crypto.Util.Counter", "Crypto.Hash",
        "Crypto.Hash.SHA512", "Crypto.Random",
        # pywebview Windows 后端（PyInstaller 经典漏网模块）
        "webview", "webview.platforms", "webview.platforms.winforms",
        "webview.platforms.edgechromium", "webview.platforms.winforms_webview2",
        "clr", "System", "System.Windows.Forms", "System.Drawing",
        "pythonnet",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="红果漫剧",
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
