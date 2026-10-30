# PrintServer v3（优化版）前端开发规格

> 本文件是前端（static/）与后端（print_server.py）之间的接口契约 + 界面设计要求。
> 后端由 Lead 实现，前端由 ui-dev 实现。**接口字段以本文件为准**；若必须调整，先与 Lead 沟通并同步更新本文件。

## 1. 目标

把原来的单页工具式界面，改造成 **手机优先、步骤引导清晰（1 上传 → 2 预览调整 → 3 打印）** 的向导式界面，同时解决：

1. **图片打印太小 / 网页里无法缩放**：步骤 2 提供所见即所得的 A4 页面预览，可双指/滚轮缩放查看，并可选择 适应页面 / 铺满页面 / 原始大小、旋转、边距、纸张和方向；打印出来的页面与预览一致，图片默认铺满到接近整页。
2. **隐私**：每个浏览器会话只能看到自己上传的文件与打印记录；只有服务器本机（localhost）打开时才是管理员视图，能看到所有会话文件。
3. **引导性强**：明确的 3 步流程、每步说明、上一步/下一步按钮、空状态引导、错误提示。

## 2. 文件与写入范围（严格遵守）

只允许新建/修改以下文件：

- `static/index.html` —— Jinja2 模板（保留 Jinja 变量注入，见 §5）
- `static/app.css` —— 全部样式（自己写，**不要依赖 Bootstrap**）
- `static/app.js` —— 全部前端逻辑（原生 JS，ES2020，不使用构建工具）
- `static/favicon.ico` —— 不改
- `static/bootstrap.*` —— **可以删除**（不再使用）；index.html 中不得再引用

不要改 `print_server.py`、`PrinterService.spec`、`docs/`。不要新建其他 static 资源文件（图片/字体/第三方 JS 都不要，图标用 inline SVG 或 emoji）。

## 3. 视觉/交互设计方向

- 中文界面。产品名：**内网打印服务**（副标题 `PrintServer`）。
- 手机优先响应式：≥992px 双栏（主操作区 + 侧栏/预览），<992px 单栏；所有触控目标 ≥44px；`viewport` 已设，输入框/按钮在 iOS 上字号 ≥16px 防止自动缩放。
- 配色：浅色背景 `#f5f7fb`，卡片白色、圆角 14–18px、轻阴影；主色蓝 `#2563eb`（渐变 `#2563eb → #4f46e5` 可用于主按钮/步骤条），成功绿 `#16a34a`，警告橙 `#d97706`，危险红 `#dc2626`；正文 `#111827`，次要 `#6b7280`。
- 字体：`-apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif`。
- 顶部：产品名 + 状态点（服务在线，绿色）+ 服务地址 chip（如 `192.168.1.5:5000`）；管理员模式显示「本机管理员」徽标。
- **步骤条（核心）**：横向 3 个圆点+标题：`1 上传文件` → `2 预览调整` → `3 打印设置`。已有文件/可进入的步骤可点击回退；未解锁步骤置灰并显示原因 tooltip。步骤条在移动端可压缩为「第 1 步 / 共 3 步」+ 进度点，但必须始终可见。
- 每步顶部有简短引导语（1 句话），例如：
  - 1：「把要打印的文件拖进来，或用手机直接拍照/从相册选择」
  - 2：「检查页面效果：可缩放、旋转；图片可选铺满整页，避免打印太小」
  - 3：「选择打印机和参数，然后开始打印」
- 反馈：toast（成功/失败/提示，3–5 秒自动消失）；按钮 loading 态；空状态插图（inline SVG）。
- 所有用户数据渲染用 `textContent` / DOM API，禁止把文件名等用户数据拼进 innerHTML（防 XSS）。

## 4. 三步流程细节

### 步骤 1：上传文件

