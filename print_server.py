import os
from flask import Flask, request, render_template, send_from_directory, redirect, url_for, jsonify
import win32print
import win32api
import win32ui
import win32con
import subprocess
from datetime import datetime
import threading
import sys
import ctypes
from ctypes import wintypes
import pystray
from PIL import Image, ImageDraw, ImageWin
import socket
import winreg
import time

from PIL import Image as PILImage
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.utils import ImageReader
import io
import tempfile

try:
    import fitz
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    print("警告: PyMuPDF未安装，静默打印功能受限。请运行: pip install PyMuPDF")

try:
    import comtypes.client
    import pythoncom
    OFFICE_AVAILABLE = True
except ImportError:
    OFFICE_AVAILABLE = False

def clean_old_files(folder=None, expire_seconds=3600):
    if folder is None:
        folder = UPLOAD_FOLDER
    while True:
        now = time.time()
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            if os.path.isfile(fpath):
                try:
                    if now - os.path.getmtime(fpath) > expire_seconds:
                        os.remove(fpath)
                except Exception:
                    pass
        time.sleep(600)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

def set_autostart(enable=True):
    exe_path = sys.executable
    key = r'Software\\Microsoft\\Windows\\CurrentVersion\\Run'
    name = 'PrintServerApp'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_ALL_ACCESS) as regkey:
        if enable:
            winreg.SetValueEx(regkey, name, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(regkey, name)
            except FileNotFoundError:
                pass

def get_autostart():
    key = r'Software\\Microsoft\\Windows\\CurrentVersion\\Run'
    name = 'PrintServerApp'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as regkey:
            val, _ = winreg.QueryValueEx(regkey, name)
            return True if val else False
    except FileNotFoundError:
        return False

def create_simple_printer_icon():
    image = Image.new('RGBA', (64, 64), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    
    draw.rectangle([14, 8, 50, 20], outline=(0, 0, 0), width=2)
    draw.rectangle([8, 20, 56, 42], fill=(240, 240, 240), outline=(0, 0, 0), width=2)
    draw.rectangle([16, 34, 48, 42], fill=(47, 136, 255), outline=(0, 0, 0), width=1)
    draw.rectangle([14, 26, 18, 28], fill=(0, 0, 0))
    draw.line([14, 8, 14, 20], fill=(0, 0, 0), width=2)
    draw.line([50, 8, 50, 20], fill=(0, 0, 0), width=2)
    
    return image

def parse_page_selection(mode, custom_range, total_pages):
    """
    根据模式返回需打印的 0 基页码列表。
    mode: 'all' | 'odd' | 'even' | 'custom'
    custom_range: 当 mode == 'custom' 时使用，如 '1-3,5,7-9'（用户输入为 1 基）
    """
    if total_pages <= 0:
        return []
    mode = (mode or 'all').lower()
    if mode == 'odd':
        # 1 基奇数页 1,3,5... -> 0 基 0,2,4...
        return list(range(0, total_pages, 2))
    if mode == 'even':
        # 1 基偶数页 2,4,6... -> 0 基 1,3,5...
        return list(range(1, total_pages, 2))
    if mode == 'custom':
        if not custom_range:
            return list(range(total_pages))
        # 严格模式：规范输入后再校验每段都在 [1, total_pages] 内，
        # 一旦发现任何越界页号就抛错，要求用户修正（不静默裁剪）。
        # 先收集 (lo, hi) 段，便于拼出精准的错误信息（"5-10" 而不是 "5,6,7,..."）。
        segments = []  # 每个 item: (lo, hi) 已 swap，1 基闭区间
        for part in str(custom_range).split(','):
            part = part.strip()
            if not part:
                continue
            if '-' in part:
                lo_s, hi_s = part.split('-', 1)
                try:
                    lo = int(lo_s.strip()); hi = int(hi_s.strip())
                except ValueError:
                    raise Exception(f"无法解析页码范围: {part}")
                if lo < 1 or hi < 1:
                    raise Exception(f"页码必须为正整数: {part}")
                if lo > hi:
                    lo, hi = hi, lo
                segments.append((lo, hi))
            else:
                try:
                    p = int(part)
                except ValueError:
                    raise Exception(f"无法解析页码: {part}")
                if p < 1:
                    raise Exception(f"页码必须为正整数: {part}")
                segments.append((p, p))
        # 全部解析成功后再统一校验越界（保证先发现格式错误再发现越界）
        for lo, hi in segments:
            if lo > total_pages or hi > total_pages:
                bad = f"{lo}-{hi}" if lo != hi else str(lo)
                raise Exception(
                    f"页码 {bad} 超出文件实际页数范围（共 {total_pages} 页）"
                )
        pages = set()
        for lo, hi in segments:
            for p in range(lo, hi + 1):
                pages.add(p - 1)
        return sorted(pages)
    return list(range(total_pages))


def page_selection_label(mode, custom_range):
    """返回日志/提示用的中文标签"""
    mode = (mode or 'all').lower()
    if mode == 'odd':
        return '仅奇数页'
    if mode == 'even':
        return '仅偶数页'
    if mode == 'custom':
        return f'自定义页({custom_range or "全部"})'
    return '全部页'


def silent_print_pdf(pdf_path, printer_name, copies=1, duplex=1, orientation=None, scale=None, paper_size=None, page_mode='all', page_range=None):
    if not PYMUPDF_AVAILABLE:
        raise Exception("PyMuPDF未安装，无法使用静默打印")
    
    if not os.path.exists(pdf_path):
        raise Exception(f"PDF文件不存在: {pdf_path}")
    
    # 用户缩放参数：转成百分比小数，None / 100 都视为 1.0（保持原 fit 尺寸）
    # 注意：不走 DEVMODE 的 dmScale（驱动行为不一致，对位图绘制经常被忽略），
    # 而是直接作用到 GDI 绘制的目标 rect 上 —— 这样 50% 就是真缩小一半。
    try:
        scale_pct = float(scale) if scale is not None else 100.0
    except (TypeError, ValueError):
        raise Exception(f"无效的 scale 值: {scale}")
    if not (10 <= scale_pct <= 200):
        raise Exception(f"scale 超出范围 [10, 200]: {scale_pct}")
    user_scale = scale_pct / 100.0
    
    try:
        # DEVMODE 只用来下发 duplex / orientation（dmScale 走应用层）
        devmode = None
        try:
            devmode = _build_print_devmode(printer_name,
                                           duplex=int(duplex) if duplex is not None else None,
                                           orientation=orientation,
                                           scale=None,
                                           paper_size=paper_size)
        except Exception as e:
            raise Exception(f"准备 DEVMODE 失败: {e}")
        
        pdf_doc = fitz.open(pdf_path)
        hprinter = win32print.OpenPrinter(printer_name)
        
        # 计算实际需要打印的页码（全部/奇数/偶数/自定义）
        page_indices = parse_page_selection(page_mode, page_range, len(pdf_doc))
        if not page_indices:
            raise Exception("没有满足条件的页面可打印，请检查自定义页码范围")
        
        try:
            printer_info = win32print.GetPrinter(hprinter, 2)
            hdc = win32ui.CreateDC()
            hdc.CreatePrinterDC(printer_name)
            
            # 用修改后的 DEVMODE 重置 DC（仅 duplex + orientation 走这里）
            if devmode is not None:
                try:
                    apply_devmode_to_dc(hdc, devmode)
                except Exception as e:
                    print(f"警告: 应用 DEVMODE 失败，将使用打印机默认设置: {e}")
            
            printable_area = hdc.GetDeviceCaps(win32con.HORZRES), hdc.GetDeviceCaps(win32con.VERTRES)
            printer_size = hdc.GetDeviceCaps(win32con.PHYSICALWIDTH), hdc.GetDeviceCaps(win32con.PHYSICALHEIGHT)
            printer_margins = hdc.GetDeviceCaps(win32con.PHYSICALOFFSETX), hdc.GetDeviceCaps(win32con.PHYSICALOFFSETY)
            
            for copy_num in range(copies):
                hdc.StartDoc("PDF Silent Print")
                
                for page_num in page_indices:
                    hdc.StartPage()
                    
                    page = pdf_doc[page_num]
                    # 渲染到 3000 DPI（PDF 默认 72 DPI，缩放系数 = 3000/72 ≈ 41.67）
                    render_scale = 648.0 / 72.0
                    mat = fitz.Matrix(render_scale, render_scale)
                    pix = page.get_pixmap(matrix=mat)
                    
                    img_data = pix.tobytes("ppm")
                    img = PILImage.open(io.BytesIO(img_data))
                    
                    img_width, img_height = img.size
                    # fit-to-page 基础比例（占满可打印区域，不加 0.9 富余）
                    fit_scale_x = printable_area[0] / img_width
                    fit_scale_y = printable_area[1] / img_height
                    fit_scale = min(fit_scale_x, fit_scale_y)
                    # 应用用户缩放：100% 时为 fit 尺寸，50% 缩小一半，200% 放大两倍
                    final_scale = fit_scale * user_scale
                    
                    scaled_width = int(img_width * final_scale)
                    scaled_height = int(img_height * final_scale)
                    # 注意：win32ui 的 PyDC 默认坐标原点 (0,0) 已经是"可打印区域的左上角"
                    # PHYSICALOFFSETX/Y 这层物理边距 driver 已在坐标变换里处理过，
                    # 这里再加 printer_margins 会把内容整体推到右下，导致右下溢出被裁切。
                    x = (printable_area[0] - scaled_width) // 2
                    y = (printable_area[1] - scaled_height) // 2
                    
                    if final_scale != 1.0:
                        img = img.resize((scaled_width, scaled_height), PILImage.Resampling.LANCZOS)
                    
                    dib = ImageWin.Dib(img)
                    dib.draw(hdc.GetHandleOutput(), (x, y, x + scaled_width, y + scaled_height))
                    
                    hdc.EndPage()
                
                hdc.EndDoc()
            
            return True
            
        finally:
            try:
                hdc.DeleteDC()
            except:
                pass
            win32print.ClosePrinter(hprinter)
            pdf_doc.close()
            
    except Exception as e:
        raise Exception(f"静默打印失败: {str(e)}")

def fallback_print_pdf(pdf_path, printer_name, copies=1):
    try:
        cmd = f'print /D:"{printer_name}" "{pdf_path}"'
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True
        else:
            raise Exception(f"命令行打印失败: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise Exception("打印命令超时")
    except Exception as e:
        raise Exception(f"备用打印方法失败: {str(e)}")

def convert_image_to_pdf(image_path, output_path, page_size=A4):
    try:
        img = PILImage.open(image_path)
        
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        
        c = canvas.Canvas(output_path, pagesize=page_size)
        page_width, page_height = page_size
        
        img_width, img_height = img.size
        ratio = min(page_width / img_width, page_height / img_height)
        
        scaled_width = img_width * ratio * 0.8
        scaled_height = img_height * ratio * 0.8
        x = (page_width - scaled_width) / 2
        y = (page_height - scaled_height) / 2
        
        c.drawImage(ImageReader(img), x, y, width=scaled_width, height=scaled_height)
        c.save()
        return True
    except Exception as e:
        print(f"图片转PDF失败: {e}")
        return False

def convert_text_to_pdf(text_path, output_path, page_size=A4):
    try:
        encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig']
        content = None
        
        for encoding in encodings:
            try:
                with open(text_path, 'r', encoding=encoding) as f:
                    content = f.read()
                break
            except UnicodeDecodeError:
                continue
        
        if content is None:
            print(f"无法读取文本文件: {text_path}")
            return False
        
        c = canvas.Canvas(output_path, pagesize=page_size)
        page_width, page_height = page_size
        
        font_registered = False
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            
            chinese_fonts = [
                ("SimSun", r"C:\Windows\Fonts\simsun.ttc"),
                ("SimHei", r"C:\Windows\Fonts\simhei.ttf"), 
                ("Microsoft-YaHei", r"C:\Windows\Fonts\msyh.ttc"),
                ("NSimSun", r"C:\Windows\Fonts\simsun.ttc")
            ]
            
            for font_name, font_path in chinese_fonts:
                try:
                    if os.path.exists(font_path):
                        pdfmetrics.registerFont(TTFont(font_name, font_path))
                        c.setFont(font_name, 10)
                        font_registered = True
                        break
                except Exception:
                    continue
        except ImportError:
            pass
        
        # 如果没有注册成功中文字体，使用Courier字体（等宽字体，对中文兼容性更好）
        if not font_registered:
            try:
                c.setFont("Courier", 9)
            except Exception:
                c.setFont("Helvetica", 10)
        
        lines = content.split('\n')
        y = page_height - 50
        line_height = 14 if font_registered else 12
        max_chars_per_line = 80 if not font_registered else 60
        
        for line in lines:
            if y < 50:
                c.showPage()
                if font_registered:
                    for font_name, font_path in chinese_fonts:
                        try:
                            if os.path.exists(font_path):
                                c.setFont(font_name, 10)
                                break
                        except:
                            continue
                else:
                    try:
                        c.setFont("Courier", 9)
                    except:
                        c.setFont("Helvetica", 10)
                y = page_height - 50
            
            if len(line) > max_chars_per_line:
                while len(line) > max_chars_per_line:
                    split_line = line[:max_chars_per_line]
                    try:
                        c.drawString(50, y, split_line)
                    except Exception:
                        c.drawString(50, y, "[无法显示的字符]")
                    y -= line_height
                    line = line[max_chars_per_line:]
                    if y < 50:
                        c.showPage()
                        if font_registered:
                            for font_name, font_path in chinese_fonts:
                                try:
                                    if os.path.exists(font_path):
                                        c.setFont(font_name, 10)
                                        break
                                except:
                                    continue
                        else:
                            try:
                                c.setFont("Courier", 9)
                            except:
                                c.setFont("Helvetica", 10)
                        y = page_height - 50
                
                if line:
                    try:
                        c.drawString(50, y, line)
                    except Exception:
                        c.drawString(50, y, "[无法显示的字符]")
                    y -= line_height
            else:
                try:
                    c.drawString(50, y, line)
                except Exception:
                    c.drawString(50, y, "[无法显示的字符]")
                y -= line_height
        
        c.save()
        return True
    except Exception as e:
        print(f"文本转PDF失败: {e}")
        return False
        
def convert_office_to_pdf_com_silent(office_path, output_path):
    try:
        pythoncom.CoInitialize()
        
        ext = office_path.lower().split('.')[-1]
        abs_office_path = os.path.abspath(office_path)
        abs_output_path = os.path.abspath(output_path)
        
        output_dir = os.path.dirname(abs_output_path)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        if not os.path.exists(abs_office_path):
            raise Exception(f"输入文件不存在: {abs_office_path}")
        
        if ext in ['doc', 'docx']:
            word = comtypes.client.CreateObject('Word.Application')
            word.Visible = False
            word.DisplayAlerts = False
            word.EnableEvents = False
            
            try:
                doc = word.Documents.Open(abs_office_path, ReadOnly=True, Visible=False)
                
                success = False
                try:
                    doc.ExportAsFixedFormat(abs_output_path, 17)
                    success = True
                except Exception:
                    try:
                        doc.SaveAs2(abs_output_path, FileFormat=17)
                        success = True
                    except Exception:
                        try:
                            doc.SaveAs(abs_output_path, 17)
                            success = True
                        except Exception:
                            pass
                
                doc.Close(SaveChanges=False)
                    
            finally:
                word.Quit()
                
        elif ext in ['xls', 'xlsx']:
            excel = comtypes.client.CreateObject('Excel.Application')
            excel.Visible = False
            excel.DisplayAlerts = False
            excel.EnableEvents = False
            excel.ScreenUpdating = False
            
            try:
                wb = excel.Workbooks.Open(abs_office_path, ReadOnly=True)
                
                success = False
                try:
                    wb.ExportAsFixedFormat(0, abs_output_path)
                    success = True
                except Exception:
                    try:
                        ws = wb.ActiveSheet
                        ws.ExportAsFixedFormat(0, abs_output_path)
                        success = True
                    except Exception:
                        try:
                            wb.SaveAs(abs_output_path, 57)
                            success = True
                        except Exception:
                            pass
                
                wb.Close(SaveChanges=False)
                    
            finally:
                excel.Quit()
                
        elif ext in ['ppt', 'pptx']:
            try:
                ppt = comtypes.client.CreateObject('PowerPoint.Application')
                
                try:
                    ppt.Visible = 0
                except Exception:
                    pass
                
                try:
                    presentation = ppt.Presentations.Open(abs_office_path)
                    
                    success = False
                    try:
                        presentation.ExportAsFixedFormat(abs_output_path, 2)
                        success = True
                    except Exception:
                        try:
                            presentation.SaveAs(abs_output_path, 32)
                            success = True
                        except Exception:
                            try:
                                presentation.Export(abs_output_path, "PDF")
                                success = True
                            except Exception:
                                try:
                                    presentation.SaveAs(abs_output_path)
                                    success = True
                                except Exception:
                                    pass
                    
                    try:
                        presentation.Close()
                    except Exception:
                        pass
                        
                except Exception as file_error:
                    print(f"PowerPoint文件处理失败: {file_error}")
                    return False
                    
            except Exception as ppt_error:
                print(f"PowerPoint COM对象创建失败: {ppt_error}")
                return False
            finally:
                try:
                    if 'ppt' in locals():
                        ppt.Quit()
                except Exception:
                    pass
                
        else:
            return False
            
        if os.path.exists(abs_output_path) and os.path.getsize(abs_output_path) > 0:
            return True
        else:
            return False
            
    except Exception as e:
        print(f"COM组件转换失败: {e}")
        return False
    finally:
        try:
            pythoncom.CoUninitialize()
        except:
            pass

def sanitize_filename(filename):
    import re
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = filename.strip('. ')
    return filename

# Office COM（Word/Excel/PowerPoint）是单例且非线程安全：
# - 同一进程内并发 CreateObject 会拿到同一个 Office 实例
# - 一个线程调 Word.Quit() 会扛断另一个线程正在处理的文档
# - Word 必时返回 0x800AC472 (Call was rejected by callee)
# 因此用一把进程内全局锁串行化所有 Office 转换。
# 图片/文本/PDF 复制走的是 PIL/reportlab/shutil，都是线程安全的，不需要这把锁。
_office_convert_lock = threading.Lock()

# --- Office 异步转换队列 ---
# 同步上传完成、把快任务（PDF/图片/文本）直接做完，docx/xlsx/pptx 入队由后台 worker
# 串行执行，HTTP /upload 立即返回让前端看到“排队中”状态。
# 状态字典 key = 上传存储文件名（如 '报告_1.docx'），value: 'queued' | 'converting' | 'done' | 'failed'
_conversion_status = {}
_conversion_status_lock = threading.Lock()
_office_convert_queue = __import__('queue').Queue()

def _set_conversion_status(key, status):
    """线程安全地更新转换状态字典。"""
    with _conversion_status_lock:
        _conversion_status[key] = status

def _get_conversion_status(key):
    """线程安全地读取转换状态；不存在表示已落盘完成或从未入队（同一回事）。"""
    with _conversion_status_lock:
        return _conversion_status.get(key)

def _conversion_worker():
    """
    后台单线程 daemon：从队列里取 Office 转换任务，串行执行。
    这样所有 Word/Excel/PPT 调用都跑在同一线程上，从根本上避免 COM 串扰，
    也天然取代了显式 _office_convert_lock。
    """
    import queue as _queue
    while True:
        try:
            task = _office_convert_queue.get(timeout=3600)
        except _queue.Empty:
            continue
        src_path, pdf_path, stored_name = task
        _set_conversion_status(stored_name, 'converting')
        try:
            ok = False
            try:
                ok = convert_office_to_pdf_com_silent(src_path, pdf_path)
            except Exception as e:
                print(f"Office转换异常 [{stored_name}]: {e}")
            if ok and os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                _set_conversion_status(stored_name, 'done')
            else:
                # 失败时仍在 uploads 区留源文件给清理线程兜底，状态标 failed 让前端红色提示
                _set_conversion_status(stored_name, 'failed')
                print(f"Office文件转换失败: {stored_name}")
        finally:
            _office_convert_queue.task_done()

def unique_path(target_dir, base_name, ext):
    """
    在 target_dir 下生一个不冲突的路径，并**原子地占位创建**。
    并发上传同名文件时：通过 os.open(O_CREAT|O_EXCL) 保证只有一方拿到该路径，
    另一方继续尝试下一个名字（_1/_2/…），从而彻底消除 TOCTOU 窗口。
    返回的路径此时已存在（长度 0 的占位文件），调用方随后用 file.save() 覆写。
    """
    clean = sanitize_filename(base_name)
    ext = ext.lstrip('.')
    os.makedirs(target_dir, exist_ok=True)
    # 第一轮：用纯名字 + _N 后缀，保持文件名可读
    candidates = [f"{clean}.{ext}"]
    candidates += [f"{clean}_{i}.{ext}" for i in range(1, 10000)]
    for candidate in candidates:
        path = os.path.join(target_dir, candidate)
        try:
            # O_CREAT|O_EXCL 在 Windows 上也是原子的：只有第一个调用成功
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return path
        except FileExistsError:
            continue
        except OSError:
            # 权限/盘符问题等：退回 UUID-based 名再试一轮
            break
    # 兜底：用一段随机 hex 避免极端情况下卡满 10000 次
    import uuid
    for _ in range(8):
        candidate = f"{clean}_{uuid.uuid4().hex[:8]}.{ext}"
        path = os.path.join(target_dir, candidate)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"无法为目标分配唯一文件名: {clean}.{ext}")

def convert_to_pdf(file_path, output_dir):
    """
    把上传文件转换成 PDF。
    返回 (pdf_path_or_None, queued_bool)：
      - pdf_path 不为 None：同步转换已完成（PDF/图片/文本）
      - queued=True：Office 任务已入队异步执行，前端应继续轮询状态
    Office 入队时，pdf_path 返回的是 worker 会写入的目标路径，本函数不阻塞等待。
    """
    filename = os.path.basename(file_path)
    name, ext = os.path.splitext(filename)
    ext = ext.lower()
    
    # 源文件名（original_filepath）已经经过 unique_path 保证唯一，
    # PDF 名直接沿用 源名前缀.pdf —— 与 uploads 区 1:1 对齐，
    # 这样 get_file_info 按上传文件名推出的 pdf 名一定能匹配到。
    clean_name = sanitize_filename(name)
    pdf_path = os.path.join(output_dir, f"{clean_name}.pdf")
    
    if ext == '.pdf':
        import shutil
        try:
            shutil.copy2(file_path, pdf_path)
            return pdf_path, False
        except Exception as e:
            print(f"复制PDF文件失败: {e}")
            return file_path, False
    
    if ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff']:
        if convert_image_to_pdf(file_path, pdf_path):
            return pdf_path, False
        return None, False
    
    if ext in ['.txt', '.log', '.md']:
        if convert_text_to_pdf(file_path, pdf_path):
            return pdf_path, False
        return None, False
    
    if ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']:
        if OFFICE_AVAILABLE:
            # 入队：worker 串行执行所有 Word/Excel/PPT，避免 COM 单例串扰。
            # 用 stored_name (源文件 basename) 作状态字典 key，与 get_file_info 对齐。
            _set_conversion_status(filename, 'queued')
            _office_convert_queue.put((file_path, pdf_path, filename))
            # 返回目标 pdf_path（虽然此时还没生成）+ queued=True，前端据此启动轮询
            return pdf_path, True
        else:
            print("Office COM组件不可用")
            return None, False
    
    return None, False
    
def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

STATIC_FOLDER = get_resource_path('static')
# 以 exe 所在目录为基准，避免开机自启时工作目录是 C:\Windows\system32 导致权限错误
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
PDF_FOLDER = os.path.join(BASE_DIR, 'pdfs')
LOG_FILE = os.path.join(BASE_DIR, 'print_log.txt')
DEFAULT_PRINTER_FILE = os.path.join(BASE_DIR, 'default_printer.json')

import json as _json

def get_default_printer():
    """读取用户设定的默认打印机名；不存在或已被卸载时返回 None。"""
    try:
        with open(DEFAULT_PRINTER_FILE, 'r', encoding='utf-8') as f:
            name = _json.load(f).get('printer')
        # 校验该打印机仍然存在于本机列表中
        if name and name in PRINTERS:
            return name
    except Exception:
        pass
    return None

def set_default_printer(printer_name):
    """写入默认打印机；传 None/空串则清空。"""
    if printer_name:
        with open(DEFAULT_PRINTER_FILE, 'w', encoding='utf-8') as f:
            _json.dump({'printer': printer_name}, f, ensure_ascii=False)
    else:
        try:
            os.remove(DEFAULT_PRINTER_FILE)
        except FileNotFoundError:
            pass

app = Flask(__name__, template_folder=STATIC_FOLDER, static_folder=STATIC_FOLDER)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PDF_FOLDER, exist_ok=True)
if not os.path.exists(STATIC_FOLDER):
    os.makedirs(STATIC_FOLDER, exist_ok=True)

PRINTERS = [p[2] for p in win32print.EnumPrinters(2)]

# --- Windows 打印参数支持的 ctypes 定义 ---
# pywin32 的 PyCDC 不支持 ResetDC，PyDEVMODE 也不能直接铺给 ctypes，
# 因此这里用 ctypes 直接定义 DEVMODEW 调用 winspool DocumentPropertiesW
# 和 gdi32 ResetDCW 来真正下发 duplex 等参数。

class _DEVMODE(ctypes.Structure):
    _fields_ = [
        ('dmDeviceName', ctypes.c_wchar * 32),
        ('dmSpecVersion', wintypes.WORD), ('dmDriverVersion', wintypes.WORD),
        ('dmSize', wintypes.WORD), ('dmDriverExtra', wintypes.WORD),
        ('dmFields', wintypes.DWORD),
        ('dmOrientation', wintypes.SHORT), ('dmPaperSize', wintypes.SHORT),
        ('dmPaperLength', wintypes.SHORT), ('dmPaperWidth', wintypes.SHORT),
        ('dmScale', wintypes.SHORT), ('dmCopies', wintypes.SHORT),
        ('dmDefaultSource', wintypes.SHORT), ('dmPrintQuality', wintypes.SHORT),
        ('dmColor', wintypes.SHORT), ('dmDuplex', wintypes.SHORT),
        ('dmYResolution', wintypes.SHORT), ('dmTTOption', wintypes.SHORT),
        ('dmCollate', wintypes.SHORT),
        ('dmFormName', ctypes.c_wchar * 32),
        ('dmLogPixels', wintypes.WORD), ('dmBitsPerPel', wintypes.DWORD),
        ('dmPelsWidth', wintypes.DWORD), ('dmPelsHeight', wintypes.DWORD),
        ('dmDisplayFlags', wintypes.DWORD), ('dmDisplayFrequency', wintypes.DWORD),
        ('dmICMMethod', wintypes.DWORD), ('dmICMIntent', wintypes.DWORD),
        ('dmMediaType', wintypes.DWORD), ('dmDitherType', wintypes.DWORD),
        ('dmReserved1', wintypes.DWORD), ('dmReserved2', wintypes.DWORD),
        ('dmPanningWidth', wintypes.DWORD), ('dmPanningHeight', wintypes.DWORD),
    ]

_winspool = ctypes.WinDLL('winspool.drv')
_gdi32 = ctypes.windll.gdi32
_winspool.DocumentPropertiesW.argtypes = [
    wintypes.HWND, wintypes.HANDLE, wintypes.LPCWSTR,
    ctypes.POINTER(_DEVMODE), ctypes.POINTER(_DEVMODE), wintypes.DWORD
]
_winspool.DocumentPropertiesW.restype = wintypes.LONG
_gdi32.ResetDCW.argtypes = [wintypes.HDC, ctypes.POINTER(_DEVMODE)]
_gdi32.ResetDCW.restype = wintypes.HDC

def get_printer_capabilities(printer_name):
    """
    查询打印机的硬件能力：双面打印、支持的纸张大小。
    返回 dict:
      {'duplex': bool,
       'paper_sizes': [{'id': int(=DMPAPER_*), 'name': str}, ...]}
    纸张大小通过 DeviceCapabilities 的 DC_PAPERNAMES(16) 和 DC_PAPERS(2) 一起查
    ——前者返回名字列表，后者按相同的顺序返回 DMPAPER_* id 列表，对齐后拼接成
    [{id, name}] 形式供前端下拉选择。
    """
    caps = {'duplex': False, 'paper_sizes': []}
    if not printer_name:
        return caps
    try:
        # DC_DUPLEX = 7，返回 1 表示支持双面，0 表示不支持
        # 注意 pywin32 第二参数 port 必须是字符串，None 会报 TypeError
        result = win32print.DeviceCapabilities(printer_name, '', 7)
        caps['duplex'] = (result == 1)
    except Exception as e:
        # 双面能力查询失败按不支持处理，避免让用户误以为可选双面后被忽略
        print(f"查询打印机双面能力失败 [{printer_name}]: {e}")
        caps['duplex'] = False
    try:
        # DC_PAPERNAMES(16) 返回 ['A4','Letter',...]
        # DC_PAPERS(2) 返回 [9,1,...] —— 对齐的 DMPAPER_* id 列表
        names = win32print.DeviceCapabilities(printer_name, '', 16) or []
        ids = win32print.DeviceCapabilities(printer_name, '', 2) or []
        paper_sizes = []
        n = min(len(names), len(ids))
        for i in range(n):
            nm = (names[i] or '').strip()
            if not nm:
                # 某些条目名字为空时跳过，避免给用户看到空白选项
                continue
            try:
                pid = int(ids[i])
            except (TypeError, ValueError):
                continue
            # 0/负值是非标纸张 id（用户自定义/换算尺寸），不放进下拉
            if pid > 0 and not any(p['id'] == pid for p in paper_sizes):
                paper_sizes.append({'id': pid, 'name': nm})
        caps['paper_sizes'] = paper_sizes
    except Exception as e:
        # 纸张能力查询失败不阻塞核心打印流程，前端会显示「无法读取纸张列表」提示
        print(f"查询打印机纸张能力失败 [{printer_name}]: {e}")
        caps['paper_sizes'] = []
    return caps

def _build_duplex_devmode(printer_name, duplex):
    """
    通过 winspool DocumentPropertiesW 获取打印机默认 DEVMODE，
    然后修改 dmDuplex 字段并重置 DM_DUPLEX 标志位。
    返回带 driver-extra 内存的 ctypes DEVMODE_FULL 对象。
    【已废弃】仅保留向后兼容；新代码请调用 _build_print_devmode。
    """
    return _build_print_devmode(printer_name, duplex=duplex)

def _build_print_devmode(printer_name, duplex=None, orientation=None, scale=None, paper_size=None):
    """
    统一的 DEVMODE 构造器，支持同时下发多项打印设置。
    参数（None 表示不改默认值）：
        duplex:       1=单面, 2=长边翻转(DMDUP_VERTICAL), 3=短边翻转(DMDUP_HORIZONTAL)
        orientation:  'portrait'|1=纵向, 'landscape'|2=横向
        scale:        int 10~200 表示缩放百分比（例如 100=原尺寸, 50=缩小一半）
        paper_size:   int DMPAPER_* id（如 9=A4, 1=Letter），来自 DeviceCapabilities(DC_PAPERS)
    返回带 driver-extra 内存的 ctypes DEVMODE_FULL 对象；如无任何修改返回 None。
    """
    duplex = int(duplex) if duplex is not None else None
    orientation_norm = None
    if orientation is not None:
        if isinstance(orientation, str):
            o = orientation.lower().strip()
            if o in ('portrait', '1', '纵向'):
                orientation_norm = win32con.DMORIENT_PORTRAIT
            elif o in ('landscape', '2', '横向'):
                orientation_norm = win32con.DMORIENT_LANDSCAPE
            else:
                raise Exception(f"无效的 orientation 值: {orientation}")
        else:
            oi = int(orientation)
            if oi == win32con.DMORIENT_PORTRAIT:
                orientation_norm = win32con.DMORIENT_PORTRAIT
            elif oi == win32con.DMORIENT_LANDSCAPE:
                orientation_norm = win32con.DMORIENT_LANDSCAPE
            else:
                raise Exception(f"无效的 orientation 值: {orientation}")
    scale_int = None
    if scale is not None:
        try:
            scale_int = int(scale)
        except (TypeError, ValueError):
            raise Exception(f"无效的 scale 值: {scale}")
        if not (10 <= scale_int <= 200):
            raise Exception(f"scale 超出范围 [10, 200]: {scale_int}")

    paper_int = None
    if paper_size is not None:
        try:
            paper_int = int(paper_size)
        except (TypeError, ValueError):
            raise Exception(f"无效的 paper_size 值: {paper_size}")
        if paper_int <= 0:
            # 0/负值视为「使用打印机默认纸张」
            paper_int = None

    # 没有任何修改时直接返回 None（用打印机默认设置）
    if duplex is None and orientation_norm is None and scale_int is None and paper_int is None:
        return None
    # 仅修改单面=默认值视为无修改
    if duplex == 1 and orientation_norm is None and scale_int is None and paper_int is None:
        return None

    duplex_mapping = {
        2: win32con.DMDUP_VERTICAL,    # 长边翻转
        3: win32con.DMDUP_HORIZONTAL,  # 短边翻转
    }
    if duplex is not None and duplex != 1 and duplex not in duplex_mapping:
        raise Exception(f"无效的 duplex 值: {duplex}")

    hprinter = None
    try:
        hprinter = win32print.OpenPrinter(printer_name)
        hp_int = int(hprinter)
        needed = _winspool.DocumentPropertiesW(None, hp_int, printer_name, None, None, 0)
        if needed <= 0:
            raise Exception("无法获取打印机 DEVMODE 大小")

        class _DEVMODE_FULL(_DEVMODE):
            _fields_ = [('_extra', ctypes.c_ubyte * (needed - ctypes.sizeof(_DEVMODE)))]

        dm = _DEVMODE_FULL()
        # DM_OUT_BUFFER = 2
        # 返回值：>0 是 info/warning（如某些字段被裁剪但仍可用），<0 才是错误，==0 是完美成功
        ret = _winspool.DocumentPropertiesW(None, hp_int, printer_name, ctypes.byref(dm), None, 2)
        if ret < 0:
            raise Exception(f"DocumentPropertiesW 失败: {ret}")

        if duplex is not None and duplex != 1:
            dm.dmDuplex = duplex_mapping[duplex]
            dm.dmFields |= win32con.DM_DUPLEX
        if orientation_norm is not None:
            dm.dmOrientation = orientation_norm
            dm.dmFields |= win32con.DM_ORIENTATION
        if scale_int is not None:
            # dmScale 是百分比：100=原大小，50=一半，200=两倍
            dm.dmScale = scale_int
            dm.dmFields |= win32con.DM_SCALE
        if paper_int is not None:
            # DM_PAPERSIZE = 2：告诉驱动 dmPaperSize 字段有效，进纸/纸张尺寸
            # 都按 DMPAPER_* id 处理（不要与 dmPaperWidth/Length 同时使用）
            dm.dmPaperSize = paper_int
            dm.dmFields |= win32con.DM_PAPERSIZE
        return dm
    finally:
        if hprinter is not None:
            try:
                win32print.ClosePrinter(hprinter)
            except Exception:
                pass

def apply_devmode_to_dc(hdc, devmode):
    """通过 gdi32.ResetDCW 把修改后的 DEVMODE 下发到 win32ui PyCDC。"""
    if devmode is None:
        return
    handle = hdc.GetHandleOutput()
    res = _gdi32.ResetDCW(handle, ctypes.byref(devmode))
    if not res:
        raise Exception("ResetDCW 失败：DEVMODE 应用失败")

ALLOWED_EXT = {
    'pdf', 'jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff',
    'txt', 'log', 'md',
    'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'
}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def log_print(filename, printer, copies, duplex, papersize, quality, status="成功", orientation=None, scale=None, page_mode=None, page_range=None):
    orient_str = orientation if orientation else '-'
    scale_str = scale if scale else '-'
    page_str = page_selection_label(page_mode, page_range)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now()} 打印: {filename} 打印机: {printer} 份数: {copies} 双面: {duplex} 页数: {page_str} 方向: {orient_str} 缩放: {scale_str}% 纸张: {papersize} 质量: {quality} 状态: {status}\n")

