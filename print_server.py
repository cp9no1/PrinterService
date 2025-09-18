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

def silent_print_pdf(pdf_path, printer_name, copies=1, duplex=1):
    if not PYMUPDF_AVAILABLE:
        raise Exception("PyMuPDF未安装，无法使用静默打印")
    
    if not os.path.exists(pdf_path):
        raise Exception(f"PDF文件不存在: {pdf_path}")
    
    try:
        pdf_doc = fitz.open(pdf_path)
        hprinter = win32print.OpenPrinter(printer_name)
        
        try:
            printer_info = win32print.GetPrinter(hprinter, 2)
            hdc = win32ui.CreateDC()
            hdc.CreatePrinterDC(printer_name)
            
            printable_area = hdc.GetDeviceCaps(win32con.HORZRES), hdc.GetDeviceCaps(win32con.VERTRES)
            printer_size = hdc.GetDeviceCaps(win32con.PHYSICALWIDTH), hdc.GetDeviceCaps(win32con.PHYSICALHEIGHT)
            printer_margins = hdc.GetDeviceCaps(win32con.PHYSICALOFFSETX), hdc.GetDeviceCaps(win32con.PHYSICALOFFSETY)
            
            for copy_num in range(copies):
                hdc.StartDoc("PDF Silent Print")
                
                for page_num in range(len(pdf_doc)):
                    hdc.StartPage()
                    
                    page = pdf_doc[page_num]
                    mat = fitz.Matrix(2.0, 2.0)
                    pix = page.get_pixmap(matrix=mat)
                    
                    img_data = pix.tobytes("ppm")
                    img = PILImage.open(io.BytesIO(img_data))
                    
                    img_width, img_height = img.size
                    scale_x = printable_area[0] / img_width
                    scale_y = printable_area[1] / img_height
                    scale = min(scale_x, scale_y) * 0.9
                    
                    scaled_width = int(img_width * scale)
                    scaled_height = int(img_height * scale)
                    x = (printable_area[0] - scaled_width) // 2 + printer_margins[0]
                    y = (printable_area[1] - scaled_height) // 2 + printer_margins[1]
                    
                    if scale != 1.0:
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

def convert_to_pdf(file_path, output_dir):
    filename = os.path.basename(file_path)
    name, ext = os.path.splitext(filename)
    ext = ext.lower()
    
    clean_name = sanitize_filename(name)
    pdf_filename = f"{clean_name}.pdf"
    pdf_path = os.path.join(output_dir, pdf_filename)
    
    if ext == '.pdf':
        import shutil
        try:
            shutil.copy2(file_path, pdf_path)
            return pdf_path
        except Exception as e:
            print(f"复制PDF文件失败: {e}")
            return file_path
    
    if ext in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff']:
        if convert_image_to_pdf(file_path, pdf_path):
            return pdf_path
    
    elif ext in ['.txt', '.log', '.md']:
        if convert_text_to_pdf(file_path, pdf_path):
            return pdf_path
    
    elif ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']:
        if OFFICE_AVAILABLE:
            if convert_office_to_pdf_com_silent(file_path, pdf_path):
                return pdf_path
            else:
                print(f"Office文件转换失败: {filename}")
        else:
            print("Office COM组件不可用")
    
    return None
    
def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

STATIC_FOLDER = get_resource_path('static')
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
PDF_FOLDER = os.path.join(os.getcwd(), 'pdfs')
LOG_FILE = os.path.join(os.getcwd(), 'print_log.txt')

app = Flask(__name__, template_folder=STATIC_FOLDER, static_folder=STATIC_FOLDER)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PDF_FOLDER, exist_ok=True)
if not os.path.exists(STATIC_FOLDER):
    os.makedirs(STATIC_FOLDER, exist_ok=True)

PRINTERS = [p[2] for p in win32print.EnumPrinters(2)]

ALLOWED_EXT = {
    'pdf', 'jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff',
    'txt', 'log', 'md',
    'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'
}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def log_print(filename, printer, copies, duplex, papersize, quality, status="成功"):
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now()} 打印: {filename} 打印机: {printer} 份数: {copies} 双面: {duplex} 纸张: {papersize} 质量: {quality} 状态: {status}\n")

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
            
        files.append(file_info)
    
    return files
    
@app.route('/')
def index():
    files = get_file_info()
    logs = get_logs()
    
    return render_template('index.html', 
                         printers=PRINTERS, 
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
        original_filepath = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(original_filepath)
        
        pdf_path = convert_to_pdf(original_filepath, PDF_FOLDER)
        
        result = {
            'success': True,
            'filename': file.filename,
            'converted': pdf_path is not None,
            'message': '上传成功'
        }
        
        if pdf_path:
            result['pdf_name'] = os.path.basename(pdf_path)
            result['message'] = '上传并转换成功'
        
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
    paper_size = data.get('paper_size', 'A4')
    quality = data.get('quality', 'normal')
    
    try:
        name, ext = os.path.splitext(filename)
        pdf_name = f"{name}.pdf"
        pdf_path = os.path.join(PDF_FOLDER, pdf_name)
        
        if not os.path.exists(pdf_path):
            return jsonify({'success': False, 'message': '文件未转换为PDF，无法静默打印'})
        
        try:
            silent_print_pdf(pdf_path, printer, copies, duplex)
            log_print(filename, printer, copies, duplex, paper_size, quality, "静默打印成功")
            return jsonify({'success': True, 'message': '静默打印成功'})
        except Exception as e:
            if PYMUPDF_AVAILABLE:
                error_msg = f"静默打印失败: {str(e)}"
            else:
                error_msg = "PyMuPDF未安装，无法静默打印"
            
            log_print(filename, printer, copies, duplex, paper_size, quality, error_msg)
            return jsonify({'success': False, 'message': error_msg})
            
    except Exception as e:
        error_msg = f"打印失败: {str(e)}"
        log_print(filename, printer, copies, duplex, paper_size, quality, error_msg)
        return jsonify({'success': False, 'message': error_msg})

@app.route('/print_all', methods=['POST'])
def print_all():
    data = request.get_json()
    printer = data.get('printer')
    copies = data.get('copies', 1)
    duplex = data.get('duplex', 1)
    paper_size = data.get('paper_size', 'A4')
    quality = data.get('quality', 'normal')
    
    if not PYMUPDF_AVAILABLE:
        return jsonify({'success': False, 'message': 'PyMuPDF未安装，无法静默打印'})
    
    try:
        files = get_file_info()
        printed_count = 0
        failed_count = 0
        
        for file_info in files:
            if file_info['pdf_path'] and os.path.exists(file_info['pdf_path']):
                try:
                    silent_print_pdf(file_info['pdf_path'], printer, copies, duplex)
                    log_print(file_info['name'], printer, copies, duplex, paper_size, quality, "静默批量打印成功")
                    printed_count += 1
                except Exception as e:
                    log_print(file_info['name'], printer, copies, duplex, paper_size, quality, f"静默批量打印失败: {str(e)}")
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
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    setup_tray()
