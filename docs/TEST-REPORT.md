# PrintServer v3（优化版）测试报告

| 项目 | 内容 |
|---|---|
| 被测版本 | PrintServer v3.0（重建优化版，Flask + 原生 JS 三步向导 + PyInstaller） |
| 测试对象 | `print_server.py` + `static/`（index.html / app.css / app.js） |
| 测试执行 | Team QA（qa-dev），2026-10-31 01:28 – 01:43（Asia/Shanghai） |
| 测试代码 | `PrintServer/tests/`（pytest 3 个模块 + Playwright E2E 脚本） |
| 测试结论 | **通过（建议发布 RC）**：pytest 162 passed / 0 failed；E2E 92 项检查全部通过（修复复验后）。1 个低危 UI 展示缺陷在测试中被发现并由 Lead 修复、QA 复验通过。真实打印链路与 exe 产物需交付前人工验收（见 §11）。 |
| 写入范围 | 仅新增/修改 `tests/` 与 `docs/TEST-REPORT.md`；未修改 `print_server.py`、`static/`、`PrinterService.spec`、`docs/UI-SPEC.md`、`smoke_test.py` |

---

## 1. 结论摘要

1. **API 契约与功能**：`UI-SPEC.md §6` 的接口全部按契约工作，覆盖 session / printers / upload（图片、PDF、TXT、XLSX、DOCX，含中文文件名）/ files / page_count / render / image_layout（fit·fill·actual、rotate、margin、A5/4R/5R/Letter、横纵向）/ excel_info / excel_preview / preview / delete_file / logs / qr / health / 413 / 错误格式。
2. **隐私隔离**：两个独立 cookie 会话的文件列表、预览、渲染、页数、图片版式、删除、打印日志互相不可见；管理员（localhost）`/api/files/all` 可看全部会话，并可用 `?sid=` 跨会话预览/渲染/页数；非管理员视图下 `/api/files/all`、`/api/qr`、`/api/admin/clean` 均返回 403，且 `?sid=` 越权被拒绝。
3. **打印链路（安全范围内）**：未向任何真实打印机（HUAWEI 等）提交任务。`parse_page_range`、`_validate_print_request`、`build_image_pdf` 全部单测覆盖；`silent_print_pdf` 通过 mock 打印机 DC / Drawing 层验证参数（打印机、份数、双面、纸张、方向、质量）、分页/份数循环、DEVMODE 失败回退重试逻辑。
4. **前端 E2E**：手机 375×900 与桌面 1440×900 均完成 上传 → 预览（缩放按钮生效、布局切换、旋转）→ 打印设置 三步流程；步骤条始终可见、可回退；无 console error、无页面 JS 异常、无外部网络请求、无失败 HTTP、无横向滚动、无 alert/confirm；admin 徽标正确显示。
5. **测试可重复**：测试自建隔离实例（独立端口 + 独立数据目录，运行时清空），不依赖前次状态；全量 pytest 连续运行两次均 162 passed。

---

## 2. 测试环境

| 项 | 值 |
|---|---|
| OS | Windows（本机，内网环境） |
| Python | 3.14.3（`C:\PrintServerRebuild\venv\Scripts\python.exe`） |
| Flask / Werkzeug | 3.1.3 / 3.1.9 |
| PyMuPDF | 1.28.2（`pymupdf`） |
| Pillow / reportlab | 13.0.0 / 5.1.0 |
| openpyxl / python-docx | 3.1.5 / 1.2.0 |
| pytest / requests | 9.2.0 / 2.34.2 |
| Playwright | 1.64.1，`channel="msedge"`，headless |
| 浏览器 | Microsoft Edge 156.0.4314.50（`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`） |
| Office 转换引擎 | 本机 Word/Excel COM 可用（`env.office=true`）；WPS、LibreOffice 不可用 |
| 打印机 | 系统枚举到 7 台（含 HUAWEI PixLab X1 等）。**测试全程未向其提交任何打印任务** |

> 为跑测试额外安装的依赖（仅测试用）：`pytest`、`requests`、`playwright`(python 包，复用系统 Edge，未下载 chromium)、`python-docx`。未安装/修改应用运行依赖。

---

## 3. 测试隔离与安全约定