def get_logs():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        return f.readlines()[-10:][::-1]

def get_file_info():
    files = []
    upload_files = os.listdir(UPLOAD_FOLDER) if os.path.exists(UPLOAD_FOLDER) else []
    pdf_files = os.listdir(PDF_FOLDER) if os.path.exists(PDF_FOLDER) else []
    
    for f in upload_files:
        file_path = os.path.join(UPLOAD_FOLDER, f)
        file_info = {
            'name': f, 
            'status': '已上传', 
            'status_color': 'secondary',
            'size': os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            'created_time': datetime.fromtimestamp(os.path.getctime(file_path)).strftime('%Y-%m-%d %H:%M:%S') if os.path.exists(file_path) else ''
        }
        
        name, ext = os.path.splitext(f)
        pdf_name = f"{name}.pdf"
        if pdf_name in pdf_files:
            file_info['pdf_path'] = os.path.join(PDF_FOLDER, pdf_name)
            file_info['pdf_name'] = pdf_name
            file_info['status'] = '已转换为PDF'
            file_info['status_color'] = 'success'
        else:
            file_info['pdf_path'] = None
            file_info['pdf_name'] = None
            # Office 异步转换中：根据状态字典显示「排队中」/「转换中」/「转换失败」
            # _get_conversion_status 返回 None 表示该文件不是 Office 任务（或已完成被清理）
            status = _get_conversion_status(f)
            if status == 'queued':
                file_info['status'] = '排队中...'
                file_info['status_color'] = 'info'
            elif status == 'converting':
                file_info['status'] = '转换中...'
                file_info['status_color'] = 'warning'
            elif status == 'failed':
                file_info['status'] = '转换失败'
                file_info['status_color'] = 'danger'
            
        files.append(file_info)
    
    return files
    
