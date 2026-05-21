# PrintServer

> 一个简单易用的内网打印解决方案，让您可以通过浏览器打印文档，解决一些电脑实在连不上打印机的问题

<img width="834" height="784" alt="image" src="https://github.com/Geekstrange/PrinterService/blob/master/imgs/1.png" />

原项目地址：https://github.com/a937750307/lan-printing

## ✨ 功能特点

- 🖨️ 支持内网静默打印（无弹窗）
- 📄 多种文件格式自动转换为PDF
- 🖱️ 支持文件拖拽上传
- 👀 文件预览功能
- 🗂️ 文件管理功能（预览/删除）
- 🔄 打印方向选择（纵向/横向/反向）
- 📑 打印范围选择（指定页码范围或可视点选）
- 📊 Excel工作表选择 + 打印区域可视拖拽选取
- 🆓 支持 LibreOffice 作为免费 Office 转换备选方案
- 📱 Web界面，支持任意设备访问

## 🚀 使用方法

### 服务端配置

1. **环境要求**
   - 能够正常连接打印机
   - 内网网络连接正常
   - Office 文档转换（任选其一）：
     - Microsoft Office（推荐，支持打印区域设置）
     - [WPS Office](https://www.wps.cn/)（国产，支持打印区域设置）
     - [LibreOffice](https://www.libreoffice.org/download/)（免费，支持工作表选择）
   - Python 依赖：`pip install flask pystray pywin32 Pillow reportlab PyMuPDF comtypes openpyxl xlrd`

2. **启动服务**
   - 运行本程序
   - 程序启动成功后，右下角会出现打印机图标
   - 程序运行过程中没有弹窗提示

<img width="216" height="84" alt="image" src="https://github.com/user-attachments/assets/0b2cb842-cbea-4456-867c-25f179cacc75" />


3. **查看服务地址**
   - 右键点击右下角的打印机图标
   - 在菜单中查看服务地址

<img width="310" height="119" alt="image" src="https://github.com/user-attachments/assets/de8287b5-9cc3-4456-95d5-e953af16b8fd" />


### 客户端使用

1. **访问打印服务**
   - 在需要打印的电脑上打开浏览器
   - 输入服务地址
   - 自动打开打印服务网页

<img width="832" height="70" alt="image" src="https://github.com/user-attachments/assets/05afcede-4c0f-41a1-a184-f0b761805590" />


2. **上传打印文件**
   - 支持拖拽文件到网页
   - 或点击上传按钮选择文件
   - 系统会自动转换为PDF格式

<img width="833" height="821" alt="image" src="https://github.com/Geekstrange/PrinterService/blob/master/imgs/1.png" />


3. **文件管理**
   - 预览转换后的PDF文件
   - 查看源文件
   - 删除不需要的文件

<img width="454" height="111" alt="image" src="https://github.com/Geekstrange/PrinterService/blob/master/imgs/2.png" />


## 📋 支持的文件格式

| 格式类型 | 支持格式 | 转换方式 |
|---------|---------|---------|
| 文档 | `.doc`, `.docx`, `.pdf` | 自动转换为PDF |
| 表格 | `.xls`, `.xlsx` | 自动转换为PDF，支持选择工作表和打印区域 |
| 演示 | `.ppt`, `.pptx` | 自动转换为PDF |
| 文本 | `.txt`, `.md`, `.log` | 自动转换为PDF |
| 图像 | `.jpg`, `.png`, `.bmp`, `.gif`, `.tiff` | 自动转换为PDF |

### 转换引擎对比

| 功能 | MS Office | WPS Office | LibreOffice |
|------|:---:|:---:|:---:|
| Word 转 PDF | ✓ | ✓ | ✓ |
| Excel 转 PDF | ✓ | ✓ | ✓ |
| PPT 转 PDF | ✓ | ✓ | ✓ |
| Excel 工作表选择 | ✓ | ✓ | ✓ |
| Excel 打印区域 (A1:F20) | ✓ | ✓ | ✗ |

## 🔄 更新日志

### v2.0

#### ✅ 新增功能
- **打印方向选择**：支持纵向/横向/反向横向/反向纵向，适用于标签纸等特殊场景
- **打印页码范围**：支持指定页码范围（如 `1-5`, `1,3,7-9`, `all`），弹窗内可视点选页码
- **Excel 工作表与打印区域**：
  - 自动读取工作表列表供选择
  - 支持打印区域坐标输入（如 `A1:F20`）
  - **表格可视选区**：弹窗内渲染 Excel 数据表格，鼠标拖拽即可选取打印区域，支持 Shift 扩展选区 / Ctrl 补选单元格
- **多引擎转换支持**：自动检测并使用 MS Office → WPS Office → LibreOffice 三级降级链，任一可用即可完成转换

#### 🔧 功能优化
- **文件页数自动检测**：打印弹窗中显示 PDF 页数，可视化页码按钮勾选
- **智能删除**：删除文件同时清理源文件与 PDF 文件
- **文件锁修复**：解决 Excel 文件因句柄泄漏导致无法删除的问题
- **离线运行**：打包所有样式文件，不依赖在线资源

## ❓ 常见问题

**Q: 程序启动后没有看到任何界面？**
A: 程序设计为后台运行，启动成功后会在系统托盘显示打印机图标。

**Q: 如何知道服务是否启动成功？**
A: 右下角出现打印机图标即表示服务启动成功。右键图标可查看服务地址和开机自启设置。

**Q: 支持哪些文件格式打印？**
A: 支持 Office 文档（Word/Excel/PowerPoint）、PDF、图片（JPG/PNG/BMP/GIF/TIFF）、文本（TXT/MD/LOG）。所有文件自动转换为 PDF 后静默打印。

**Q: 没有安装 Microsoft Office 能用吗？**
A: 可以。安装免费的 [LibreOffice](https://www.libreoffice.org/download/) 后，系统会自动检测并使用它作为备选转换引擎。注意：打印区域设置（如 A1:F20）仅 MS Office 支持。

**Q: Excel 如何只打印某个工作表或部分区域？**
A: 点击文件的「静默打印」→ 弹窗中选择工作表 → 可手动输入区域（如 `A1:F20`）或点击「可视选区域」用鼠标拖拽选取 → 确认打印。

**Q: 如何选择打印方向？**
A: 左侧打印设置中「打印方向」下拉可选择纵向/横向/反向横向/反向纵向。

**Q: PyMuPDF 未安装的警告影响使用吗？**
A: 影响。PyMuPDF（fitz）负责静默打印，未安装则无法打印。请执行 `pip install PyMuPDF`。