- **pytest 自建 3 个隔离实例**（pytest fixture 启动子进程，结束后关闭）：

  | 实例 | 端口（优先） | 关键环境变量 | 数据目录（每次运行前清空） |
  |---|---|---|---|
  | 管理员视图 | 5011（占用时自动 +10 起找空闲口） | `PRINTSERVER_DATA_DIR=<tests>/_runtime/admin_data` | `tests/_runtime/admin_data` |
  | 普通用户视图 | 5012 | 追加 `PRINTSERVER_ADMIN_DISABLED=1` | `tests/_runtime/nonadmin_data` |
  | 小上限实例（413） | 5013 | 追加 `PRINTSERVER_MAX_UPLOAD_MB=1` | `tests/_runtime/small_data` |

- **E2E 实例**：优先使用 UI-SPEC 约定的 **5001**（被占用且健康则复用，否则自动换空闲端口），数据目录 `tests/_runtime/e2e_data`。
- 所有测试数据都在 `tests/_runtime/` 下，测试运行前清空；**不触碰 Lead 的数据目录**。
- 每个 pytest 用例使用全新的 `requests.Session()`，即独立 sid，用例之间文件/日志互不影响。
- **打印安全红线**：不调用会真正 `StartDoc` 的路径。打印相关仅覆盖：
  - `/print_single`、`/print_all` 的参数校验失败路径（打印机不存在、文件不存在、打印区域非法等，均在创建打印任务前返回）；
  - 单元测试 monkeypatch `_create_printer_dc`、`ImageWin.Dib` 等，验证 `silent_print_pdf` 的参数传递/分页行为，不接触真实打印机。
  - E2E 只进入步骤 3 并检查设置项渲染，**不点击「开始打印」**。

---

## 4. 执行命令

```powershell
# 全量 pytest（API 集成 + 隐私隔离 + 打印单测；自动启动/停止 3 个隔离实例）
cd C:\PrintServerRebuild\PrintServer
C:\PrintServerRebuild\venv\Scripts\python.exe -m pytest tests -q

# 单独运行
C:\PrintServerRebuild\venv\Scripts\python.exe -m pytest tests\test_print.py -q
C:\PrintServerRebuild\venv\Scripts\python.exe -m pytest tests\test_api.py -q
C:\PrintServerRebuild\venv\Scripts\python.exe -m pytest tests\test_privacy.py -q

# Playwright 端到端（自动启动 5001 实例；手机 375x900 + 桌面 1440x900；截图到 tests/screenshots/）
C:\PrintServerRebuild\venv\Scripts\python.exe tests\e2e_ui.py

# 手动起服务观察（管理员视图 / 普通用户视图）
$env:PRINTSERVER_DATA_DIR="C:\PrintServerRebuild\PrintServer\tests\_runtime\admin_data"
C:\PrintServerRebuild\venv\Scripts\python.exe print_server.py --port 5001 --no-tray
# 另开 PowerShell：
$env:PRINTSERVER_ADMIN_DISABLED="1"
$env:PRINTSERVER_DATA_DIR="C:\PrintServerRebuild\PrintServer\tests\_runtime\nonadmin_data"
C:\PrintServerRebuild\venv\Scripts\python.exe print_server.py --port 5002 --no-tray
```

产物：
- 测试代码：`tests/ps_lib.py`（公共工具）、`tests/conftest.py`（隔离实例 fixture）、`tests/test_print.py`、`tests/test_api.py`、`tests/test_privacy.py`、`tests/e2e_ui.py`
- E2E 机读结果：`tests/_runtime/e2e_result.json`
- 截图：`tests/screenshots/`

---

## 5. 结果总览

| 测试集 | 用例/检查数 | 通过 | 失败 | 耗时 | 结果 |
|---|---|---|---|---|---|
| `tests/test_print.py`（打印链路单测，无真实打印） | 110 | 110 | 0 | ≈2.0s | ✅ |
| `tests/test_api.py`（API 集成，含中文名/413/Excel/DOCX） | 35 | 35 | 0 | ≈16.9s | ✅ |
| `tests/test_privacy.py`（双会话隐私 + 管理员/非管理员权限） | 17 | 17 | 0 | ≈4.8s | ✅ |
| **pytest 合计** | **162** | **162** | **0** | **21.87s**（全量） | ✅ |
| `tests/e2e_ui.py`（Playwright，2 个 profile） | 92 | 92 | 0 | ≈40s（含隔离实例启动） | ✅（Lead 修复 UI 缺陷后复验） |

