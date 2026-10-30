# -*- coding: utf-8 -*-
"""PrintServer v3 QA 测试共享工具。

只写入 tests/ 目录，不修改应用代码（print_server.py / static/）。
"""
import io
import os
import shutil
import socket
import subprocess
import sys
import time

import requests
from PIL import Image, ImageChops, ImageDraw

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
SERVER_SCRIPT = os.path.join(PROJECT_DIR, "print_server.py")
RUNTIME_DIR = os.path.join(TESTS_DIR, "_runtime")
SCREENSHOT_DIR = os.path.join(TESTS_DIR, "screenshots")

# 测试用独立端口（避开 Lead / 其他人的 5001/5002 实例）
ADMIN_PORT = 5011
NONADMIN_PORT = 5012
SMALL_PORT = 5013
E2E_PORT = 5001  # 端到端脚本优先使用 UI-SPEC 约定的 5001，被占用时自动换端口


# ---------------------------------------------------------------------------
# 目录 / 端口
# ---------------------------------------------------------------------------
def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def reset_dir(path):
    """删除并重建测试数据目录（只用于 tests/_runtime 下自己的目录）。"""
    path = os.path.abspath(path)
    if RUNTIME_DIR.lower() not in path.lower():
        raise RuntimeError("拒绝清理非 tests/_runtime 目录: %s" % path)
    shutil.rmtree(path, ignore_errors=True)
    return ensure_dir(path)


def port_free(port, host="127.0.0.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def pick_port(preferred):
    if port_free(preferred):
        return preferred
    for port in range(preferred + 10, preferred + 60):
        if port_free(port):
            return port
    raise RuntimeError("找不到可用端口（从 %s 起尝试 60 个）" % preferred)


# ---------------------------------------------------------------------------
# 服务生命周期
# ---------------------------------------------------------------------------
class Server(object):
    def __init__(self, proc, port, data_dir, stdout_path, log_file):
        self.proc = proc
        self.port = port
        self.data_dir = data_dir
        self.stdout_path = stdout_path
        self._log_file = log_file

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def request(self, method, path, **kwargs):
        kwargs.setdefault("timeout", 60)
        return requests.request(method, self.base + path, **kwargs)

    def log_tail(self, lines=80):
        try:
            with open(self.stdout_path, "r", encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-lines:])
        except Exception:
            return "(无法读取服务日志)"

    def stop(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=10)
        except Exception:
            pass
        try:
            self._log_file.close()
        except Exception:
            pass


def wait_health(base, timeout=60):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            r = requests.get(base + "/health", timeout=1)
            if r.status_code == 200 and r.json().get("status") == "ok":
                return True
        except Exception as e:  # noqa: BLE001 - 启动期连接错误属预期
            last_error = e
        time.sleep(0.25)
    raise RuntimeError("服务 %s 在 %ss 内未就绪: %s" % (base, timeout, last_error))


def start_server(port, data_dir, extra_env=None, timeout=60, reset=True):
    """启动一个隔离的 PrintServer 实例（数据目录在 tests/_runtime 下）。"""
    if reset:
        reset_dir(data_dir)
    else:
        ensure_dir(data_dir)
    env = os.environ.copy()
    env.pop("PRINTSERVER_ADMIN_DISABLED", None)
    env.update({
        "PRINTSERVER_DATA_DIR": os.path.abspath(data_dir),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})

    stdout_path = os.path.join(data_dir, "server_stdout.log")
    log_file = open(stdout_path, "wb")
    cmd = [sys.executable, SERVER_SCRIPT, "--port", str(port), "--no-tray"]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        cmd, cwd=PROJECT_DIR, env=env, stdout=log_file, stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    server = Server(proc, port, data_dir, stdout_path, log_file)
    try:
        wait_health(server.base, timeout=timeout)
    except Exception:
        server.stop()
        raise RuntimeError("启动测试服务失败（端口 %s）:\n%s" % (port, server.log_tail()))
    return server


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------
def make_image(path, size=(2000, 1000), color=(30, 120, 200), fmt=None):
    """生成纯色测试图片（非白，便于用 bbox 判断绘制区域）。"""
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    # 画一个角落色块，便于人工检查方向
    draw.rectangle([0, 0, max(1, size[0] // 10), max(1, size[1] // 10)], fill=(255, 0, 0))
    fmt = fmt or ("JPEG" if path.lower().endswith((".jpg", ".jpeg")) else "PNG")
    img.save(path, fmt, quality=92)
    return path


def make_pdf(path, pages=3):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(path, pagesize=A4)
    for i in range(pages):
        c.setFont("Helvetica-Bold", 28)
        c.drawString(80, 700, "PrintServer Test Page %d" % (i + 1))
        c.rect(80, 500, 300, 120, stroke=1, fill=0)
        c.setFont("Helvetica", 14)
        c.drawString(80, 470, "page %d / %d" % (i + 1, pages))
        c.showPage()
    c.save()
    return path


def make_txt(path, text="打印测试文本\n第二行：中文内容\nThird line\n"):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def make_xlsx(path, sheets=2):
    import openpyxl

    wb = openpyxl.Workbook()
    default = wb.active
    default.title = "Sheet1"
    for row in range(1, 6):
        for col in range(1, 5):
            default.cell(row=row, column=col, value="R%dC%d" % (row, col))
    for idx in range(2, sheets + 1):
        ws = wb.create_sheet("数据%d" % idx)
        for row in range(1, 4):
            ws.cell(row=row, column=1, value="编号%d" % row)
            ws.cell(row=row, column=2, value=row * 10)
    wb.save(path)
    return path


def make_docx(path, paragraphs=3):
    import docx

    document = docx.Document()
    document.add_heading("PrintServer 打印测试文档", level=1)
    for i in range(paragraphs):
        document.add_paragraph("这是第 %d 段测试文字，用于验证 DOCX 转换。" % (i + 1))
    document.save(path)
    return path


def make_big_file(path, size_bytes):
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"0" * max(0, size_bytes - 4))
    return path


# ---------------------------------------------------------------------------
# 上传 / 响应分析
# ---------------------------------------------------------------------------
def upload(sess, base, path, filename=None, timeout=300):
    filename = filename or os.path.basename(path)
    with open(path, "rb") as f:
        return sess.post(base + "/upload",
                         files={"file": (filename, f, "application/octet-stream")},
                         timeout=timeout)


def png_size(content):
    img = Image.open(io.BytesIO(content))
    return img.size


def content_bbox(content):
    """返回 (size, bbox) —— 相对纯白背景的非白区域 bbox（用于判断绘制区域）。"""
    img = Image.open(io.BytesIO(content)).convert("RGB")
    diff = ImageChops.difference(img, Image.new("RGB", img.size, (255, 255, 255)))
    return img.size, diff.getbbox()


def approx(actual, expected, tol):
    return abs(actual - expected) <= tol


def bbox_center(bbox):
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def wait_until(predicate, timeout=10, interval=0.2, message="条件未满足"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except Exception as e:  # noqa: BLE001 - 轮询期间异常也视为未满足
            last = e
        time.sleep(interval)
    raise AssertionError("%s（最后结果: %r）" % (message, last))
