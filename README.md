# 内网打印服务

> 一个简单易用的内网打印解决方案，让您可以通过浏览器打印文档，解决一些电脑实在连不上打印机的问题

<img width="834" height="784" alt="image" src="https://github.com/user-attachments/assets/d3702780-8377-4ded-8138-211254614f60" />

原项目地址：https://github.com/a937750307/lan-printing

## ✨ 功能特点

- 🖨️ 支持内网打印
- 📄 多种文件格式自动转换为PDF
- 🖱️ 支持文件拖拽上传
- 👀 文件预览功能
- 🗂️ 文件管理功能
- 📱 Web界面，支持任意设备访问

## 🚀 使用方法

### 服务端配置

1. **环境要求**
   - 安装了Office的电脑
   - 能够正常连接打印机
   - 内网网络连接正常

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

<img width="833" height="821" alt="image" src="https://github.com/user-attachments/assets/c257b36a-a9a0-4a48-8d13-f3e7d4ce7434" />


3. **文件管理**
   - 预览转换后的PDF文件
   - 查看源文件
   - 删除不需要的文件

<img width="454" height="111" alt="image" src="https://github.com/user-attachments/assets/af23f735-d15c-4f6d-ac31-af4cc4daef4c" />


## 📋 支持的文件格式

| 格式类型 | 支持格式 | 转换方式 |
|---------|---------|---------|
| 文档 | `.doc`, `.docx`, `.pdf` | 自动转换为PDF |
| 表格 | `.xls`, `.xlsx` | 自动转换为PDF |
| 演示 | `.ppt`, `.pptx` | 自动转换为PDF |
| 文本 | `.txt`, `.md` , `.log` | 自动转换为PDF |
| 图像 | `.jpg`, `.png` , `.gif` | 自动转换为PDF |

## 🔄 更新日志

### 最新版本更新内容

#### ✅ 新增功能
- **扩展文件格式支持**：新增多种文件格式支持，所有文件自动转换为PDF进行打印
- **拖拽上传**：修改操作逻辑，支持文件拖拽上传，操作更便捷
- **文件管理**：新增完整的文件操作功能
  - 📖 预览转换后的PDF文件
  - 📄 查看源文件
  - 🗑️ 删除文件功能

#### 🔧 功能优化
- **智能删除**：删除文件时会自动清理转换文件和源文件
- **日志管理**：删除操作不记录删除日志，但保留打印日志
- **界面优化**：去除logo，采用单文件打包
- **离线运行**：打包所有样式文件，不依赖在线资源

## ❓ 常见问题

**Q: 程序启动后没有看到任何界面？**
A: 程序设计为后台运行，启动成功后会在系统托盘显示打印机图标。

**Q: 如何知道服务是否启动成功？**
A: 右下角出现打印机图标即表示服务启动成功。

**Q: 支持哪些文件格式打印？**
A: 支持Office文档（Word、Excel、PowerPoint）和PDF文件，其他格式会自动转换为PDF。