@app.route('/')
def index():
    files = get_file_info()
    logs = get_logs()
    
    return render_template('index.html', 
                         printers=PRINTERS, 
                         default_printer=get_default_printer(),
                         files=files, 
                         logs=logs,
                         office_available=OFFICE_AVAILABLE,
                         pymupdf_available=PYMUPDF_AVAILABLE)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有文件'})
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': '文件名为空'})
    
    if not allowed_file(file.filename):
        return jsonify({'success': False, 'message': '文件类型不支持'})
    
    try:
        # 多用户同名文件防冲突：上传存储名也走 unique_path。
        # 拆出文件名/扩展名 → 生成 '报告.docx' 或 '报告_1.docx' 这样的唯一名。
        _bn = os.path.basename(file.filename)
        _nm, _ex = os.path.splitext(_bn)
        original_filepath = unique_path(UPLOAD_FOLDER, _nm, _ex)
        file.save(original_filepath)
        
        # 返回 (pdf_path, queued)；同步任务直接出结果，Office 任务入队异步处理
        pdf_path, queued = convert_to_pdf(original_filepath, PDF_FOLDER)
        
        # 返回给前端的 filename 用 stored_name（含序号），让前端后续轮询能匹配到
        stored_name = os.path.basename(original_filepath)
        result = {
            'success': True,
            # 注意：前端展示用 file.filename 还是 stored_name 取决于 UI 风格；
            # 这里返回 stored_name 让后续 refresh / print / delete 能精确定位文件。
            'filename': stored_name,
            'display_name': file.filename,
            'converted': bool(pdf_path) and not queued,
            'queued': queued,
            'message': '上传成功' if not queued else '上传成功，等待转换'
        }
        
        if pdf_path and not queued:
            result['pdf_name'] = os.path.basename(pdf_path)
            result['message'] = '上传并转换成功'
        elif queued:
            # 给前端一个明确的状态字符串，方便 uploadFile 直接渲染
            result['status'] = 'queued'
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'处理失败: {str(e)}'})