- 大号拖拽区（桌面支持 dragover/drop；手机点击打开文件选择）：
  - 主按钮「选择文件」；手机额外显示「拍照上传」（`<input type="file" accept="image/*" capture="environment">`）；提示「电脑上也可以 Ctrl+V 粘贴文件」并实现 paste 上传。
  - 支持格式 chips：PDF / 图片 JPG·PNG·BMP·GIF·TIFF / 文本 TXT·MD·LOG / Office DOC·DOCX·XLS·XLSX·PPT·PPTX。
  - 拖拽区显示当前上限 `{{ max_upload_mb }}MB`。
- 上传队列：每文件一张卡片（类型图标、文件名、大小、状态 badge：上传中/转换中/已就绪/失败、进度条）。上传是并发多文件（POST /upload 每个文件一个请求），每次成功后刷新 `GET /api/files` 并与队列合并；失败显示后端 message，可重试。
- 列表展示当前会话已上传文件（刷新页面后仍保留）：状态「已就绪 / 不可打印 / 转换失败」，操作：预览（步骤 2）、删除（确认弹窗）。
- 底部主按钮「下一步：预览调整」，没有可打印文件时禁用并提示原因。
- 管理员模式（`boot.is_admin`）额外显示折叠卡片「本机管理员 · 所有会话文件」：按会话分组（sid 前 8 位 + 文件数），每项可预览/删除，提供「清理全部」危险按钮（POST /api/admin/clean，二次确认）。

### 步骤 2：预览调整

- 左侧/上方文件名切换器（横向滚动 chips，含类型图标；多文件时）。
- 预览画布：
  - 浅灰工作区中央显示白色 A4 页面（按所选纸张/方向显示纵横比），页面内容来自 `GET /api/render/<name>/<page>?dpi=120`（PNG）。
  - **缩放查看器（必须支持）**：滚轮缩放（桌面）、双指捏合缩放（手机，pointer events）、拖动平移、双击放大/还原；工具条按钮：`−`、百分比、`+`、`适应窗口`、`100%`。缩放范围 25%–400%，缩放围绕鼠标/捏合中心。
  - 多页 PDF/Office：上一页/下一页 + 页码输入 + 「共 N 页」。
  - 若 `render` 失败，回退用 `<iframe src="/preview/name">` 或提示「暂无法预览，可直接进入打印」。
- 图片专属布局面板（仅 kind == 'image' 时显示）：
  - 布局：`适应页面`（完整图片，有留白，**默认**）/ `铺满页面`（裁切铺满整页，推荐用于手机照片，加"推荐"角标）/ `原始大小`
  - 旋转：0°/90°/180°/270° 四个按钮（显示当前选中）
  - 边距：滑块 0–20mm，默认 5mm（铺满时可自动 0）
  - 纸张：A4 / A5 / Letter / 4R / 5R；页面方向：纵向 / 横向
  - 参数变化时带 250ms 防抖重新请求 `GET /api/image_layout/<name>?...` 并把返回 PNG 显示在页面预览中（本地 objectURL 管理，释放旧 URL），同时显示「当前预览 = 实际打印效果」提示。
  - 「恢复默认」按钮（默认 = 适应页面 + 5mm + A4 纵向）。
  - 注意：打印时后端会按 image 参数重新生成 PDF 并打印，因此预览必须与实际发送的 image 参数一致。
- 非图片文件不做布局面板；打印方向和纸张在步骤 3 设置。
- 底部按钮「上一步」「下一步：打印设置」。

### 步骤 3：打印设置