**可重复性验证**：全量 pytest 连续两次运行均 `162 passed`（18.53s / 21.87s），无状态残留、无偶发失败。
**Office 转换**：本机 Office COM 可用，XLSX/DOCX 均真实完成转换（未 skip）。

---

## 6. pytest 明细

### 6.1 `tests/test_print.py` — 110 passed

| 测试组 | 覆盖内容 |
|---|---|
| `test_parse_page_range`（16 组参数） | `all`/空/None、单页、区间、逗号混排、倒序区间自动纠正、超页数截断、非法字符回退全部页、去重、空白处理 |
| `_validate_print_request`（合法/非法） | 默认值、完整参数、打印机不存在（中文报错含“打印机”）、无可用打印机、份数 clamp 1–10、双面 1/2/3、纸张归一化（大小写/非法→A4）、方向 4 值校验、质量 normal/high、页码范围截断、工作表名、打印区域正则（合法 `A1`/`A1:F20`/`A1:B2,C3:D4`；非法 `Sheet1!A1`、`A1:`、`1:2`、`A1-F20`、`A1:F20;` 等）、可选字段空值 |
| `_normalize_paper` / `_normalize_image_layout` | 5 种纸张大小写、非法回退 A4；fit/rotate/margin/paper/orientation 的默认、合法、非法回退与 clamp |
| `build_image_pdf` 页面尺寸（6 组） | A4 纵向/横向、A5、Letter、4R、5R：PDF 页面物理尺寸 ≈ 纸张毫米尺寸（±0.6pt） |
| `build_image_pdf` 版式 | fit：绘制区域 = 内容区、5mm 边距、居中、不铺满；fill：铺满整页、居中裁切不变形（嵌入图宽高比 ≈ 页面比）、带 margin 时保留边距；actual：96DPI 原始尺寸 200×100px→150×75pt 居中、超大图缩放不超内容区；rotate 0/90/180/270 后内容宽高比 2:1 / 1:2；非法参数回退；负 margin clamp 到 0 |
| `build_image_pdf_bytes` / `render_image_layout_png` | PDF 魔数、PNG 魔数、100dpi 下 A4 像素尺寸（±3px）、非白区域存在 |
| `silent_print_pdf`（mock，**不产生真实打印**） | 参数传递（打印机名 / 纸张 A5 / 双面 2）、页码范围 `1,3` × 份数 2 → StartDoc×2、StartPage×4/EndPage×4、每页 Draw 区域在可打印区域内；份数 99 clamp 到 10；A4+单面走默认 DC 路径（`force_default=True`）；自定义纸张 DC 可打印区域无效时**回退默认 DC 重试**并在 warnings 记录；未知打印机/空打印机/文件不存在在创建 DC 前报错 |
| `_apply_print_orientation` | portrait / landscape / reverse_landscape / reverse_portrait / 未知 的尺寸旋转正确 |

### 6.2 `tests/test_api.py` — 35 passed