@app.route('/print_single', methods=['POST'])
def print_single():
    data = request.get_json()
    filename = data.get('filename')
    printer = data.get('printer')
    copies = data.get('copies', 1)
    duplex = data.get('duplex', 1)
    paper_size_raw = data.get('paper_size')
    try:
        paper_size = int(paper_size_raw) if paper_size_raw else None
    except (TypeError, ValueError):
        # 老前端可能仍发字符串 'A4'，无法识别为 DMPAPER id 时整体当作不改纸张
        print(f"忽略无法解析的 paper_size: {paper_size_raw}")
        paper_size = None
    paper_size_log = paper_size if paper_size else '默认'
    quality = data.get('quality', 'normal')
    page_mode = data.get('page_mode', 'all')
    page_range = data.get('page_range')
    
    # 前端拦截失败后的后端兑底校验：打印机不支持双面时禁止使用
    if int(duplex) != 1:
        caps = get_printer_capabilities(printer)
        if not caps.get('duplex', False):
            msg = f"打印机 [{printer}] 不支持双面打印，请选择单面"
            log_print(filename, printer, copies, duplex, paper_size_log, quality, msg, page_mode=page_mode, page_range=page_range)
            return jsonify({'success': False, 'message': msg})
    
    try:
        name, ext = os.path.splitext(filename)
        pdf_name = f"{name}.pdf"
        pdf_path = os.path.join(PDF_FOLDER, pdf_name)
        
        if not os.path.exists(pdf_path):
            return jsonify({'success': False, 'message': '文件未转换为PDF，无法静默打印'})
        
        try:
            silent_print_pdf(pdf_path, printer, copies, duplex, paper_size=paper_size, page_mode=page_mode, page_range=page_range)
            log_print(filename, printer, copies, duplex, paper_size_log, quality, "静默打印成功", page_mode=page_mode, page_range=page_range)
            return jsonify({'success': True, 'message': '静默打印成功'})
        except Exception as e:
            if PYMUPDF_AVAILABLE:
                error_msg = f"静默打印失败: {str(e)}"
            else:
                error_msg = "PyMuPDF未安装，无法静默打印"
            
            log_print(filename, printer, copies, duplex, paper_size, quality, error_msg, page_mode=page_mode, page_range=page_range)
            return jsonify({'success': False, 'message': error_msg})
            
    except Exception as e:
        error_msg = f"打印失败: {str(e)}"
        log_print(filename, printer, copies, duplex, paper_size, quality, error_msg, page_mode=page_mode, page_range=page_range)
        return jsonify({'success': False, 'message': error_msg})

