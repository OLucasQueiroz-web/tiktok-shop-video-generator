# -*- mode: python ; coding: utf-8 -*-
# Build console (sem janela Tkinter) a partir de main.py -- é o processo que o
# app Electron (electron/pipeline.js) spawna em produção, lendo as linhas
# __PROG__:/__HISTORY_JSON__: do stdout. Não confundir com
# TikTokShopVideoGenerator.spec (esse aqui builda gui.py, windowed).
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
for pkg in ('groq', 'easyocr', 'torch', 'torchvision', 'cv2', 'scipy'):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TikTokShopVideoGeneratorCLI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TikTokShopVideoGeneratorCLI',
)