| 测试组 | 覆盖内容 |
|---|---|
| 基础 | `/health`；首页 HTML 渲染（`lang=zh-cn`、注入 `PRINTSERVER_BOOT`、无未渲染 Jinja、title/静态资源可访问）；未知 API 返回 JSON 404 |
| 会话 | `/api/session` 字段契约（sid 32 位 hex、is_admin、version、server.ip/port、max_upload_mb、env、paper_sizes、printers、default_printer）；cookie `printserver_sid`；sid 稳定；两个浏览器会话 sid 不同 |
| 上传 | 中文文件名图片（name/display_name 保留中文、kind、converted、page_count、status、pdf_name、文件列表字段完整）；3 页 PDF；TXT；XLSX（`excel_info` 工作表 `["Sheet1","数据2"]`、`excel_preview` 单元格/表头列数）；DOCX（Office 可用时转换成功）；不支持类型拒绝（3 种）；缺 file 字段；路径穿越文件名 `../../逃逸文件.txt` 被清洗且不落盘到会话目录外；同名重复上传覆盖（文件数仍为 1、PDF 已更新） |
| 预览/渲染 | `/api/page_count`（name 与 pdf_name 均可、缺文件 404）；`/api/render` PNG 魔数 + A4 100dpi 尺寸 + 不同页不同 + 超页码 500 + 缺文件 404 + dpi 上限 clamp；`/preview`（图片/PDF/文本在已转换时按契约返回 PDF、缺文件 404、路径穿越被阻止） |
| 图片版式 | fit/fill/actual 三态差异（A4 100dpi：fit 非白区域 = 内容区居中、fill 铺满整页且输出不同）；rotate 0°/90° 非白区域宽高比 2.0↔0.5；纸张尺寸 A4/A5/4R/5R/A4 横向像素尺寸；margin 5mm vs 20mm 绘制区域差异；非图片文件请求布局被拒绝 |
| 删除 | 删除成功并移出列表；二次删除返回“不存在”；缺 name 报错 |
| 413 | 小上限实例（`PRINTSERVER_MAX_UPLOAD_MB=1`）上传 1.5MB 文件 → HTTP 413 + `success:false` + “大小限制”；同实例小文件上传正常 |
| 打印参数（仅失败路径） | `/print_single`：缺文件名、文件不存在(404)、打印机不存在、打印区域非法；`/print_all`：打印机不存在；空会话合法参数 → “没有可打印的文件”且无日志（未发生打印） |
| 日志 | 校验失败后 `/api/logs` 出现失败记录（display_name/status/time）；limit 生效 |
| 其他 | `/api/printers` 列表与默认打印机；`/api/qr` 管理员返回 PNG + `X-QR-URL` 头 |

### 6.3 `tests/test_privacy.py` — 17 passed

| 测试组 | 覆盖内容 |
|---|---|
| 管理员实例双会话隔离 | A 上传图片+文本并产生一条失败打印日志；B 的文件列表为空；B 对 A 的文件 `/preview`、`/api/render`、`/api/page_count`、`/api/image_layout`、`/api/excel_info` 全部不可见；B 无法删除 A 的文件；B 日志为空（A 能看到自己的失败记录） |
| 管理员能力 | `/api/files/all` 含 A 的 sid 与全部文件；管理员会话 B 用 `?sid=A` 可预览 A 的图片/渲染/布局/页数 |
| 非管理员视图（`PRINTSERVER_ADMIN_DISABLED=1`） | `/api/session` `is_admin=false`；`/api/files/all`、`/api/qr`、`/api/admin/clean` → **403**；非管理员传 `all=1` 日志也不泄漏他人记录；两个普通会话文件仍隔离；`?sid=` 越权预览/渲染/删除被拒绝且 A 文件未被删；`/api/admin/clean` 被拒后文件仍在 |

---

## 7. Playwright E2E 明细（92/92 PASS）

脚本：`tests/e2e_ui.py`；浏览器 Microsoft Edge（Playwright `channel="msedge"`, headless）；两个 profile 各 46 项检查：

| Profile | 检查点数 | 结果 |
|---|---|---|
| 手机 `375×900`（`is_mobile=True, has_touch=True`） | 46 | ✅ 全部通过 |
| 桌面 `1440×900` | 46 | ✅ 全部通过 |
| 合计 | 92 | ✅ 92 pass / 0 fail |

每个 profile 覆盖：