@app.route('/print_all', methods=['POST'])
def print_all():
    data = request.get_json()
    printer = data.get('printer')
    copies = data.get('copies', 1)
    duplex = data.get('duplex', 1)
    paper_size_raw = data.get('paper_size')
    try:
        paper_size = int(paper_size_raw) if paper_size_raw else None
    except (TypeError, ValueError):
        print(f"忽略无法解析的 paper_size: {paper_size_raw}")
        paper_size = None
    paper_size_log = paper_size if paper_size else '默认'
    quality = data.get('quality', 'normal')
    orientation = data.get('orientation')
    scale = data.get('scale')
    page_mode = data.get('page_mode', 'all')
    page_range = data.get('page_range')
    
    # 后端兑底校验：打印机不支持双面时禁止使用
    if int(duplex) != 1:
        caps = get_printer_capabilities(printer)
        if not caps.get('duplex', False):
            msg = f"打印机 [{printer}] 不支持双面打印，请选择单面"
            return jsonify({'success': False, 'message': msg})
    
    if not PYMUPDF_AVAILABLE:
        return jsonify({'success': False, 'message': 'PyMuPDF未安装，无法静默打印'})
    
    try:
        files = get_file_info()
        printed_count = 0
        failed_count = 0
        
        for file_info in files:
            if file_info['pdf_path'] and os.path.exists(file_info['pdf_path']):
                try:
                    silent_print_pdf(file_info['pdf_path'], printer, copies, duplex, orientation=orientation, scale=scale, paper_size=paper_size, page_mode=page_mode, page_range=page_range)
                    log_print(file_info['name'], printer, copies, duplex, paper_size_log, quality, "静默批量打印成功", orientation, scale, page_mode=page_mode, page_range=page_range)
                    printed_count += 1
                except Exception as e:
                    log_print(file_info['name'], printer, copies, duplex, paper_size_log, quality, f"静默批量打印失败: {str(e)}", orientation, scale, page_mode=page_mode, page_range=page_range)
                    failed_count += 1
        
        if printed_count > 0:
            message = f'静默批量打印完成，成功打印 {printed_count} 个文件'
            if failed_count > 0:
                message += f'，{failed_count} 个文件打印失败'
            return jsonify({'success': True, 'message': message, 'printed_count': printed_count})
        else:
            return jsonify({'success': False, 'message': '没有可打印的PDF文件'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'静默批量打印失败: {str(e)}'})

