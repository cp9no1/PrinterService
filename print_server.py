# -*- coding: utf-8 -*-
"""PrintServer v3 — 内网打印服务（重建优化版）

在原版（https://github.com/Geekstrange/PrinterService ）基础上重写：

主要改进
--------
1. 图片打印：上传的手机照片自动按 EXIF 方向校正；网页端三步向导中提供
   "适应页面 / 铺满页面 / 原始大小"、旋转、边距、纸张、方向调整，并提供
   所见即所得的页面预览（可缩放/捏合查看）。静默打印按可打印区域铺满，
   不再额外缩小 10%，解决"打出来很小"的问题。
2. 隐私隔离：每个浏览器（cookie 会话）拥有独立的 uploads/pdfs 目录，
   只能看到、打印、删除自己上传的文件与自己的打印记录；同时在局域网内
   无法访问他人文件。服务所在主机（localhost）自动成为管理员视图，
   可查看所有会话、清理全部文件。
3. 界面：三步向导（1 上传 → 2 预览调整 → 3 打印设置），手机优先响应式。
4. 打印质量：按打印机可打印区域精确铺满；支持纸张（A4/A5/Letter/4R/5R）、
   单双面（通过 DEVMODE 真正生效）、150/300 DPI 渲染；打印任务串行化。

保留原版能力
------------
PDF / Office(DOC/DOCX/XLS/XLSX/PPT/PPTX) / 图片 / 文本自动转 PDF；
页码范围、Excel 工作表与打印区域选择；托盘图标、开机自启、文件自动清理。

运行
----
    python print_server.py [--port 5000] [--no-tray] [--open-browser]

打包
----
    pyinstaller PrinterService.spec
"""

import argparse
import ctypes
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import webbrowser
from ctypes import wintypes
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 基础常量
# ---------------------------------------------------------------------------
APP_NAME = "内网打印服务"
VERSION = "3.0"

DEFAULT_PORT = int(os.environ.get("PRINTSERVER_PORT", "5000"))
MAX_UPLOAD_MB = int(os.environ.get("PRINTSERVER_MAX_UPLOAD_MB", "200"))
FILE_EXPIRE_SECONDS = int(os.environ.get("PRINTSERVER_FILE_EXPIRE_SECONDS", "3600"))
CLEAN_INTERVAL_SECONDS = 600

PRINT_DPI_NORMAL = int(os.environ.get("PRINTSERVER_PRINT_DPI", "150"))
PRINT_DPI_HIGH = 300
PREVIEW_DPI = 120

# 纸张尺寸（毫米，纵向宽 x 高）
PAPER_SIZES_MM = {
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "Letter": (215.9, 279.4),
    "4R": (102.0, 152.0),
    "5R": (127.0, 178.0),
}
PAPER_STD_ID = {"Letter": 1, "A4": 9, "A5": 11}
DMPAPER_USER = 256

ORIENTATIONS = ("portrait", "landscape", "reverse_landscape", "reverse_portrait")
IMAGE_FITS = ("fit", "fill", "actual")

IMAGE_EXTS = {"jpg", "jpeg", "png", "bmp", "gif", "tiff"}
TEXT_EXTS = {"txt", "log", "md"}
OFFICE_EXTS = {"doc", "docx", "xls", "xlsx", "ppt", "pptx"}
ALLOWED_EXT = IMAGE_EXTS | TEXT_EXTS | OFFICE_EXTS | {"pdf"}

KIND_BY_EXT = {
    "pdf": "pdf",
    "jpg": "image", "jpeg": "image", "png": "image", "bmp": "image",
    "gif": "image", "tiff": "image",
    "txt": "text", "log": "text", "md": "text",
    "doc": "doc", "docx": "doc",
    "xls": "sheet", "xlsx": "sheet",
    "ppt": "slide", "pptx": "slide",
}

KIND_ICON = {
    "pdf": "📕", "image": "🖼️", "text": "📄", "doc": "📝",
    "sheet": "📊", "slide": "📽️", "other": "📁",
}


def safe_print(*args, **kwargs):
    """在无控制台 / GBK 控制台下也不会因 emoji 崩溃的 print。"""
    try:
        print(*args, **kwargs)
    except Exception:
        try:
            sys.stdout.write(" ".join(str(a) for a in args) + "\n")
        except Exception:
            pass


try:  # 让 Windows 控制台尽量使用 UTF-8
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------------------
# 可选依赖
# ---------------------------------------------------------------------------
try:
    import pymupdf as fitz  # PyMuPDF >= 1.24 推荐名称
    PYMUPDF_AVAILABLE = True
except ImportError:
    try:
        import fitz  # 兼容旧版
        PYMUPDF_AVAILABLE = True
    except ImportError:
        fitz = None
        PYMUPDF_AVAILABLE = False

try:
    import comtypes.client
    import pythoncom
    OFFICE_AVAILABLE = True
except ImportError:
    comtypes = None
    pythoncom = None
    OFFICE_AVAILABLE = False

try:
    import win32print
    import win32ui
    import win32con
    import win32api
    WIN32_AVAILABLE = True
except ImportError:
    win32print = win32ui = win32con = win32api = None
    WIN32_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageWin, ImageOps
    from PIL import Image as PILImage
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

try:
    import qrcode
    QRCODE_AVAILABLE = True
except ImportError:
    qrcode = None
    QRCODE_AVAILABLE = False

try:
    from flask import (
        Flask, request, render_template, send_file, jsonify, session, Response,
        abort,
    )
    from werkzeug.exceptions import HTTPException
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    raise

# ---------------------------------------------------------------------------
# 运行环境 / 目录
# ---------------------------------------------------------------------------
def _get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _get_base_dir()


def get_resource_path(relative_path):
    """PyInstaller onefile 解包目录 或 源码目录。"""
    base_path = getattr(sys, "_MEIPASS", None) or BASE_DIR
    return os.path.join(base_path, relative_path)


def _pick_data_dir():
    candidates = [os.getcwd(), BASE_DIR]
    for cand in candidates:
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".printserver_write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return cand
        except Exception:
            continue
    return os.getcwd()


STATIC_FOLDER = get_resource_path("static")
DATA_DIR = os.environ.get("PRINTSERVER_DATA_DIR") or _pick_data_dir()
UPLOAD_ROOT = os.path.join(DATA_DIR, "uploads")
PDF_ROOT = os.path.join(DATA_DIR, "pdfs")
TEMP_ROOT = os.path.join(DATA_DIR, "temp")
LOG_FILE = os.path.join(DATA_DIR, "print_log.txt")
LOG_JSONL = os.path.join(DATA_DIR, "print_log.jsonl")
SECRET_FILE = os.path.join(DATA_DIR, ".printserver_secret")
SERVER_LOG = os.path.join(DATA_DIR, "server.log")

PORT = DEFAULT_PORT
HOST = "0.0.0.0"

# 运行时状态
_print_lock = threading.Lock()          # 串行化打印任务（共享打印机）
_office_lock = threading.Lock()         # Office COM 自动化串行化
_log_lock = threading.Lock()
_page_cache = {}
_page_cache_lock = threading.Lock()
_server_log_lock = threading.Lock()


def ensure_dirs():
    for d in (UPLOAD_ROOT, PDF_ROOT, TEMP_ROOT):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass


def server_log(message):
    """轻量服务器日志（窗口版 exe 无控制台，便于排查问题）。"""
    line = "%s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
    try:
        with _server_log_lock:
            try:
                if os.path.exists(SERVER_LOG) and os.path.getsize(SERVER_LOG) > 1_000_000:
                    os.replace(SERVER_LOG, SERVER_LOG + ".old")
            except Exception:
                pass
            with open(SERVER_LOG, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 文件名 / 会话辅助
# ---------------------------------------------------------------------------
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def clean_filename(filename):
    """保留中文的安全文件名（去掉路径分隔符与非法字符）。"""
    name = os.path.basename(str(filename or "")).strip()
    name = _INVALID_FILENAME.sub("_", name).strip(" .")
    if not name:
        name = "file"
    base, ext = os.path.splitext(name)
    if len(base) > 80:
        base = base[:80]
    if len(ext) > 12:
        ext = ext[:12]
    return (base or "file") + ext


def sanitize_filename(filename):
    return clean_filename(filename)


def pdf_name_for(upload_name):
    base, _ext = os.path.splitext(upload_name)
    return clean_filename(base) + ".pdf"


def kind_of(filename):
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    return KIND_BY_EXT.get(ext, "other")


def session_dirs(sid, create=False):
    up = os.path.join(UPLOAD_ROOT, sid)
    pp = os.path.join(PDF_ROOT, sid)
    if create:
        os.makedirs(up, exist_ok=True)
        os.makedirs(pp, exist_ok=True)
    return up, pp


def get_sid():
    """返回当前浏览器会话 ID（cookie 签名会话），首次访问时创建。"""
    sid = session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        session["sid"] = sid
        session.permanent = True
    return sid


def is_admin_request():
    if os.environ.get("PRINTSERVER_ADMIN_DISABLED") == "1":
        return False
    addr = (request.remote_addr or "").strip()
    if addr in ("127.0.0.1", "::1", "localhost"):
        return True
    if addr.startswith("::ffff:127.0.0.1"):
        return True
    extra = os.environ.get("PRINTSERVER_ADMIN_IPS", "")
    if extra:
        allowed = {x.strip() for x in extra.split(",") if x.strip()}
        if addr in allowed:
            return True
    return False


class FileAccessError(Exception):
    pass


def _safe_sid_param(sid_param):
    sid_param = (sid_param or "").strip()
    if not _SID_RE.match(sid_param):
        raise FileAccessError("非法会话标识")
    return sid_param


def resolve_owned_file(name, sid_param=None):
    """在当前会话（管理员可指定 sid）中定位文件。

    返回 (sid, upload_path, pdf_path)；权限不足或文件不存在抛 FileAccessError。
    """
    name = clean_filename(name)
    if not name or name in (".", ".."):
        raise FileAccessError("文件名无效")
    sid = get_sid()
    if sid_param:
        if not is_admin_request():
            raise FileAccessError("无权访问其他会话的文件")
        sid = _safe_sid_param(sid_param)
    udir, pdir = session_dirs(sid)
    upload_path = os.path.join(udir, name)
    pdf_path = os.path.join(pdir, pdf_name_for(name))
    if not os.path.isfile(upload_path) and not os.path.isfile(pdf_path):
        raise FileAccessError("文件不存在或已被清理")
    return sid, upload_path, pdf_path


def _session_id_exists(sid):
    if not _SID_RE.match(sid or ""):
        return False
    return os.path.isdir(os.path.join(UPLOAD_ROOT, sid))


# ---------------------------------------------------------------------------
# PDF / 图片工具
# ---------------------------------------------------------------------------
def get_page_count(pdf_path, use_cache=True):
    if not pdf_path or not os.path.isfile(pdf_path):
        return 0
    if not PYMUPDF_AVAILABLE:
        return 0
    try:
        st = os.stat(pdf_path)
        key = (os.path.abspath(pdf_path), st.st_mtime, st.st_size)
        if use_cache:
            with _page_cache_lock:
                cached = _page_cache.get(key)
            if cached is not None:
                return cached
        doc = fitz.open(pdf_path)
        try:
            count = len(doc)
        finally:
            doc.close()
        if use_cache:
            with _page_cache_lock:
                if len(_page_cache) > 512:
                    _page_cache.clear()
                _page_cache[key] = count
        return count
    except Exception:
        return 0


def render_pdf_page_png(pdf_path, page_number, dpi=PREVIEW_DPI):
    """把 PDF 的某一页（1 基）渲染为 PNG bytes。"""
    if not PYMUPDF_AVAILABLE:
        raise RuntimeError("PyMuPDF 未安装，无法预览")
    dpi = max(60, min(240, int(dpi or PREVIEW_DPI)))
    doc = fitz.open(pdf_path)
    try:
        total = len(doc)
        if page_number < 1 or page_number > total:
            raise RuntimeError("页码超出范围")
        page = doc[page_number - 1]
        mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _mm_to_pt(mm):
    return float(mm) * 72.0 / 25.4


def _normalize_paper(paper):
    if paper in PAPER_SIZES_MM:
        return paper
    for key in PAPER_SIZES_MM:
        if key.lower() == str(paper or "").strip().lower():
            return key
    return "A4"


def _clamp(value, low, high, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _rotate_pil_image(img, rotate):
    """rotate 为顺时针角度，仅支持 0/90/180/270。"""
    rotate = int(rotate) % 360
    if rotate == 90:
        return img.transpose(PILImage.Transpose.ROTATE_270)  # 顺时针 90
    if rotate == 180:
        return img.transpose(PILImage.Transpose.ROTATE_180)
    if rotate == 270:
        return img.transpose(PILImage.Transpose.ROTATE_90)
    return img


def _prepare_image_for_pdf(image_path, rotate=0):
    """读取图片：EXIF 方向校正 → RGB → 用户旋转。"""
    img = PILImage.open(image_path)
    try:
        img.load()
    except Exception:
        pass
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        background = PILImage.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return _rotate_pil_image(img, rotate)


def _image_target_dpi_for_layout(page_w_pt, page_h_pt, margin_pt):
    """限制写入 PDF 的图片分辨率（最高约 300 DPI，避免 PDF 过大）。"""
    content_w = max(1.0, page_w_pt - 2 * margin_pt)
    content_h = max(1.0, page_h_pt - 2 * margin_pt)
    target_px = int(max(content_w, content_h) / 72.0 * 300)
    return max(600, target_px)


def build_image_pdf(image_path, output_path, fit="fit", rotate=0, margin_mm=5.0,
                    paper="A4", page_orientation="portrait"):
    """把图片排版成 PDF（图片真正决定打印大小的地方）。

    fit:
      - fit    适应页面：完整显示，保持比例，居中，四周留边距
      - fill   铺满页面：按比例放大铺满内容区，居中裁切（照片推荐）
      - actual 原始大小：按图片自身 DPI（缺省 96）实际尺寸，超出页面时缩小到合适
    rotate: 顺时针旋转角度（0/90/180/270），在适应/铺满计算之前应用
    margin_mm: 边距（毫米）
    paper: A4/A5/Letter/4R/5R
    page_orientation: portrait / landscape
    """
    if not PIL_AVAILABLE or not REPORTLAB_AVAILABLE:
        raise RuntimeError("Pillow/reportlab 未安装，无法处理图片")
    paper = _normalize_paper(paper)
    page_orientation = "landscape" if str(page_orientation).lower() == "landscape" else "portrait"
    fit = fit if fit in IMAGE_FITS else "fit"
    rotate = int(rotate) % 360
    if rotate not in (0, 90, 180, 270):
        rotate = 0
    margin_mm = _clamp(margin_mm, 0, 30, 5)
    if fit == "fill":
        margin_mm = _clamp(margin_mm, 0, 30, 0)

    img = _prepare_image_for_pdf(image_path, rotate)
    try:
        pw_mm, ph_mm = PAPER_SIZES_MM[paper]
        if page_orientation == "landscape":
            pw_mm, ph_mm = ph_mm, pw_mm
        page_w, page_h = _mm_to_pt(pw_mm), _mm_to_pt(ph_mm)
        margin = _mm_to_pt(margin_mm)
        content_w = max(1.0, page_w - 2 * margin)
        content_h = max(1.0, page_h - 2 * margin)

        # 限制超大图片尺寸，保证 PDF 不至于过大
        max_px = _image_target_dpi_for_layout(page_w, page_h, margin)
        iw, ih = img.size
        if max(iw, ih) > max_px * 2:
            ratio = (max_px * 2.0) / float(max(iw, ih))
            img = img.resize((max(1, int(iw * ratio)), max(1, int(ih * ratio))),
                             PILImage.Resampling.LANCZOS)
            iw, ih = img.size

        c = canvas.Canvas(output_path, pagesize=(page_w, page_h))
        if fit == "fill":
            scale = max(content_w / float(iw), content_h / float(ih))
            keep_w = int(round(min(iw, content_w / scale)))
            keep_h = int(round(min(ih, content_h / scale)))
            left = max(0, (iw - keep_w) // 2)
            top = max(0, (ih - keep_h) // 2)
            crop = img.crop((left, top, left + keep_w, top + keep_h))
            c.drawImage(ImageReader(crop), margin, margin,
                        width=content_w, height=content_h)
        elif fit == "actual":
            dpi_x, dpi_y = 96.0, 96.0
            try:
                info_dpi = img.info.get("dpi")
                if info_dpi and float(info_dpi[0]) > 1 and float(info_dpi[1]) > 1:
                    dpi_x, dpi_y = float(info_dpi[0]), float(info_dpi[1])
            except Exception:
                pass
            draw_w = iw / dpi_x * 72.0
            draw_h = ih / dpi_y * 72.0
            scale = min(1.0, content_w / draw_w, content_h / draw_h)
            draw_w *= scale
            draw_h *= scale
            x = (page_w - draw_w) / 2.0
            y = (page_h - draw_h) / 2.0
            c.drawImage(ImageReader(img), x, y, width=draw_w, height=draw_h)
        else:  # fit
            scale = min(content_w / float(iw), content_h / float(ih))
            draw_w = iw * scale
            draw_h = ih * scale
            x = (page_w - draw_w) / 2.0
            y = (page_h - draw_h) / 2.0
            c.drawImage(ImageReader(img), x, y, width=draw_w, height=draw_h)
        c.setTitle("PrintServer image")
        c.save()
        return True
    finally:
        try:
            img.close()
        except Exception:
            pass


def build_image_pdf_bytes(image_path, **kwargs):
    """生成图片 PDF 到内存，用于 /api/image_layout 实时预览。"""
    buf = io.BytesIO()
    tmp_path = os.path.join(TEMP_ROOT, "preview_%s.pdf" % uuid.uuid4().hex)
    ensure_dirs()
    try:
        build_image_pdf(image_path, tmp_path, **kwargs)
        with open(tmp_path, "rb") as f:
            buf.write(f.read())
        return buf.getvalue()
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def render_image_layout_png(image_path, dpi=PREVIEW_DPI, **layout):
    """按图片排版参数生成「实际打印效果」的 PNG。"""
    pdf_bytes = build_image_pdf_bytes(image_path, **layout)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[0]
        dpi = max(60, min(200, int(dpi or PREVIEW_DPI)))
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def parse_page_range(range_str, total_pages):
    """解析 '1-5,7,all'，返回 0 基页码列表。"""
    range_str = (range_str or "").strip()
    if not range_str or range_str.lower() == "all":
        return list(range(total_pages))
    pages = set()
    for part in range_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start_s, end_s = part.split("-", 1)
                start, end = int(start_s.strip()), int(end_s.strip())
                if start > end:
                    start, end = end, start
                start = max(1, start)
                end = min(total_pages, end)
                for p in range(start, end + 1):
                    pages.add(p - 1)
            except ValueError:
                continue
        else:
            try:
                p = int(part)
                if 1 <= p <= total_pages:
                    pages.add(p - 1)
            except ValueError:
                continue
    if not pages:
        return list(range(total_pages))
    return sorted(pages)


# ---------------------------------------------------------------------------
# 文本 / Office → PDF（转换链：MS Office → WPS → LibreOffice）
# ---------------------------------------------------------------------------
_CJK_FONT_NAME = None
_CJK_FONT_LOCK = threading.Lock()


def get_cjk_font():
    global _CJK_FONT_NAME
    if _CJK_FONT_NAME:
        return _CJK_FONT_NAME
    with _CJK_FONT_LOCK:
        if _CJK_FONT_NAME:
            return _CJK_FONT_NAME
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont

            candidates = [
                ("SimHei", r"C:\Windows\Fonts\simhei.ttf"),
                ("MicrosoftYaHei", r"C:\Windows\Fonts\msyh.ttc"),
                ("SimSun", r"C:\Windows\Fonts\simsun.ttc"),
                ("DengXian", r"C:\Windows\Fonts\Deng.ttf"),
            ]
            for font_name, font_path in candidates:
                try:
                    if os.path.exists(font_path):
                        pdfmetrics.registerFont(TTFont(font_name, font_path))
                        _CJK_FONT_NAME = font_name
                        return font_name
                except Exception:
                    continue
        except Exception:
            pass
        _CJK_FONT_NAME = "Helvetica"
        return _CJK_FONT_NAME


def _read_text_file(path):
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312", "big5", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            return None
    return None


def convert_text_to_pdf(text_path, output_path):
    if not REPORTLAB_AVAILABLE:
        return False
    try:
        content = _read_text_file(text_path)
        if content is None:
            safe_print("无法读取文本文件: %s" % text_path)
            return False
        content = content.replace("\r\n", "\n").replace("\r", "\n")

        page_w, page_h = A4
        margin = 50.0
        font_size = 10.5
        line_height = 16.0
        font_name = get_cjk_font()
        is_cjk = font_name != "Helvetica"
        char_width = font_size * (1.0 if is_cjk else 0.6)
        max_chars = max(20, int((page_w - 2 * margin) / char_width))

        c = canvas.Canvas(output_path, pagesize=A4)
        c.setFont(font_name, font_size)
        y = page_h - margin
        for raw_line in content.split("\n"):
            raw_line = raw_line.expandtabs(4)
            if not raw_line.strip():
                y -= line_height
                if y < margin:
                    c.showPage()
                    c.setFont(font_name, font_size)
                    y = page_h - margin
                continue
            chunks = [raw_line[i:i + max_chars] for i in range(0, len(raw_line), max_chars)]
            for chunk in chunks:
                if y < margin:
                    c.showPage()
                    c.setFont(font_name, font_size)
                    y = page_h - margin
                try:
                    c.drawString(margin, y, chunk)
                except Exception:
                    try:
                        c.drawString(margin, y, chunk.encode("ascii", "replace").decode("ascii"))
                    except Exception:
                        pass
                y -= line_height
        c.save()
        return True
    except Exception as e:
        safe_print("文本转PDF失败: %s" % e)
        return False


def _check_wps():
    """不启动任何进程，只检查 WPS COM 是否注册。"""
    if not OFFICE_AVAILABLE:
        return False
    try:
        import winreg
        for progid in ("WPS.Application", "ET.Application", "WPP.Application",
                       "KWPS.Application", "KET.Application", "KWPP.Application"):
            try:
                key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + "\\CLSID")
                winreg.CloseKey(key)
                return True
            except Exception:
                continue
    except Exception:
        return False
    return False


WPS_AVAILABLE = _check_wps()


def find_libreoffice():
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        r"C:\LibreOffice\program\soffice.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(path_dir, "soffice.exe")
        if os.path.exists(candidate):
            return candidate
    return None


LIBREOFFICE_PATH = find_libreoffice()
LIBREOFFICE_AVAILABLE = LIBREOFFICE_PATH is not None


def _get_excel_sheets_xlsx(filepath):
    wb = None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath, read_only=True)
        return wb.sheetnames
    except Exception:
        return None
    finally:
        if wb:
            try:
                wb.close()
            except Exception:
                pass


def _get_excel_sheets_xls(filepath):
    wb = None
    try:
        import xlrd
        wb = xlrd.open_workbook(filepath)
        return [s.name for s in wb.sheets()]
    except Exception:
        return None
    finally:
        if wb:
            try:
                wb.release_resources()
            except Exception:
                pass


def get_excel_sheets(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".xlsx":
        return _get_excel_sheets_xlsx(filepath)
    if ext == ".xls":
        return _get_excel_sheets_xls(filepath)
    return None


def convert_office_to_pdf_com_silent(office_path, output_path, sheet_name=None, print_area=None):
    """MS Office COM 静默转换（Word/Excel/PowerPoint）。"""
    try:
        pythoncom.CoInitialize()
        ext = office_path.lower().split(".")[-1]
        abs_input = os.path.abspath(office_path)
        abs_output = os.path.abspath(output_path)
        out_dir = os.path.dirname(abs_output)
        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        if not os.path.exists(abs_input):
            raise RuntimeError("输入文件不存在: %s" % abs_input)

        if ext in ("doc", "docx"):
            word = comtypes.client.CreateObject("Word.Application")
            try:
                word.Visible = False
                word.DisplayAlerts = False
                word.EnableEvents = False
                doc = word.Documents.Open(abs_input, ReadOnly=True, Visible=False)
                try:
                    try:
                        doc.ExportAsFixedFormat(abs_output, 17)
                    except Exception:
                        try:
                            doc.SaveAs2(abs_output, FileFormat=17)
                        except Exception:
                            doc.SaveAs(abs_output, 17)
                finally:
                    try:
                        doc.Close(SaveChanges=False)
                    except Exception:
                        pass
            finally:
                try:
                    word.Quit()
                except Exception:
                    pass

        elif ext in ("xls", "xlsx"):
            excel = comtypes.client.CreateObject("Excel.Application")
            try:
                excel.Visible = False
                excel.DisplayAlerts = False
                excel.EnableEvents = False
                excel.ScreenUpdating = False
                wb = excel.Workbooks.Open(abs_input, ReadOnly=True)
                try:
                    if sheet_name:
                        try:
                            wb.Worksheets(sheet_name).Activate()
                        except Exception:
                            pass
                    if print_area:
                        try:
                            wb.ActiveSheet.PageSetup.PrintArea = print_area
                        except Exception:
                            pass
                    try:
                        wb.ExportAsFixedFormat(0, abs_output)
                    except Exception:
                        try:
                            wb.ActiveSheet.ExportAsFixedFormat(0, abs_output)
                        except Exception:
                            wb.SaveAs(abs_output, 57)
                finally:
                    try:
                        wb.Close(SaveChanges=False)
                    except Exception:
                        pass
            finally:
                try:
                    excel.Quit()
                except Exception:
                    pass

        elif ext in ("ppt", "pptx"):
            ppt = comtypes.client.CreateObject("PowerPoint.Application")
            try:
                try:
                    ppt.Visible = 0
                except Exception:
                    pass
                presentation = ppt.Presentations.Open(abs_input)
                try:
                    try:
                        presentation.ExportAsFixedFormat(abs_output, 2)
                    except Exception:
                        try:
                            presentation.SaveAs(abs_output, 32)
                        except Exception:
                            presentation.Export(abs_output, "PDF")
                finally:
                    try:
                        presentation.Close()
                    except Exception:
                        pass
            finally:
                try:
                    ppt.Quit()
                except Exception:
                    pass
        else:
            return False

        return os.path.exists(abs_output) and os.path.getsize(abs_output) > 0
    except Exception as e:
        safe_print("COM组件转换失败: %s" % e)
        return False
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


_WPS_WORD_CLSIDS = ["WPS.Application", "KWPS.Application"]
_WPS_EXCEL_CLSIDS = ["ET.Application", "KET.Application", "Excel.Application"]
_WPS_PPT_CLSIDS = ["WPP.Application", "KWPP.Application"]


def _try_create_wps_com(progids):
    for pid in progids:
        try:
            return comtypes.client.CreateObject(pid)
        except Exception:
            continue
    return None


def convert_office_to_pdf_wps(office_path, output_path, sheet_name=None, print_area=None):
    """WPS Office COM 静默转换。"""
    try:
        pythoncom.CoInitialize()
        ext = office_path.lower().split(".")[-1]
        abs_input = os.path.abspath(office_path)
        abs_output = os.path.abspath(output_path)
        out_dir = os.path.dirname(abs_output)
        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        if not os.path.exists(abs_input):
            raise RuntimeError("输入文件不存在: %s" % abs_input)

        if ext in ("doc", "docx"):
            word = _try_create_wps_com(_WPS_WORD_CLSIDS)
            if not word:
                return False
            try:
                word.Visible = False
                try:
                    word.DisplayAlerts = False
                except Exception:
                    pass
                try:
                    doc = word.Documents.Open(abs_input, ReadOnly=True, Visible=False)
                except Exception:
                    doc = word.Documents.Open(abs_input, ReadOnly=True)
                try:
                    try:
                        doc.ExportAsFixedFormat(abs_output, 17)
                    except Exception:
                        try:
                            doc.SaveAs(abs_output, 17)
                        except Exception:
                            doc.SaveAs(abs_output)
                finally:
                    try:
                        doc.Close(SaveChanges=False)
                    except Exception:
                        pass
            finally:
                try:
                    word.Quit()
                except Exception:
                    pass

        elif ext in ("xls", "xlsx"):
            excel = _try_create_wps_com(_WPS_EXCEL_CLSIDS)
            if not excel:
                return False
            try:
                excel.Visible = False
                try:
                    excel.DisplayAlerts = False
                except Exception:
                    pass
                try:
                    excel.ScreenUpdating = False
                except Exception:
                    pass
                try:
                    wb = excel.Workbooks.Open(abs_input, ReadOnly=True)
                except Exception:
                    wb = excel.Workbooks.Open(abs_input)
                try:
                    if sheet_name:
                        try:
                            wb.Worksheets(sheet_name).Activate()
                        except Exception:
                            pass
                    if print_area:
                        try:
                            wb.ActiveSheet.PageSetup.PrintArea = print_area
                        except Exception:
                            pass
                    try:
                        wb.ExportAsFixedFormat(0, abs_output)
                    except Exception:
                        try:
                            wb.ActiveSheet.ExportAsFixedFormat(0, abs_output)
                        except Exception:
                            for fmt in (57, 0, 6):
                                try:
                                    wb.SaveAs(abs_output, fmt)
                                    break
                                except Exception:
                                    continue
                finally:
                    try:
                        wb.Close(SaveChanges=False)
                    except Exception:
                        pass
            finally:
                try:
                    excel.Quit()
                except Exception:
                    pass

        elif ext in ("ppt", "pptx"):
            ppt = _try_create_wps_com(_WPS_PPT_CLSIDS)
            if not ppt:
                return False
            try:
                try:
                    ppt.Visible = 0
                except Exception:
                    pass
                try:
                    presentation = ppt.Presentations.Open(abs_input)
                except Exception:
                    presentation = ppt.Presentations.Open(abs_input, ReadOnly=True)
                try:
                    try:
                        presentation.ExportAsFixedFormat(abs_output, 2)
                    except Exception:
                        presentation.Export(abs_output, "PDF")
                finally:
                    try:
                        presentation.Close()
                    except Exception:
                        pass
            finally:
                try:
                    ppt.Quit()
                except Exception:
                    pass
        else:
            return False

        return os.path.exists(abs_output) and os.path.getsize(abs_output) > 0
    except Exception as e:
        safe_print("WPS COM 组件转换失败: %s" % e)
        return False
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def convert_office_to_pdf_libreoffice(office_path, output_path, sheet_name=None):
    """LibreOffice headless 转换（免费备选）。"""
    if not LIBREOFFICE_AVAILABLE:
        return False
    tmp_file = None
    try:
        abs_input = os.path.abspath(office_path)
        output_dir = os.path.abspath(os.path.dirname(output_path))
        os.makedirs(output_dir, exist_ok=True)

        convert_path = abs_input
        ext = os.path.splitext(abs_input)[1].lower()
        if sheet_name and ext == ".xlsx":
            wb_src = wb_new = None
            try:
                import openpyxl
                wb_src = openpyxl.load_workbook(abs_input)
                if sheet_name in wb_src.sheetnames:
                    wb_new = openpyxl.Workbook()
                    wb_new.remove(wb_new.active)
                    ws_src = wb_src[sheet_name]
                    ws_new = wb_new.create_sheet(title=sheet_name)
                    for row in ws_src.iter_rows():
                        for cell in row:
                            ws_new.cell(row=cell.row, column=cell.column, value=cell.value)
                    fd, tmp_file = tempfile.mkstemp(suffix=".xlsx")
                    os.close(fd)
                    wb_new.save(tmp_file)
                    convert_path = tmp_file
            except Exception as e:
                safe_print("提取工作表失败，使用整个文件: %s" % e)
            finally:
                for wb in (wb_src, wb_new):
                    if wb:
                        try:
                            wb.close()
                        except Exception:
                            pass

        cmd = [LIBREOFFICE_PATH, "--headless", "--convert-to", "pdf",
               "--outdir", output_dir, convert_path]
        subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        base_name = os.path.splitext(os.path.basename(convert_path))[0]
        lo_pdf = os.path.join(output_dir, base_name + ".pdf")
        if os.path.exists(lo_pdf):
            if os.path.abspath(lo_pdf) != os.path.abspath(output_path):
                shutil.move(lo_pdf, output_path)
            return True
        return False
    except subprocess.TimeoutExpired:
        safe_print("LibreOffice 转换超时")
        return False
    except Exception as e:
        safe_print("LibreOffice 转换失败: %s" % e)
        return False
    finally:
        if tmp_file and os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except Exception:
                pass


def _normalize_image_layout(layout):
    """规范化前端传来的图片排版参数。"""
    layout = layout if isinstance(layout, dict) else {}
    fit = layout.get("fit")
    fit = fit if fit in IMAGE_FITS else "fit"
    try:
        rotate = int(layout.get("rotate", 0)) % 360
    except (TypeError, ValueError):
        rotate = 0
    if rotate not in (0, 90, 180, 270):
        rotate = 0
    margin = _clamp(layout.get("margin", 5), 0, 30, 5)
    paper = _normalize_paper(layout.get("paper", "A4"))
    orientation = "landscape" if str(layout.get("page_orientation", "portrait")).lower() == "landscape" else "portrait"
    return {
        "fit": fit,
        "rotate": rotate,
        "margin_mm": margin,
        "paper": paper,
        "page_orientation": orientation,
    }


def convert_to_pdf(file_path, output_dir, sheet_name=None, print_area=None, image_layout=None):
    """把任意支持的文件转换为 PDF，返回 PDF 路径；失败返回 None。"""
    filename = os.path.basename(file_path)
    name, ext = os.path.splitext(filename)
    ext = ext.lower()
    clean_name = clean_filename(name)
    pdf_filename = clean_name + ".pdf"
    pdf_path = os.path.join(output_dir, pdf_filename)
    os.makedirs(output_dir, exist_ok=True)

    if ext == ".pdf":
        try:
            shutil.copy2(file_path, pdf_path)
            return pdf_path
        except Exception as e:
            safe_print("复制PDF文件失败: %s" % e)
            return None

    if ext.lstrip(".") in IMAGE_EXTS:
        try:
            build_image_pdf(file_path, pdf_path, **(image_layout or {}))
            return pdf_path
        except Exception as e:
            safe_print("图片转PDF失败: %s" % e)
            return None

    if ext.lstrip(".") in TEXT_EXTS:
        return pdf_path if convert_text_to_pdf(file_path, pdf_path) else None

    if ext.lstrip(".") in OFFICE_EXTS:
        def _try_office_chain():
            return convert_office_to_pdf_com_silent(file_path, pdf_path, sheet_name, print_area) if OFFICE_AVAILABLE else None

        def _try_wps_chain():
            return convert_office_to_pdf_wps(file_path, pdf_path, sheet_name, print_area) if WPS_AVAILABLE else None

        def _try_lo_chain():
            return convert_office_to_pdf_libreoffice(file_path, pdf_path, sheet_name) if LIBREOFFICE_AVAILABLE else None

        with _office_lock:
            for attempt, label in ((_try_office_chain, "Office COM"),
                                   (_try_wps_chain, "WPS"),
                                   (_try_lo_chain, "LibreOffice")):
                try:
                    if attempt():
                        return pdf_path
                except Exception as e:
                    safe_print("%s 转换异常: %s" % (label, e))
                if os.path.exists(pdf_path) and os.path.getsize(pdf_path) == 0:
                    try:
                        os.remove(pdf_path)
                    except Exception:
                        pass
        safe_print("无可用的转换引擎 (Office COM/WPS/LibreOffice): %s" % filename)
        return None

    return None


# ---------------------------------------------------------------------------
# 静默打印（GDI + DEVMODE）
# ---------------------------------------------------------------------------
if WIN32_AVAILABLE:
    DM_IN_BUFFER = win32con.DM_IN_BUFFER
    DM_OUT_BUFFER = win32con.DM_OUT_BUFFER
    DM_PAPERSIZE = win32con.DM_PAPERSIZE
    DM_PAPERLENGTH = win32con.DM_PAPERLENGTH
    DM_PAPERWIDTH = win32con.DM_PAPERWIDTH
    DM_DUPLEX = win32con.DM_DUPLEX
else:  # pragma: no cover
    DM_IN_BUFFER, DM_OUT_BUFFER = 0x8, 0x2
    DM_PAPERSIZE, DM_PAPERLENGTH, DM_PAPERWIDTH, DM_DUPLEX = 0x2, 0x4, 0x8, 0x1000


class _DEVMODEW(ctypes.Structure):
    """DEVMODEW（打印机部分），x64 下 sizeof == 220。"""
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


if WIN32_AVAILABLE:
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
    _gdi32.CreateDCW.argtypes = [wintypes.LPWSTR, wintypes.LPWSTR, wintypes.LPWSTR,
                                 ctypes.POINTER(_DEVMODEW)]
    _gdi32.CreateDCW.restype = wintypes.HDC
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _gdi32.DeleteDC.restype = wintypes.BOOL
    _winspool.DocumentPropertiesW.argtypes = [
        wintypes.HWND, wintypes.HANDLE, wintypes.LPWSTR,
        ctypes.POINTER(_DEVMODEW), ctypes.POINTER(_DEVMODEW), wintypes.DWORD,
    ]
    _winspool.DocumentPropertiesW.restype = ctypes.c_long
else:  # pragma: no cover
    _gdi32 = None
    _winspool = None


def enum_printers():
    if not WIN32_AVAILABLE:
        return []
    try:
        return [p[2] for p in win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
    except Exception:
        try:
            return [p[2] for p in win32print.EnumPrinters(2)]
        except Exception:
            return []


def get_default_printer():
    if not WIN32_AVAILABLE:
        return ""
    try:
        return win32print.GetDefaultPrinter()
    except Exception:
        printers = enum_printers()
        return printers[0] if printers else ""


def _make_devmode(printer_name, paper_size="A4", duplex=1):
    """构造带纸张/单双面设置的 DEVMODE。返回 (driver, port, devmode_ptr, buffer, warnings)。"""
    warnings = []
    h_printer = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(h_printer, 2)
        driver = info.get("pDriverName")
        port = info.get("pPortName")
        size = _winspool.DocumentPropertiesW(None, int(h_printer), printer_name, None, None, 0)
        if not size or size <= 0:
            raise RuntimeError("无法读取打印机 DEVMODE (size=%s)" % size)
        buf = ctypes.create_string_buffer(size)
        dm = ctypes.cast(buf, ctypes.POINTER(_DEVMODEW))
        rc = _winspool.DocumentPropertiesW(None, int(h_printer), printer_name, dm, None, DM_OUT_BUFFER)
        if rc is not None and rc < 0:
            raise RuntimeError("DocumentProperties 获取默认 DEVMODE 失败: %s" % rc)
        dm.contents.dmSize = ctypes.sizeof(_DEVMODEW) if dm.contents.dmSize == 0 else dm.contents.dmSize

        paper_size = _normalize_paper(paper_size)
        if paper_size in PAPER_STD_ID:
            dm.contents.dmPaperSize = PAPER_STD_ID[paper_size]
            dm.contents.dmFields |= DM_PAPERSIZE
        elif paper_size in PAPER_SIZES_MM:
            w_mm, h_mm = PAPER_SIZES_MM[paper_size]
            dm.contents.dmPaperSize = DMPAPER_USER
            dm.contents.dmPaperWidth = int(round(w_mm * 10))
            dm.contents.dmPaperLength = int(round(h_mm * 10))
            dm.contents.dmFields |= DM_PAPERSIZE | DM_PAPERWIDTH | DM_PAPERLENGTH
        if duplex in (2, 3):
            dm.contents.dmDuplex = duplex
            dm.contents.dmFields |= DM_DUPLEX
        rc2 = _winspool.DocumentPropertiesW(None, int(h_printer), printer_name, dm, dm,
                                           DM_IN_BUFFER | DM_OUT_BUFFER)
        if rc2 is not None and rc2 < 0:
            warnings.append("打印机不接受所选纸张/双面设置，回退为驱动默认值")
            rc3 = _winspool.DocumentPropertiesW(None, int(h_printer), printer_name, dm, None,
                                                DM_OUT_BUFFER)
            if rc3 is not None and rc3 < 0:
                raise RuntimeError("无法构造打印机 DEVMODE")
        return driver, port, dm, buf, warnings
    finally:
        try:
            win32print.ClosePrinter(h_printer)
        except Exception:
            pass


def _create_default_printer_dc(printer_name):
    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer_name)
    return dc


def _create_printer_dc(printer_name, paper_size="A4", duplex=1, force_default=False):
    """创建打印机 DC。

    - A4 + 单面（默认设置）：走与原版相同的 CreatePrinterDC 路径，稳定可靠；
    - 其它纸张或双面：使用自定义 DEVMODE 让设置真正生效，失败自动回退默认设置。

    返回 (dc, raw_hdc_or_None, warnings, used_devmode)
    """
    warnings = []
    need_devmode = (not force_default) and (paper_size != "A4" or duplex in (2, 3))
    if need_devmode:
        try:
            driver, port, dm, _buf, dm_warnings = _make_devmode(printer_name, paper_size, duplex)
            warnings.extend(dm_warnings)
            hdc = _gdi32.CreateDCW(driver, printer_name, port, dm)
            if hdc:
                return win32ui.CreateDCFromHandle(hdc), hdc, warnings, True
            warnings.append("使用所选纸张/双面创建打印上下文失败，回退为打印机默认设置")
        except Exception as e:
            warnings.append("打印机高级设置不可用（%s），使用默认设置" % e)

    dc = _create_default_printer_dc(printer_name)
    return dc, None, warnings, False


def _close_printer_dc(dc, raw_hdc):
    try:
        if raw_hdc:
            _gdi32.DeleteDC(raw_hdc)
        elif dc is not None:
            dc.DeleteDC()
    except Exception:
        pass


def _apply_print_orientation(img, orientation):
    if orientation == "landscape":
        return img.rotate(90, expand=True)
    if orientation == "reverse_landscape":
        return img.rotate(-90, expand=True)
    if orientation == "reverse_portrait":
        return img.rotate(180, expand=True)
    return img


def silent_print_pdf(pdf_path, printer_name, copies=1, duplex=1, orientation="portrait",
                     page_range=None, paper_size="A4", quality="normal"):
    """使用 Windows GDI 静默打印 PDF（无任何弹窗）。

    返回 dict: {"pages": 页数, "warnings": [...]}
    """
    if not PYMUPDF_AVAILABLE:
        raise RuntimeError("PyMuPDF未安装，无法使用静默打印")
    if not WIN32_AVAILABLE:
        raise RuntimeError("pywin32未安装，无法使用静默打印")
    if not os.path.exists(pdf_path):
        raise RuntimeError("PDF文件不存在: %s" % pdf_path)
    if not printer_name:
        raise RuntimeError("未选择打印机")
    available = enum_printers()
    if available and printer_name not in available:
        raise RuntimeError("打印机不存在或当前不可用: %s" % printer_name)

    try:
        copies = max(1, min(10, int(copies)))
    except (TypeError, ValueError):
        copies = 1
    try:
        duplex = int(duplex)
    except (TypeError, ValueError):
        duplex = 1
    if duplex not in (1, 2, 3):
        duplex = 1
    if orientation not in ORIENTATIONS:
        orientation = "portrait"
    paper_size = _normalize_paper(paper_size)
    quality = "high" if str(quality).lower() == "high" else "normal"
    dpi = PRINT_DPI_HIGH if quality == "high" else PRINT_DPI_NORMAL

    with _print_lock:
        try:
            doc = fitz.open(pdf_path)
        except Exception:
            raise RuntimeError("PDF文件无法打开")
        try:
            total_pages = len(doc)
            if total_pages <= 0:
                raise RuntimeError("PDF文件没有可打印页面")
            pages = parse_page_range(page_range, total_pages)

            warnings_all = []
            need_advanced = (paper_size != "A4" or duplex in (2, 3))
            attempts = [False, True] if need_advanced else [True]
            last_error = None
            for force_default in attempts:
                pages_done = 0
                dc = raw_hdc = None
                try:
                    dc, raw_hdc, warnings, used_devmode = _create_printer_dc(
                        printer_name, paper_size, duplex, force_default=force_default)
                    warnings_all.extend(warnings)

                    printable_w = dc.GetDeviceCaps(win32con.HORZRES)
                    printable_h = dc.GetDeviceCaps(win32con.VERTRES)
                    if printable_w <= 0 or printable_h <= 0:
                        printable_w = dc.GetDeviceCaps(win32con.PHYSICALWIDTH)
                        printable_h = dc.GetDeviceCaps(win32con.PHYSICALHEIGHT)
                    offset_x = dc.GetDeviceCaps(win32con.PHYSICALOFFSETX)
                    offset_y = dc.GetDeviceCaps(win32con.PHYSICALOFFSETY)
                    if printable_w <= 0 or printable_h <= 0:
                        raise RuntimeError("无法获取打印机可打印区域")
                    output_handle = dc.GetHandleOutput()

                    for _copy_index in range(copies):
                        dc.StartDoc("PrintServer")
                        try:
                            for page_index in pages:
                                dc.StartPage()
                                try:
                                    page = doc[page_index]
                                    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
                                    pix = page.get_pixmap(matrix=mat, alpha=False)
                                    img = PILImage.open(io.BytesIO(pix.tobytes("ppm")))
                                    img = _apply_print_orientation(img, orientation)
                                    img_w, img_h = img.size
                                    scale = min(printable_w / float(img_w), printable_h / float(img_h))
                                    draw_w = max(1, int(img_w * scale))
                                    draw_h = max(1, int(img_h * scale))
                                    x = (printable_w - draw_w) // 2 + offset_x
                                    y = (printable_h - draw_h) // 2 + offset_y
                                    dib = ImageWin.Dib(img)
                                    dib.draw(output_handle, (x, y, x + draw_w, y + draw_h))
                                finally:
                                    dc.EndPage()
                                pages_done += 1
                            dc.EndDoc()
                        except Exception:
                            try:
                                dc.AbortDoc()
                            except Exception:
                                pass
                            raise
                    return {"pages": len(pages), "warnings": warnings_all}
                except Exception as e:
                    last_error = e
                    # 自定义纸张/双面失败且还没输出任何页面时，回退默认设置重试一次
                    if force_default or pages_done > 0 or not used_devmode:
                        raise
                    warnings_all.append("所选纸张/双面设置打印失败，已回退默认设置重试: %s" % e)
                finally:
                    _close_printer_dc(dc, raw_hdc)
            if last_error:
                raise last_error
            raise RuntimeError("打印失败")
        finally:
            try:
                doc.close()
            except Exception:
                pass



# ---------------------------------------------------------------------------
# 打印日志
# ---------------------------------------------------------------------------
def log_print(filename, sid, printer, copies, duplex, papersize, quality, status):
    now = datetime.now()
    line = ("%s 打印: %s 打印机: %s 份数: %s 双面: %s 纸张: %s 质量: %s 状态: %s\n"
            % (now, filename, printer, copies, duplex, papersize, quality, status))
    record = {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "sid": sid,
        "name": filename,
        "printer": printer,
        "copies": copies,
        "duplex": duplex,
        "paper": papersize,
        "quality": quality,
        "status": status,
    }
    with _log_lock:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        try:
            with open(LOG_JSONL, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass


def read_logs(sid, limit=20, include_all=False):
    limit = max(1, min(200, int(limit or 20)))
    entries = []
    if not os.path.isfile(LOG_JSONL):
        return entries
    try:
        with open(LOG_JSONL, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-2000:]
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if include_all or record.get("sid") == sid:
                entries.append({
                    "time": record.get("time", ""),
                    "message": ("%s: %s" % (record.get("status", ""), record.get("name", ""))).strip(": "),
                    "status": record.get("status", ""),
                    "display_name": record.get("name", ""),
                })
                if len(entries) >= limit:
                    break
    except Exception:
        pass
    return entries


# ---------------------------------------------------------------------------
# 文件整理 / 清理
# ---------------------------------------------------------------------------
def list_session_files(sid):
    udir, pdir = session_dirs(sid)
    files = []
    if not os.path.isdir(udir):
        return files
    try:
        names = os.listdir(udir)
    except Exception:
        return files
    for name in names:
        upload_path = os.path.join(udir, name)
        if not os.path.isfile(upload_path):
            continue
        try:
            size = os.path.getsize(upload_path)
            mtime = os.path.getmtime(upload_path)
        except Exception:
            size, mtime = 0, time.time()
        kind = kind_of(name)
        pdf_name = pdf_name_for(name)
        pdf_path = os.path.join(pdir, pdf_name)
        converted = os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 0
        entry = {
            "name": name,
            "display_name": name,
            "size": size,
            "created_time": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": mtime,
            "kind": kind,
            "kind_icon": KIND_ICON.get(kind, "📁"),
            "converted": converted,
            "pdf_name": pdf_name if converted else None,
            "page_count": get_page_count(pdf_path) if converted else 0,
            "status": "已就绪" if converted else "转换失败",
            "status_color": "success" if converted else "danger",
        }
        files.append(entry)
    files.sort(key=lambda item: item.get("created_ts", 0), reverse=True)
    return files


def list_all_sessions():
    sessions = []
    if not os.path.isdir(UPLOAD_ROOT):
        return sessions
    try:
        sids = sorted(os.listdir(UPLOAD_ROOT))
    except Exception:
        return sessions
    for sid in sids:
        if not os.path.isdir(os.path.join(UPLOAD_ROOT, sid)):
            continue
        files = list_session_files(sid)
        if not files:
            continue
        sessions.append({"sid": sid, "files": files})
    return sessions


def delete_session_file(name, sid_param=None):
    sid, upload_path, pdf_path = resolve_owned_file(name, sid_param)
    deleted = []
    if os.path.isfile(upload_path):
        try:
            os.remove(upload_path)
            deleted.append("源文件")
        except Exception as e:
            raise RuntimeError("删除源文件失败: %s" % e)
    if os.path.isfile(pdf_path):
        try:
            os.remove(pdf_path)
            deleted.append("PDF")
        except Exception:
            pass
    return sid, deleted


def clean_old_files(folder, expire_seconds=FILE_EXPIRE_SECONDS):
    """定期清理过期文件/空会话目录。"""
    while True:
        now = time.time()
        try:
            if os.path.isdir(folder):
                for entry in os.listdir(folder):
                    path = os.path.join(folder, entry)
                    try:
                        if os.path.isdir(path):
                            for fname in os.listdir(path):
                                fpath = os.path.join(path, fname)
                                if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > expire_seconds:
                                    os.remove(fpath)
                            try:
                                os.rmdir(path)
                            except OSError:
                                pass
                        elif os.path.isfile(path):
                            if now - os.path.getmtime(path) > expire_seconds:
                                os.remove(path)
                    except Exception:
                        continue
        except Exception:
            pass
        time.sleep(CLEAN_INTERVAL_SECONDS)


def migrate_legacy_files():
    """把旧版本平铺在 uploads/pdfs 根目录的文件移入 _legacy 会话（仅管理员可见）。"""
    try:
        legacy_sid = "_legacy"
        moved = 0
        for root in (UPLOAD_ROOT, PDF_ROOT):
            if not os.path.isdir(root):
                continue
            flat_files = [e for e in os.listdir(root) if os.path.isfile(os.path.join(root, e))]
            if not flat_files:
                continue
            target = os.path.join(root, legacy_sid)
            os.makedirs(target, exist_ok=True)
            for fname in flat_files:
                try:
                    shutil.move(os.path.join(root, fname), os.path.join(target, fname))
                    moved += 1
                except Exception:
                    continue
        if moved:
            server_log("已迁移旧版文件 %s 个到 _legacy 会话" % moved)
    except Exception:
        pass


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.6)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


# ---------------------------------------------------------------------------
# Flask 应用
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder=STATIC_FOLDER, static_folder=STATIC_FOLDER,
            static_url_path="/static")


def _load_or_create_secret():
    try:
        if os.path.isfile(SECRET_FILE):
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                secret = f.read().strip()
            if len(secret) >= 32:
                return secret
    except Exception:
        pass
    secret = uuid.uuid4().hex + uuid.uuid4().hex
    try:
        ensure_dirs()
        with open(SECRET_FILE, "w", encoding="utf-8") as f:
            f.write(secret)
    except Exception:
        pass
    return secret


app.secret_key = _load_or_create_secret()
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
    SESSION_COOKIE_NAME="printserver_sid",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    TEMPLATES_AUTO_RELOAD=False,
    MAX_FORM_MEMORY_SIZE=MAX_UPLOAD_MB * 1024 * 1024,
)
try:
    app.json.ensure_ascii = False
except Exception:
    pass


@app.before_request
def _ensure_session_cookie():
    if request.path.startswith("/static/") or request.path == "/health":
        return None
    get_sid()
    return None


def ok(**payload):
    payload.setdefault("success", True)
    return jsonify(payload)


def fail(message, status=200, **payload):
    payload.update({"success": False, "message": message})
    return jsonify(payload), status


def _boot_data():
    printers = enum_printers()
    default_printer = get_default_printer()
    return {
        "version": VERSION,
        "sid": get_sid(),
        "is_admin": is_admin_request(),
        "server": {"ip": get_local_ip(), "port": PORT},
        "printers": printers,
        "default_printer": default_printer,
        "max_upload_mb": MAX_UPLOAD_MB,
        "env": {
            "pymupdf": PYMUPDF_AVAILABLE,
            "office": OFFICE_AVAILABLE,
            "wps": WPS_AVAILABLE,
            "libreoffice": LIBREOFFICE_AVAILABLE,
        },
        "paper_sizes": list(PAPER_SIZES_MM.keys()),
    }


# ---------- 页面 ----------
@app.route("/")
def index():
    try:
        boot = _boot_data()
        return render_template("index.html", boot=boot, max_upload_mb=MAX_UPLOAD_MB)
    except Exception:
        server_log("index 渲染失败:\n" + traceback.format_exc())
        raise


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": VERSION})


# ---------- 会话 / 环境 ----------
@app.route("/api/session")
def api_session():
    return ok(**_boot_data())


@app.route("/api/printers")
def api_printers():
    printers = enum_printers()
    return ok(printers=printers, default=get_default_printer())


# ---------- 文件列表 ----------
@app.route("/api/files")
def api_files():
    return ok(files=list_session_files(get_sid()))


@app.route("/api/files/all")
def api_files_all():
    if not is_admin_request():
        return fail("仅本机管理员可查看所有文件", 403)
    return ok(sessions=list_all_sessions())


# ---------- 上传 ----------
@app.route("/upload", methods=["POST"])
def upload_file():
    sid = get_sid()
    if "file" not in request.files:
        return fail("没有文件")
    file = request.files["file"]
    if not file or not file.filename:
        return fail("文件名为空")

    filename = clean_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if ext not in ALLOWED_EXT:
        return fail("不支持的文件类型：%s（支持 PDF/图片/文本/Office 文档）" % (ext or "无扩展名"))

    try:
        udir, pdir = session_dirs(sid, create=True)
        upload_path = os.path.join(udir, filename)
        # 同名文件覆盖：先删除旧 PDF
        try:
            old_pdf = os.path.join(pdir, pdf_name_for(filename))
            if os.path.isfile(old_pdf):
                os.remove(old_pdf)
        except Exception:
            pass
        file.save(upload_path)
    except Exception as e:
        server_log("保存上传文件失败:\n" + traceback.format_exc())
        return fail("保存文件失败: %s" % e)

    try:
        pdf_path = convert_to_pdf(upload_path, pdir)
    except Exception as e:
        server_log("转换失败:\n" + traceback.format_exc())
        pdf_path = None

    converted = bool(pdf_path and os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 0)
    kind = kind_of(filename)
    page_count = get_page_count(pdf_path) if converted else 0
    result = {
        "success": converted,
        "name": filename,
        "display_name": filename,
        "size": os.path.getsize(upload_path) if os.path.isfile(upload_path) else 0,
        "kind": kind,
        "kind_icon": KIND_ICON.get(kind, "📁"),
        "converted": converted,
        "pdf_name": pdf_name_for(filename) if converted else None,
        "page_count": page_count,
        "status": "已就绪" if converted else "转换失败",
        "status_color": "success" if converted else "danger",
    }
    if converted:
        result["message"] = "上传并转换成功"
    else:
        result["message"] = "文件已上传，但转换为 PDF 失败，暂时无法静默打印"
    return jsonify(result)


# ---------- 预览 ----------
def _text_preview_response(upload_path):
    content = _read_text_file(upload_path)
    if content is None:
        return Response("文件编码不支持或损坏", status=415, mimetype="text/plain")
    return Response(content, mimetype="text/plain; charset=utf-8")


@app.route("/preview/<name>")
def preview_file(name):
    sid_param = request.args.get("sid")
    try:
        _sid, upload_path, pdf_path = resolve_owned_file(name, sid_param)
    except FileAccessError as e:
        return Response(str(e), status=404, mimetype="text/plain; charset=utf-8")
    if os.path.isfile(pdf_path):
        return send_file(pdf_path, mimetype="application/pdf", as_attachment=False,
                         download_name=os.path.basename(pdf_path))
    if os.path.isfile(upload_path):
        kind = kind_of(upload_path)
        if kind == "image":
            return send_file(upload_path, as_attachment=False,
                             download_name=os.path.basename(upload_path))
        if kind == "pdf":
            return send_file(upload_path, mimetype="application/pdf", as_attachment=False,
                             download_name=os.path.basename(upload_path))
        if kind == "text":
            return _text_preview_response(upload_path)
    return Response("文件不存在或不支持预览", status=404, mimetype="text/plain; charset=utf-8")


@app.route("/api/render/<name>/<int:page>")
def api_render_page(name, page):
    if not PYMUPDF_AVAILABLE:
        return fail("PyMuPDF 未安装，无法预览")
    try:
        _sid, _upload_path, pdf_path = resolve_owned_file(name, request.args.get("sid"))
    except FileAccessError as e:
        return fail(str(e), 404)
    if not os.path.isfile(pdf_path):
        return fail("文件尚未转换为 PDF", 404)
    try:
        dpi = request.args.get("dpi", PREVIEW_DPI)
        try:
            dpi = int(dpi)
        except (TypeError, ValueError):
            dpi = PREVIEW_DPI
        png = render_pdf_page_png(pdf_path, page, dpi)
        resp = Response(png, mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        return fail("预览渲染失败: %s" % e, 500)


@app.route("/api/page_count/<name>")
def api_page_count(name):
    try:
        _sid, upload_path, pdf_path = resolve_owned_file(name, request.args.get("sid"))
    except FileAccessError as e:
        return fail(str(e), 404)
    if not os.path.isfile(pdf_path) and os.path.isfile(upload_path) and kind_of(upload_path) == "pdf":
        pdf_path = upload_path
    if not os.path.isfile(pdf_path):
        return fail("PDF文件不存在")
    count = get_page_count(pdf_path)
    if count <= 0:
        return fail("无法读取 PDF 页数")
    return ok(page_count=count, pdf_name=os.path.basename(pdf_path))


@app.route("/api/image_layout/<name>")
def api_image_layout(name):
    """图片版式实时预览：返回「实际打印效果」PNG。"""
    if not PYMUPDF_AVAILABLE:
        return fail("PyMuPDF 未安装，无法预览")
    try:
        _sid, upload_path, _pdf_path = resolve_owned_file(name, request.args.get("sid"))
    except FileAccessError as e:
        return fail(str(e), 404)
    if kind_of(upload_path) != "image":
        return fail("该文件不是图片，无法调整图片版式")
    layout = _normalize_image_layout({
        "fit": request.args.get("fit"),
        "rotate": request.args.get("rotate"),
        "margin": request.args.get("margin"),
        "paper": request.args.get("paper"),
        "page_orientation": request.args.get("page_orientation"),
    })
    try:
        dpi = request.args.get("dpi", PREVIEW_DPI)
        try:
            dpi = int(dpi)
        except (TypeError, ValueError):
            dpi = PREVIEW_DPI
        png = render_image_layout_png(upload_path, dpi=dpi, **layout)
        resp = Response(png, mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        server_log("图片版式预览失败:\n" + traceback.format_exc())
        return fail("图片预览失败: %s" % e, 500)


# ---------- Excel ----------
@app.route("/api/excel_info/<name>")
def api_excel_info(name):
    try:
        _sid, upload_path, _pdf_path = resolve_owned_file(name, request.args.get("sid"))
    except FileAccessError as e:
        return fail(str(e), 404)
    if kind_of(upload_path) != "sheet":
        return fail("该文件不是 Excel")
    sheets = get_excel_sheets(upload_path)
    if sheets is None:
        return fail("无法读取工作表（需安装 openpyxl 或 xlrd）")
    return ok(sheets=sheets, sheet_count=len(sheets))


@app.route("/api/excel_preview/<name>")
def api_excel_preview(name):
    try:
        _sid, upload_path, _pdf_path = resolve_owned_file(name, request.args.get("sid"))
    except FileAccessError as e:
        return fail(str(e), 404)
    if kind_of(upload_path) != "sheet":
        return fail("该文件不是 Excel")
    sheet_name = request.args.get("sheet", "")
    try:
        ext = os.path.splitext(upload_path)[1].lower()
        rows = []
        max_cols = 0
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(upload_path, read_only=True, data_only=True)
            try:
                ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
                for row in ws.iter_rows(max_row=100, max_col=26, values_only=True):
                    vals = [str(v) if v is not None else "" for v in row]
                    if any(vals):
                        rows.append(vals)
                        max_cols = max(max_cols, len(vals))
            finally:
                wb.close()
        elif ext == ".xls":
            import xlrd
            wb = xlrd.open_workbook(upload_path)
            try:
                ws = wb.sheet_by_name(sheet_name) if sheet_name else wb.sheet_by_index(0)
                for r in range(min(ws.nrows, 100)):
                    vals = [str(ws.cell_value(r, c)) if ws.cell_value(r, c) != "" else ""
                            for c in range(min(ws.ncols, 26))]
                    if any(vals):
                        rows.append(vals)
                        max_cols = max(max_cols, len(vals))
            finally:
                wb.release_resources()
        if not rows:
            rows = [["" for _ in range(5)] for _ in range(3)]
        cols = max(10, min(26, max_cols or 10))
        return ok(rows=rows, col_count=cols, headers=[chr(65 + i) for i in range(cols)])
    except Exception as e:
        return fail("读取表格失败: %s" % e)


# ---------- 打印 ----------
def _validate_print_request(data):
    """解析并校验打印参数（返回 dict；失败抛 ValueError）。"""
    printers = enum_printers()
    printer = str(data.get("printer") or "").strip()
    if not printer:
        printer = get_default_printer()
    if not printer:
        raise ValueError("没有可用的打印机")
    if printers and printer not in printers:
        raise ValueError("打印机不存在或当前不可用: %s" % printer)
    try:
        copies = int(data.get("copies", 1))
    except (TypeError, ValueError):
        copies = 1
    copies = max(1, min(10, copies))
    try:
        duplex = int(data.get("duplex", 1))
    except (TypeError, ValueError):
        duplex = 1
    if duplex not in (1, 2, 3):
        duplex = 1
    paper = _normalize_paper(data.get("paper_size", "A4"))
    orientation = data.get("orientation", "portrait")
    if orientation not in ORIENTATIONS:
        orientation = "portrait"
    quality = "high" if str(data.get("quality", "normal")).lower() == "high" else "normal"
    page_range = str(data.get("page_range") or "all")[:100]
    sheet_name = data.get("sheet_name")
    if sheet_name is not None:
        sheet_name = str(sheet_name)[:100] or None
    print_area = data.get("print_area")
    if print_area is not None:
        print_area = str(print_area).strip()[:100] or None
        if print_area and not re.match(r"^[A-Za-z]{1,3}\d{1,5}(:[A-Za-z]{1,3}\d{1,5})?(,[A-Za-z]{1,3}\d{1,5}(:[A-Za-z]{1,3}\d{1,5})?)*$", print_area):
            raise ValueError("打印区域格式不正确，例如 A1:F20")
    return {
        "printer": printer,
        "copies": copies,
        "duplex": duplex,
        "paper": paper,
        "orientation": orientation,
        "quality": quality,
        "page_range": page_range,
        "sheet_name": sheet_name,
        "print_area": print_area,
    }


@app.route("/print_single", methods=["POST"])
def print_single():
    data = request.get_json(silent=True) or {}
    sid = get_sid()
    name = data.get("name") or data.get("filename")
    if not name:
        return fail("缺少文件名")
    name = os.path.basename(clean_filename(name))
    try:
        _sid, upload_path, pdf_path = resolve_owned_file(name)
    except FileAccessError as e:
        return fail(str(e), 404)
    kind = kind_of(upload_path)

    try:
        params = _validate_print_request(data)
    except ValueError as e:
        log_print(name, sid, str(data.get("printer") or ""), data.get("copies", 1),
                  data.get("duplex", 1), data.get("paper_size", "A4"),
                  data.get("quality", "normal"), "失败: %s" % e)
        return fail(str(e))

    # 图片：按前端选择的版式重新生成 PDF（所见即所得）
    if kind == "image" and isinstance(data.get("image"), dict):
        layout = _normalize_image_layout(data["image"])
        try:
            build_image_pdf(upload_path, pdf_path, **layout)
        except Exception as e:
            return fail("按所选版式生成打印页失败: %s" % e)
        params["orientation"] = "landscape" if layout["page_orientation"] == "landscape" else "portrait"
        params["paper"] = layout["paper"]

    # Excel：选择工作表/打印区域时重新转换
    if kind == "sheet" and (params["sheet_name"] or params["print_area"]):
        if not os.path.isfile(upload_path):
            return fail("Excel 源文件不存在")
        try:
            new_pdf = convert_to_pdf(upload_path, os.path.dirname(pdf_path),
                                     params["sheet_name"], params["print_area"])
        except Exception as e:
            return fail("按选定工作表/区域转换PDF失败: %s" % e)
        if not new_pdf:
            return fail("按选定工作表/区域转换PDF失败（请检查转换引擎）")
        pdf_path = new_pdf

    if not os.path.isfile(pdf_path):
        return fail("文件尚未成功转换为PDF，无法静默打印")

    try:
        result = silent_print_pdf(
            pdf_path, params["printer"], params["copies"], params["duplex"],
            params["orientation"], params["page_range"], params["paper"], params["quality"])
    except Exception as e:
        message = "打印失败: %s" % e
        log_print(name, sid, params["printer"], params["copies"], params["duplex"],
                  params["paper"], params["quality"], "失败: %s" % e)
        return fail(message)

    log_print(name, sid, params["printer"], params["copies"], params["duplex"],
              params["paper"], params["quality"], "静默打印成功")
    return ok(message="打印成功", display_name=name, pages=result.get("pages", 0),
              warnings=result.get("warnings", []))


@app.route("/print_all", methods=["POST"])
def print_all():
    data = request.get_json(silent=True) or {}
    sid = get_sid()
    if not PYMUPDF_AVAILABLE:
        return fail("PyMuPDF未安装，无法静默打印")
    try:
        params = _validate_print_request(data)
    except ValueError as e:
        return fail(str(e))
    files = list_session_files(sid)
    printable = [f for f in files if f.get("converted")]
    if not printable:
        return fail("没有可打印的文件")
    printed = 0
    failed = 0
    for info in printable:
        pdf_path = os.path.join(PDF_ROOT, sid, info["pdf_name"])
        try:
            silent_print_pdf(pdf_path, params["printer"], params["copies"], params["duplex"],
                             params["orientation"], params["page_range"], params["paper"],
                             params["quality"])
            log_print(info["name"], sid, params["printer"], params["copies"], params["duplex"],
                      params["paper"], params["quality"], "静默批量打印成功")
            printed += 1
        except Exception as e:
            log_print(info["name"], sid, params["printer"], params["copies"], params["duplex"],
                      params["paper"], params["quality"], "静默批量打印失败: %s" % e)
            failed += 1
    if printed == 0:
        return fail("批量打印失败")
    message = "批量打印完成，成功 %d 个文件" % printed
    if failed:
        message += "，%d 个失败" % failed
    return ok(message=message, printed_count=printed, failed_count=failed)


# ---------- 删除 / 日志 / 管理 ----------
@app.route("/delete_file", methods=["POST"])
def delete_file():
    data = request.get_json(silent=True) or {}
    name = data.get("name") or data.get("filename")
    if not name:
        return fail("文件名不能为空")
    sid_param = data.get("sid")
    try:
        sid, deleted = delete_session_file(name, sid_param)
    except FileAccessError as e:
        return fail(str(e), 404)
    except Exception as e:
        return fail("删除失败: %s" % e)
    if not deleted:
        return fail("文件不存在")
    return ok(message="已删除: %s" % "、".join(deleted))


@app.route("/api/logs")
def api_logs():
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    admin = is_admin_request()
    return ok(logs=read_logs(get_sid(), limit, include_all=admin and request.args.get("all") == "1"),
              is_admin=admin)


@app.route("/api/admin/clean", methods=["POST"])
def api_admin_clean():
    if not is_admin_request():
        return fail("仅本机管理员可执行清理", 403)
    removed = 0
    for root in (UPLOAD_ROOT, PDF_ROOT, TEMP_ROOT):
        if not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            path = os.path.join(root, entry)
            try:
                if os.path.isdir(path):
                    removed += len(os.listdir(path))
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    removed += 1
                    os.remove(path)
            except Exception:
                continue
    with _log_lock:
        for log_path in (LOG_FILE, LOG_JSONL):
            try:
                if os.path.exists(log_path):
                    os.remove(log_path)
            except Exception:
                pass
    return ok(message="已清理所有会话文件", removed=removed)


@app.route("/api/qr")
def api_qr():
    if not is_admin_request():
        return fail("仅本机管理员可查看二维码", 403)
    if not QRCODE_AVAILABLE:
        return fail("未安装 qrcode 模块")
    url = "http://%s:%s/" % (get_local_ip(), PORT)
    try:
        qr = qrcode.QRCode(box_size=8, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        resp = Response(buf.getvalue(), mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-QR-URL"] = url
        return resp
    except Exception as e:
        return fail("生成二维码失败: %s" % e, 500)


# ---------- 错误处理 ----------
@app.errorhandler(413)
def _handle_413(_error):
    return fail("文件超过大小限制（最大 %d MB）" % MAX_UPLOAD_MB, 413)


@app.errorhandler(404)
def _handle_404(error):
    if request.path.startswith("/api/") or request.path in ("/upload", "/print_single",
                                                          "/print_all", "/delete_file"):
        return fail("接口不存在", 404)
    return error


@app.errorhandler(500)
def _handle_500(_error):
    server_log("服务器内部错误:\n" + traceback.format_exc())
    if request.path.startswith("/api/") or request.path in ("/upload", "/print_single",
                                                          "/print_all", "/delete_file"):
        return fail("服务器内部错误，请查看 server.log", 500)
    return "服务器内部错误", 500


@app.errorhandler(Exception)
def _handle_exception(error):
    if isinstance(error, HTTPException):
        return error
    server_log("未捕获异常:\n" + traceback.format_exc())
    if request.path.startswith("/api/") or request.path in ("/upload", "/print_single",
                                                          "/print_all", "/delete_file"):
        return fail("服务器内部错误: %s" % error, 500)
    return "服务器内部错误", 500


# ---------------------------------------------------------------------------
# 托盘 / 启动
# ---------------------------------------------------------------------------
def create_tray_image():
    image = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([10, 6, 54, 22], radius=3, outline=(30, 64, 175), width=3)
    draw.rounded_rectangle([6, 20, 58, 46], radius=6, fill=(219, 234, 254), outline=(30, 64, 175), width=3)
    draw.rounded_rectangle([18, 34, 46, 44], radius=2, fill=(37, 99, 235), outline=(30, 64, 175), width=2)
    draw.ellipse([14, 25, 20, 31], fill=(22, 163, 74))
    draw.line([16, 6, 16, 18], fill=(30, 64, 175), width=3)
    draw.line([48, 6, 48, 18], fill=(30, 64, 175), width=3)
    return image


def set_autostart(enable=True):
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    name = "PrintServerApp"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_ALL_ACCESS) as regkey:
        if enable:
            exe_path = '"%s"' % sys.executable
            winreg.SetValueEx(regkey, name, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(regkey, name)
            except FileNotFoundError:
                pass


def get_autostart():
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    name = "PrintServerApp"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as regkey:
            winreg.QueryValueEx(regkey, name)
            return True
    except Exception:
        return False


def _open_local_page(path="/"):
    try:
        webbrowser.open("http://127.0.0.1:%s%s" % (PORT, path))
    except Exception:
        pass


def _on_open_admin(icon, item):
    _open_local_page("/")


def _on_show_qr(icon, item):
    _open_local_page("/api/qr")


def _on_toggle_autostart(icon, item):
    try:
        set_autostart(not get_autostart())
    except Exception as e:
        server_log("切换开机自启失败: %s" % e)
    try:
        icon.update_menu()
    except Exception:
        pass


def _on_quit(icon, item):
    try:
        icon.stop()
    except Exception:
        pass
    server_log("用户从托盘退出")
    os._exit(0)


def build_tray_menu(icon):
    import pystray
    autostart = get_autostart()
    return pystray.Menu(
        pystray.MenuItem("打开管理页面", _on_open_admin, default=True),
        pystray.MenuItem("手机访问二维码", _on_show_qr),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("服务地址: %s:%s" % (get_local_ip(), PORT), None, enabled=False),
        pystray.MenuItem("开机自启：%s" % ("已开启" if autostart else "未开启"), _on_toggle_autostart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _on_quit),
    )


def setup_tray():
    import pystray
    image = create_tray_image()
    icon = pystray.Icon("PrintServer", image, "%s %s" % (APP_NAME, VERSION))
    icon.menu = build_tray_menu(icon)
    try:
        icon.notify("服务已启动，右键托盘图标可打开管理页面", APP_NAME)
    except Exception:
        pass
    icon.run()


def _show_message_box(text, title=APP_NAME, error=False):
    try:
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        safe_print(text)


def _port_available(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("" if host == "0.0.0.0" else host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def run_flask(host, port):
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


def _start_background_services(host, port):
    threading.Thread(target=clean_old_files, args=(UPLOAD_ROOT,), daemon=True,
                     name="cleaner-uploads").start()
    threading.Thread(target=clean_old_files, args=(PDF_ROOT,), daemon=True,
                     name="cleaner-pdfs").start()
    threading.Thread(target=run_flask, args=(host, port), daemon=True,
                     name="flask").start()


def print_startup_banner():
    safe_print("=" * 56)
    safe_print("%s v%s" % (APP_NAME, VERSION))
    safe_print("本机 IP: %s" % get_local_ip())
    safe_print("服务端口: %s" % PORT)
    safe_print("服务地址: http://%s:%s/" % (get_local_ip(), PORT))
    safe_print("-" * 56)
    safe_print("转换引擎:")
    safe_print("  PyMuPDF:      %s" % ("可用" if PYMUPDF_AVAILABLE else "未安装 - 打印不可用"))
    safe_print("  Office COM:   %s" % ("可用" if OFFICE_AVAILABLE else "未安装"))
    safe_print("  WPS Office:   %s" % ("可用" if WPS_AVAILABLE else "未安装"))
    safe_print("  LibreOffice:  %s" % ("可用" if LIBREOFFICE_AVAILABLE else "未安装"))
    safe_print("-" * 56)
    if not PYMUPDF_AVAILABLE:
        safe_print("警告：PyMuPDF 未安装，静默打印不可用！")
    safe_print("手机与电脑连接同一局域网后，浏览器访问上面的服务地址即可")
    safe_print("=" * 56)


def main():
    global PORT, HOST
    parser = argparse.ArgumentParser(description="%s v%s" % (APP_NAME, VERSION))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="服务端口（默认 5000）")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--no-tray", action="store_true", help="不创建托盘图标（调试用）")
    parser.add_argument("--open-browser", action="store_true", help="启动后自动打开管理页面")
    parser.add_argument("--version", action="version", version="%s %s" % (APP_NAME, VERSION))
    args = parser.parse_args()

    PORT = args.port
    HOST = args.host

    ensure_dirs()
    migrate_legacy_files()
    server_log("启动 %s v%s，端口 %s，数据目录 %s" % (APP_NAME, VERSION, PORT, DATA_DIR))
    print_startup_banner()

    if not _port_available(HOST, PORT):
        message = "端口 %s 已被占用，PrintServer 可能已经在运行。\n请先退出已运行的程序，或使用 --port 指定其他端口。" % PORT
        safe_print(message)
        server_log("端口占用: %s" % PORT)
        if not args.no_tray:
            _show_message_box(message, "%s - 启动失败" % APP_NAME, error=True)
        sys.exit(1)

    if args.no_tray:
        if args.open_browser:
            threading.Timer(1.2, lambda: _open_local_page("/")).start()
        _start_background_services(HOST, PORT)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        return

    _start_background_services(HOST, PORT)
    if args.open_browser:
        threading.Timer(1.5, lambda: _open_local_page("/")).start()
    try:
        setup_tray()
    except Exception:
        server_log("托盘图标初始化失败:\n" + traceback.format_exc())
        safe_print("托盘图标不可用，服务继续在后台运行。")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