1. **加载 / 步骤 1**：服务在线状态、3 步步骤条可见且第 1 步激活、管理员徽标、空文件状态、下一步按钮初始禁用、控制台无错误。
2. **上传**：通过文件选择（PIL 生成 1600×1200 测试图）上传，文件出现在“我的文件”、管理员卡片可见、下一步按钮启用。
3. **步骤 2 预览**：面板切换、`#pageImg` 真实渲染（`naturalWidth>0`）、图片布局面板与“当前预览 = 实际打印效果”提示、缩放工具条；**点 `+` 后百分比 32%→40%（手机）/40%→50%（桌面），点「适应窗口」恢复**。
4. **布局**：默认「适应页面」；切「铺满页面」后预览图更新、边距自动 0mm、发出 `fit=fill` 请求、**文件信息同步显示「铺满页面」**（缺陷修复复验点）；旋转 90° 后预览更新并发出 `rotate=90` 请求；恢复默认。
5. **步骤 3 设置**：打印机下拉有选项（7 台）、份数默认 1 且 stepper ±1 生效、纸张 5 项、单双面 3 项、质量 2 项、页码范围 4 个快捷键、页码“共 1 页”、待打印文件清单且默认勾选、图片文件隐藏方向选择、开始打印按钮可见（**未点击**）、打印记录卡片可见。
6. **导航 / 布局质量**：步骤条可回退/前进；三步均无横向滚动；步骤条始终可见。
7. **运行时健康**：无 console error、无 pageerror、无外部网络请求（仅 127.0.0.1）、无失败 HTTP 响应、无 alert/confirm 弹窗。
8. **安全**：未点击「开始打印」，未向真实打印机提交任务。

机读结果（`tests/_runtime/e2e_result.json` 摘要）：
```json
{"pass": 92, "fail": 0,
 "profiles": [
   {"label": "mobile",  "console_errors": [], "page_errors": [], "external_requests": [], "bad_responses": [], "dialogs": []},
   {"label": "desktop", "console_errors": [], "page_errors": [], "external_requests": [], "bad_responses": [], "dialogs": []}
 ]}
```

---

## 8. 缺陷记录

### BUG-1（低危 · 已修复并复验通过）：步骤 2 切换图片布局后「文件信息」不同步

- **发现方式**：E2E 断言 + 截图人工复核（`mobile_04_step2_fill.png`、`desktop_04_step2_fill.png` 旧版）。
- **现象**：步骤 2 点击「铺满页面 / 适应页面」后，左侧布局按钮与预览图已更新，但右下「文件信息」卡的“图片布局”仍显示旧值（如按钮已是“铺满页面”，信息仍是“适应页面 · 旋转 0°”）。
- **复现**：上传图片 → 下一步 → 点「铺满页面」→ 查看右下「文件信息」。
- **定位（QA 只读排查）**：`static/app.js` 中 `#fitGroup` 点击处理（约 2028-2041 行）只调用了 `renderLayoutPanel()` + `scheduleLayoutPreview()`，未调用 `renderPreviewInfo()`（旋转/纸张/恢复默认按钮均有调用）。
- **修复（Lead）**：在该处理中补上 `renderPreviewInfo()`。
- **影响面**：仅信息展示不一致；实际 `image_layout` 预览参数和 `/print_single` 的 `image` 参数均正确，**不影响打印结果**。
- **复验**：修复后重跑 E2E → **92 PASS / 0 FAIL**；截图中「文件信息」已显示“铺满页面 · 旋转 0°”。

**未解决缺陷：无。**

---

## 9. 测试截图

截图目录：`tests/screenshots/`（Playwright 整页截图，2 个 profile × 5 个关键状态）。

### 9.1 手机 375×900

| 步骤 1：上传前 | 步骤 2：预览（适应页面） |
|---|---|
| ![手机步骤1](<../tests/screenshots/mobile_01_step1_initial.png>) | ![手机步骤2](<../tests/screenshots/mobile_03_step2_preview.png>) |

| 步骤 2：铺满页面（信息已同步） | 步骤 3：打印设置 |
|---|---|
| ![手机铺满](<../tests/screenshots/mobile_04_step2_fill.png>) | ![手机步骤3](<../tests/screenshots/mobile_05_step3_settings.png>) |

上传后状态：`mobile_02_step1_uploaded.png`（文件卡片、已就绪、下一步启用）。

### 9.2 桌面 1440×900

| 步骤 2：预览（适应页面） | 步骤 2：铺满页面 |
|---|---|
| ![桌面预览](<../tests/screenshots/desktop_03_step2_preview.png>) | ![桌面铺满](<../tests/screenshots/desktop_04_step2_fill.png>) |

| 步骤 3：打印设置（桌面双栏） |
|---|
| ![桌面步骤3](<../tests/screenshots/desktop_05_step3_settings.png>) |

步骤 1 初始/上传后：`desktop_01_step1_initial.png`、`desktop_02_step1_uploaded.png`。