@app.route('/api/files')
def get_files_api():
    return jsonify(get_file_info())

@app.route('/api/printer_capabilities')
def printer_capabilities_api():
    """返回所有本地打印机的能力信息，供前端根据硬件能力动态禁用不支持的选项。"""
    caps = {}
    for p in PRINTERS:
        caps[p] = get_printer_capabilities(p)
    return jsonify(caps)

@app.route('/api/default_printer', methods=['GET'])
def get_default_printer_api():
    return jsonify({'printer': get_default_printer()})

@app.route('/api/default_printer', methods=['POST'])
def set_default_printer_api():
    data = request.get_json(silent=True) or {}
    printer = data.get('printer')
    # 不存在或不为空时必须校验在列表内；空串/None 视为取消默认
    if printer and printer not in PRINTERS:
        return jsonify({'success': False, 'message': '该打印机不存在于本机列表中'}), 400
    try:
        set_default_printer(printer or None)
        return jsonify({'success': True, 'default_printer': printer or None})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/printer_capabilities/<path:printer_name>')
def printer_capabilities_one_api(printer_name):
    """查询单台打印机的能力（URL 安全地用 path 接收名字中可能存在的特殊字符）。"""
    return jsonify(get_printer_capabilities(printer_name))

@app.route('/preview/<filename>')
def preview_file(filename):
    pdf_path = os.path.join(PDF_FOLDER, filename)
    if os.path.exists(pdf_path):
        return send_from_directory(PDF_FOLDER, filename)
    
    upload_path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(upload_path):
        ext = filename.rsplit('.', 1)[1].lower()
        if ext in {'jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff'}:
            return send_from_directory(UPLOAD_FOLDER, filename)
        elif ext == 'pdf':
            return send_from_directory(UPLOAD_FOLDER, filename)
        elif ext in {'txt', 'log', 'md'}:
            encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig']
            content = None
            
            for encoding in encodings:
                try:
                    with open(upload_path, 'r', encoding=encoding) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue
            
            if content:
                return f'<pre style="padding: 20px; font-family: monospace;">{content}</pre>'
            else:
                return '文件编码不支持或损坏'
    
    return '文件不存在或不支持预览'