- 打印参数卡片：
  - 打印机下拉（来自 `GET /api/printers`，带刷新按钮），显示默认打印机；
  - 份数（1–10，stepper）；单双面：单面 / 长边翻转双面 / 短边翻转双面（值 1/2/3）；
  - 纸张：A4/A5/Letter/4R/5R（与步骤 2 图片纸张联动，但可独立）；打印方向：纵向/横向/反向横向/反向纵向（图片文件时隐藏方向，因图片方向在步骤 2 决定）；
  - 质量：普通/高质量（normal/high）；
  - 页码范围：快捷按钮「全部 / 奇数页 / 偶数页 / 自定义」+ 输入框（如 `1-5,7`）+ 可视化页码按钮网格（≤40 页数字按钮，多页时显示全部输入）；页数来自 `GET /api/page_count/<pdf_name>`；转换失败文件禁用。
  - Excel 文件（kind == 'sheet'）：显示工作表下拉（`GET /api/excel_info`）和打印区域输入（如 `A1:F20`）+「可视选区域」按钮打开表格弹窗（`GET /api/excel_preview`），支持拖拽矩形选择/Shift 扩展/Ctrl 多选，确认后写入打印区域。
- 待打印文件清单：每行 checkbox（默认勾选所有已就绪文件）、文件名、页数、移除；「全选/全不选」。单文件打印用当前选中文件的参数，多文件 = 批量打印（POST /print_all，仅打印本会话勾选文件——注意：/print_all 后端暂不支持勾选列表，前端多文件时逐文件调用 /print_single 并汇总结果）。
- 主按钮「开始打印」（大按钮、loading 态、显示「正在打印 2/3…」进度）；打印中禁用其他操作。结果卡片：逐文件成功/失败 + message；失败可重试。
- 打印记录：`GET /api/logs?limit=20` 显示本会话最近记录（时间、文件、状态），只显示自己的记录。支持「再来一单 / 新增文件」按钮回到步骤 1，以及「清空我的文件」（对每个文件调用 /delete_file，二次确认）。

## 5. 模板注入（index.html 中必须包含）

后端渲染 `index.html` 时注入：

```html
<script>window.PRINTSERVER_BOOT = {{ boot | tojson }};</script>
<script src="/static/app.js"></script>
```

`boot` 结构：

```json
{
  "version": "3.0",
  "sid": "0f3c2a1b9e8d4c7a",
  "is_admin": false,
  "server": {"ip": "192.168.1.5", "port": 5000},
  "printers": ["HUAWEI PixLab X1 0822 WLAN", "Microsoft Print to PDF"],
  "default_printer": "HUAWEI PixLab X1 0822 WLAN",
  "max_upload_mb": 200,
  "env": {"pymupdf": true, "office": true, "wps": false, "libreoffice": false},
  "paper_sizes": ["A4", "A5", "Letter", "4R", "5R"]
}
```

`index.html` 中可用 Jinja 变量：`{{ boot|tojson }}`、`{{ max_upload_mb }}`；其余数据一律走 API。`<html lang="zh-cn">`，`<title>内网打印服务</title>`，引用 `/static/favicon.ico`、`/static/app.css`、`/static/app.js`。注意 `boot` 放在 app.js 之前。

## 6. API 契约（全部返回 JSON，除标注为文件流的接口）

> 联调环境（后端已实现）：`http://127.0.0.1:5001` 为管理员视图（本机），
> `http://127.0.0.1:5002`（`PRINTSERVER_ADMIN_DISABLED=1` 启动）等价于局域网普通用户视图。
> 若 5001 未启动，在 `C:\PrintServerRebuild\PrintServer` 下运行：
> `C:\PrintServerRebuild\venv\Scripts\python.exe print_server.py --port 5001 --no-tray`
> 管理员预览他人文件时可附加 `?sid=<sid>` 参数（仅本机管理员有效），用于 `/preview`、`/api/render`、`/api/image_layout`、`/api/page_count`、`/api/excel_*`。