> 人工复核结论：截图非空白/无错位；手机端无横向滚动、步骤条常驻；桌面端双栏布局正常、预览与侧栏信息一致；「铺满页面」时预览图铺满整页、边距 0mm；步骤 3 参数项渲染完整；控制台无报错。

---

## 10. 覆盖矩阵（任务要求 → 验证结果）

| 任务要求 | 验证方式 | 结果 |
|---|---|---|
| /api/session | `test_api.py::test_session_contract_impl` 等 | ✅ |
| 上传 图片/PDF/TXT/XLSX/DOCX（含中文名） | `test_api.py` 上传组（5 种格式 + 中文名 + 路径穿越 + 同名覆盖） | ✅ |
| page_count | `test_api.py::test_page_count_api` | ✅ |
| render PNG | `test_api.py::test_render_png`（尺寸/魔数/越界/dpi clamp） | ✅ |
| image_layout 三种 fit + rotate 效果差异 | `test_api.py::test_image_layout_fit_fill_actual / test_image_layout_rotate`（像素 bbox 比对）+ 单测 PDF bbox | ✅ |
| excel_info / excel_preview | `test_api.py::test_upload_xlsx_and_excel_apis` | ✅ |
| preview / delete / 413 / health | `test_api.py` 对应用例 | ✅ |
| 双 cookie 会话隔离（列表/预览/删除/日志/页数/image_layout） | `test_privacy.py` 前 5 个用例 | ✅ |
| 管理员 /api/files/all 与 sid 访问 | `test_privacy.py::test_admin_sees_all_sessions / test_admin_can_access_other_session_by_sid` | ✅ |
| 非管理员 403（/api/files/all、/api/qr） | `test_privacy.py::test_nonadmin_forbidden_endpoints`（另含 /api/admin/clean） | ✅ |
| parse_page_range | `test_print.py` 16 组参数 | ✅ |
| _validate_print_request（合法/非法打印机、份数/双面/纸张/方向/打印区域格式） | `test_print.py` 参数化用例 | ✅ |
| 图片版式尺寸（A4/A5/4R、fit/fill/rotate 绘制区域） | `test_print.py` PDF bbox 断言 + `test_api.py` 像素 bbox 断言 | ✅ |
| 打印只测校验失败路径 / mock 单测（不产生真实打印） | 所有打印相关用例均在 `silent_print_pdf` 前失败或 mock DC | ✅ |
| E2E 三步流程（手机 375×900 + 桌面 1440×900） | `tests/e2e_ui.py`（92 项检查） | ✅ |
| console error / 外部网络请求检查 | E2E 内 `page.on("console"/"request"/"pageerror"/"response"/"dialog")` | ✅ |
| 截图并人工自检 | `tests/screenshots/` 10 张 + `read_image` 人工复核 | ✅ |

---

## 11. 遗留风险与限制（交付前建议确认）