@app.route('/delete_file', methods=['POST'])
def delete_file():
    data = request.get_json()
    filename = data.get('filename')
    
    if not filename:
        return jsonify({'success': False, 'message': '文件名不能为空'})
    
    try:
        deleted_files = []
        
        original_path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(original_path):
            os.remove(original_path)
            deleted_files.append('源文件')
        
        # 清掉遗留的转换状态（避免下次同名上传读到过期的 queued/failed）
        with _conversion_status_lock:
            _conversion_status.pop(filename, None)
        
        name, ext = os.path.splitext(filename)
        pdf_filename = f"{name}.pdf"
        pdf_path = os.path.join(PDF_FOLDER, pdf_filename)
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
            deleted_files.append('PDF文件')
        
        if deleted_files:
            message = f"已删除: {', '.join(deleted_files)}"
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'message': '文件不存在'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'})
        
def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)

def on_quit(icon, item):
    icon.stop()
    os._exit(0)

def on_toggle_autostart(icon, item):
    current = get_autostart()
    set_autostart(not current)
    icon.menu = build_menu(icon)

def build_menu(icon):
    autostart = get_autostart()
    ip = get_local_ip()
    port = 5000
    return pystray.Menu(
        pystray.MenuItem(f'服务地址: {ip}:{port}', None, enabled=False),
        pystray.MenuItem('开机自启：' + ('已开启' if autostart else '未开启'), on_toggle_autostart),
        pystray.MenuItem('退出', on_quit)
    )

