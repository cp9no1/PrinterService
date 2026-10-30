# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PrintServer v3 (onefile, windowed)."""

a = Analysis(
    ['print_server.py'],
    pathex=[],
    binaries=[],
    datas=[('static', 'static')],
    hiddenimports=[
        'comtypes.client',
        'pythoncom',
        'pystray._win32',
        'win32timezone',
        'PIL.ImageWin',
        'reportlab.pdfbase',
        'reportlab.pdfbase.ttfonts',
        'pymupdf',
        'fitz',
        'openpyxl',
        'xlrd',
        'qrcode',
        'qrcode.image.pil',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'jupyter',
        'IPython',
        'pytest',
        'setuptools',
        'distutils',
        'unittest',
        'docx',
        'python-docx',
        'pptx',
        'python-pptx',
        'xlsxwriter',
        'xlwt',
        'pymupdf4llm',
        'pymupdf_layout',
        'onnxruntime',
        'networkx',
        'protobuf',
        'flatbuffers',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

icon_path = 'app.ico'
import os as _os
if not _os.path.exists(icon_path):
    icon_path = None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PrintServer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