统一错误：HTTP 200 + `{"success": false, "message": "中文错误信息"}`（静态文件/预览接口除外）。会话由浏览器 cookie 自动携带（前端无需处理鉴权）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/session` | `{success, sid, is_admin, version, server:{ip,port}, max_upload_mb, env:{pymupdf,office,wps,libreoffice}}` |
| GET | `/api/printers` | `{success, printers:[...], default: "name"}` |
| POST | `/upload` | multipart 字段 `file`。返回 `{success, name, display_name, size, kind, converted, pdf_name, page_count, status, message}`；`name` 是后续所有接口使用的存储标识，`display_name` 是原始文件名。`kind`: `image|pdf|text|doc|sheet|slide|other` |
| GET | `/api/files` | `{success, files:[{name, display_name, size, created_time, kind, converted, pdf_name, page_count, status, status_color}]}` 仅本会话 |
| GET | `/api/files/all` | 仅管理员。`{success, sessions:[{sid, files:[...]}]}` |
| POST | `/delete_file` | body `{name}` → `{success, message}` |
| GET | `/preview/<name>` | 文件流：有 PDF 时返回 PDF，否则返回源文件（图片/文本/PDF）；无权限或无文件返回 404/403 文本 |
| GET | `/api/page_count/<pdf_name>` | `{success, page_count}`（也接受 name） |
| GET | `/api/render/<name>/<int:page>?dpi=120` | PNG 文件流，页面从第 1 页开始；不存在返回 404 |
| GET | `/api/image_layout/<name>?fit=fit\|fill\|actual&rotate=0\|90\|180\|270&margin=5&paper=A4&page_orientation=portrait\|landscape&dpi=120` | PNG 文件流：按参数生成「实际打印效果」页面图（图片专用） |
| GET | `/api/excel_info/<name>` | `{success, sheets:[...]}` |
| GET | `/api/excel_preview/<name>?sheet=` | `{success, rows, headers, col_count}` |
| GET | `/api/logs?limit=20` | `{success, logs:[{time, message, status, display_name}], is_admin}` 仅本会话（管理员全部） |
| POST | `/print_single` | 见 §7 |
| POST | `/print_all` | 见 §7（批量打印本会话全部可打印文件；管理员为本机全部） |
| POST | `/api/admin/clean` | 仅管理员，清空所有会话文件 → `{success, message}` |
| GET | `/health` | `{"status":"ok"}` |

### 特殊响应头

`/api/image_layout`、`/api/render` 返回 `image/png`。

## 7. 打印请求体

```json
POST /print_single
{
  "name": "0f3c2a1b_照片.jpg",          // 必填，存储标识
  "printer": "HUAWEI PixLab X1 0822 WLAN",
  "copies": 1,
  "duplex": 1,                           // 1单面 2长边 3短边
  "paper_size": "A4",
  "quality": "normal",                   // normal | high
  "orientation": "portrait",             // portrait | landscape | reverse_landscape | reverse_portrait
  "page_range": "all",
  "sheet_name": null,
  "print_area": null,
  "image": {                             // 仅图片可传；不传则用当前保存的默认布局
    "fit": "fill",                       // fit | fill | actual
    "rotate": 0,
    "margin": 5,
    "paper": "A4",
    "page_orientation": "portrait"
  }
}
→ {"success": true, "message": "打印成功", "display_name": "照片.jpg"}
```

`POST /print_all`：body 同上去掉 `name`/`image` 等单文件字段（保留 printer/copies/duplex/paper_size/quality/orientation/page_range），返回 `{success, printed_count, failed_count, message}`。

## 8. 验收标准（前端）

1. 手机宽度 375px 下完整可用：上传、预览缩放、打印参数、打印全流程无横向滚动。
2. 步骤条清晰显示 1→2→3，可回退；未解锁状态正确。
3. 图片：默认铺满整页预览；缩放工具 + 双指/滚轮缩放可用；切换布局/旋转/边距后预览 300ms 内更新；打印请求带上 image 参数。
4. 隐私：两个不同浏览器会话（或隐身窗口）互相看不到对方文件/日志；管理员（localhost）能看到所有会话。
5. 无 JS 报错（Chrome DevTools console 干净）；无外部网络请求（仅本站）。
6. 后端异常（422/500/网络失败）时界面有中文提示，不白屏。
7. 不用 innerHTML 拼接用户文件名；不用 `alert/confirm`（用自定义 toast + 弹窗）。