def setup_tray():
    image = create_simple_printer_icon()
    icon = pystray.Icon('print_server', image, '内网打印服务(静默版)')
    icon.menu = build_menu(icon)
    icon.run()

if __name__ == '__main__':
    print("正在启动内网打印服务...")
    print(f"本机IP: {get_local_ip()}")
    print(f"服务端口: 5000")
    print(f"转换库状态:")
    print(f"  PyMuPDF: {'可用' if PYMUPDF_AVAILABLE else '未安装 - 静默打印功能不可用'}")
    print(f"  Office COM: {'可用' if OFFICE_AVAILABLE else '未安装Office'}")
    
    if not PYMUPDF_AVAILABLE:
        print("\n⚠️  警告：PyMuPDF未安装，静默打印功能不可用！")
        print("请运行以下命令安装：pip install PyMuPDF")
    else:
        print("\n✅ 静默打印功能已就绪，使用Windows底层API，完全无界面弹出")
    
    print("支持拖拽上传，服务启动中...")
    
    cleaner_thread = threading.Thread(target=clean_old_files, daemon=True)
    cleaner_thread.start()
    
    pdf_cleaner_thread = threading.Thread(target=lambda: clean_old_files(PDF_FOLDER), daemon=True)
    pdf_cleaner_thread.start()
    
    # Office 转换 worker：永远单线程串行处理所有 docx/xlsx/pptx 任务
    office_worker_thread = threading.Thread(target=_conversion_worker, daemon=True)
    office_worker_thread.start()
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    setup_tray()