| # | 风险/限制 | 影响 | 建议 |
|---|---|---|---|
| 1 | **真实打印未实测**（安全红线：禁止向 HUAWEI 等实体打印机提交任务） | 纸张/双面/方向/质量的真实输出效果、DPI 清晰度未经端到端验证；仅 mock 单测验证了参数传递、分页与回退逻辑 | 交付前用「Microsoft Print to PDF」作为目标打印机人工打印一次（A4/A5、双面、页码范围、图片铺满），核对输出 |
| 2 | **PyInstaller exe 产物未由 QA 验证**（本报告验证的是源码运行；Lead 已于 01:42 用修复后 static 重新打包 `dist/PrintServer.exe`） | 打包后可能存在资源路径/模板加载差异 | 打包后人工冒烟：双击 exe → 浏览器打开首页 → 上传一张图片 → 预览 → 不打印；检查 tray/端口占用提示 |
| 3 | **移动端真机/iOS Safari 未测**：E2E 为 Chromium（Edge headless）模拟手机 viewport + touch | 双指捏合手势、iOS 输入框自动缩放、刘海安全区等真机行为未验证 | 用 iPhone/Android 真机各走一遍三步流程（重点：双指缩放、拍照上传、无横向滚动） |
| 4 | **Office 转换依赖本机 Office COM**（本机 Word/Excel 可用，XLSX/DOCX 转换成功） | 目标机无 Office/WPS/LibreOffice 时，DOC/DOCX/XLS/XLSX/PPT/PPTX 上传后 `converted=false`（前端会显示“转换失败/不可打印”中文提示，不会白屏） | 在部署说明中注明转换引擎依赖；目标机按需安装 Office/WPS/LibreOffice |
| 5 | `/api/admin/clean` **成功路径未执行**（破坏性：会清空所有会话文件与日志，避免影响共享实例） | 管理员“清理全部”按钮的后端效果未自动化验证 | 在独立数据目录实例上人工验证一次（或后续补独立实例自动化用例） |
| 6 | 413 只覆盖“超限”场景（1MB 上限实例上传 1.5MB） | 200MB 默认上限的边界值/大文件上传耗时未验证 | 如需生产验证，用接近上限的样本在测试机人工验证一次 |
| 7 | 文件自动清理（默认 1 小时过期）未测 | 过期文件清理/日志清理行为未验证 | 可把 `PRINTSERVER_FILE_EXPIRE_SECONDS` 调小做一次集成验证（当前未覆盖） |
| 8 | 管理员判定基于 `request.remote_addr` 为 localhost | 本机所有浏览器会话均为管理员（设计如此）；若未来部署在反向代理/端口转发后，`remote_addr` 可能不是 127.0.0.1，管理员能力会变化 | 部署文档注明；必要时使用 `PRINTSERVER_ADMIN_IPS` 或保持直连局域网访问 |
| 9 | 未做并发/压力测试（多会话同时上传、批量打印排队） | 高并发下 `_print_lock` 串行化、Office COM 串行化仅有静态代码审查 | 如需对外承诺并发能力，补多会话并发上传 + 批量打印（mock）压测 |
| 10 | E2E 未覆盖滚轮缩放/双击放大/真实双指手势（已验证工具条按钮与触摸模拟） | 交互细节存在回归风险 | 后续可补 Playwright 滚轮/双击用例；真机手势见第 3 条 |

> 说明：以上均为“未验证/依赖环境”的残余风险，不代表已发现功能缺陷。测试期间发现的唯一缺陷（BUG-1）已修复并复验通过。

---

## 12. 发布建议

- **结论：建议发布（Release Candidate 可放行）**，理由：
  1. API 契约、隐私隔离、图片版式、打印参数链路（mock）全部自动化验证通过（162/162）；
  2. UI 三步流程在手机/桌面尺寸下自动化验证通过（92/92），无 console error / 外部请求 / 横向滚动；
  3. 唯一低危 UI 缺陷已修复且复验通过，无未解决缺陷；
  4. 测试在隔离环境可重复执行（连续两轮全绿），不污染用户/Lead 数据。
- **放行前建议完成的 2 项人工验收**（约 10 分钟）：
  1. 打包后的 `dist/PrintServer.exe` 冒烟（首页加载 + 上传 + 预览 + 不打印）；
  2. 使用「Microsoft Print to PDF」人工打印一张图片/PDF（验证纸张、双面、方向、页码范围的实际输出）。
- 发布后如出现打印相关问题，可用本报告的复现命令与 `tests/_runtime/e2e_result.json`、服务器日志（`tests/_runtime/*_data/server_stdout.log`）快速定位。

---

## 13. 产物清单

| 产物 | 路径 |
|---|---|
| 打印链路单测（110 用例，不真实打印） | `tests/test_print.py` |
| API 集成测试（35 用例） | `tests/test_api.py` |
| 隐私隔离测试（17 用例） | `tests/test_privacy.py` |
| Playwright E2E（92 项检查 + 截图） | `tests/e2e_ui.py` |
| 测试公共工具 / 隔离实例 fixture | `tests/ps_lib.py` / `tests/conftest.py` |
| 截图（手机/桌面 × 5 状态） | `tests/screenshots/*.png` |
| E2E 机读结果 | `tests/_runtime/e2e_result.json` |
| 测试运行数据（隔离，可随时删除） | `tests/_runtime/` |
| 本报告 | `docs/TEST-REPORT.md` |

---

*报告生成：2026-10-31 01:43（Asia/Shanghai） · QA（qa-dev） · 基于 Lead 修复后的最新 `static/app.js`（121589 字节）与 `print_server.py`（92425 字节）验证。*
