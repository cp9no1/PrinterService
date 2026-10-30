# -*- coding: utf-8 -*-
"""Playwright 端到端 UI 测试：手机 375x900 / 桌面 1440x900 的三步向导流程。

流程：打开 → 上传图片 → 步骤 2 预览（缩放/布局）→ 步骤 3 打印设置 → 截图。
绝不点击「开始打印」，不会向真实打印机提交任务。

运行：
    C:\\PrintServerRebuild\\venv\\Scripts\\python.exe tests\\e2e_ui.py
可选环境变量：
    PS_E2E_BASE=http://127.0.0.1:5001   复用已有实例
    PS_E2E_HEADED=1                     有界面运行（调试用）
"""
import json
import os
import sys
import time

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
for _p in (PROJECT_DIR, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ps_lib  # noqa: E402

SHOT_DIR = ps_lib.SCREENSHOT_DIR
RUNTIME_DIR = ps_lib.RUNTIME_DIR
ASSET_DIR = os.path.join(RUNTIME_DIR, "e2e_assets")
RESULT_PATH = os.path.join(RUNTIME_DIR, "e2e_result.json")

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("  PASS", name)
    else:
        FAIL.append((name, detail))
        print("  FAIL", name, "|", str(detail)[:400])
    return bool(cond)


def wait_ui_ready(page, timeout=30000):
    page.wait_for_selector("#serverStatusText", state="visible", timeout=timeout)
    page.wait_for_function(
        "() => (document.querySelector('#serverStatusText') || {}).textContent === '服务在线'",
        timeout=timeout)


def wait_text(page, selector, expected, timeout=15000):
    page.wait_for_function(
        "([sel, exp]) => {const el = document.querySelector(sel);"
        " return !!el && (el.textContent || '').indexOf(exp) >= 0;}",
        arg=[selector, expected], timeout=timeout)


def shot(page, name):
    path = os.path.join(SHOT_DIR, name)
    # 步骤条是 position:sticky，截图前回到顶部，避免整页截图时 sticky 元素出现在中间
    try:
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(150)
    except Exception:
        pass
    page.screenshot(path=path, full_page=True, animations="disabled")
    print("  screenshot:", path)
    return path


def make_assets():
    ps_lib.ensure_dir(ASSET_DIR)
    image_path = os.path.join(ASSET_DIR, "测试照片.jpg")
    ps_lib.make_image(image_path, size=(1600, 1200), color=(37, 99, 235))
    return image_path


def run_flow(browser, base, label, viewport, mobile, image_path):
    """执行一遍完整三步流程并截图。返回本 profile 的统计。"""
    print("\n=== 流程: %s (%sx%s) ===" % (label, viewport["width"], viewport["height"]))
    console_errors = []
    page_errors = []
    external_requests = []
    bad_responses = []
    dialogs = []
    image_layout_requests = []

    context = browser.new_context(
        viewport=viewport,
        device_scale_factor=1,
        is_mobile=mobile,
        has_touch=mobile,
        locale="zh-CN",
    )
    page = context.new_page()
    page.set_default_timeout(30000)

    def on_console(msg):
        if msg.type == "error":
            console_errors.append(msg.text)

    def on_pageerror(err):
        page_errors.append(str(err))

    def on_request(req):
        url = req.url
        if url.startswith("http://") or url.startswith("https://"):
            host = url.split("/")[2].split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                external_requests.append(url)
            if "/api/image_layout/" in url:
                image_layout_requests.append(url)

    def on_response(resp):
        try:
            if resp.status >= 400:
                bad_responses.append("%s %s" % (resp.status, resp.url))
        except Exception:
            pass

    def on_dialog(dialog):
        dialogs.append("%s: %s" % (dialog.type, dialog.message))
        try:
            dialog.dismiss()
        except Exception:
            pass

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("request", on_request)
    page.on("response", on_response)
    page.on("dialog", on_dialog)

    prefix = label
    try:
        page.goto(base + "/", wait_until="domcontentloaded", timeout=60000)
        wait_ui_ready(page)

        # ---------- 步骤 1：初始状态 ----------
        steps = page.locator("#stepbar .step")
        check("%s: 步骤条可见" % prefix, steps.count() == 3 and steps.first.is_visible())
        check("%s: 步骤 1 为当前步骤" % prefix,
              "is-active" in (page.locator('#stepbar .step[data-step="1"]')
                              .get_attribute("class") or ""))
        check("%s: 管理员徽标可见" % prefix,
              page.locator("#adminBadge").is_visible()
              and "本机管理员" in page.locator("#adminBadge").inner_text())
        check("%s: 文件列表为空状态" % prefix, page.locator("#filesEmpty").is_visible())
        check("%s: 下一步按钮初始禁用" % prefix,
              page.locator("#step1NextBtn").is_disabled())
        shot(page, "%s_01_step1_initial.png" % prefix)

        # ---------- 步骤 1：上传图片 ----------
        page.set_input_files("#fileInput", image_path)
        page.wait_for_function(
            "() => document.querySelectorAll('#sessionFileList .file-row').length >= 1",
            timeout=90000)
        page.wait_for_function(
            "() => {const b = document.querySelector('#step1NextBtn'); return b && !b.disabled;}",
            timeout=30000)
        file_text = page.locator("#sessionFileList").inner_text()
        check("%s: 上传后文件出现在列表" % prefix, "测试照片" in file_text, file_text[:200])
        check("%s: 上传后管理员卡片可见" % prefix,
              page.locator("#adminCard").is_visible())
        check("%s: 下一步按钮已启用" % prefix,
              not page.locator("#step1NextBtn").is_disabled())
        shot(page, "%s_02_step1_uploaded.png" % prefix)

        # ---------- 步骤 2：预览 ----------
        page.click("#step1NextBtn")
        page.wait_for_function(
            "() => {const p = document.querySelector('[data-step-panel=\"2\"]');"
            " return p && p.classList.contains('is-active');}", timeout=30000)
        page.wait_for_function(
            "() => {const i = document.querySelector('#pageImg');"
            " return !!i && !i.hidden && i.naturalWidth > 0;}", timeout=90000)
        check("%s: 步骤 2 面板激活" % prefix,
              "is-active" in (page.locator('[data-step-panel="2"]')
                              .get_attribute("class") or ""))
        check("%s: 预览图已渲染" % prefix,
              page.evaluate("() => document.querySelector('#pageImg').naturalWidth") > 0)
        check("%s: 图片布局面板可见" % prefix,
              page.locator("#imageLayoutCard").is_visible())
        check("%s: 预览图 = 实际打印效果 提示可见" % prefix,
              page.locator("#wysiwygNote").is_visible())
        check("%s: 缩放工具条可见" % prefix,
              page.locator("#zoomInBtn").is_visible()
              and page.locator("#zoomOutBtn").is_visible())

        zoom_before = int(page.locator("#zoomPct").inner_text().replace("%", ""))
        page.click("#zoomInBtn")
        page.wait_for_function(
            "prev => parseInt(document.querySelector('#zoomPct').textContent) > prev",
            arg=zoom_before, timeout=5000)
        zoom_after = int(page.locator("#zoomPct").inner_text().replace("%", ""))
        check("%s: 缩放 + 按钮生效（%d%% → %d%%）" % (prefix, zoom_before, zoom_after),
              zoom_after > zoom_before)
        page.click("#zoomOutBtn")
        page.click("#fitBtn")
        page.wait_for_function(
            "prev => Math.abs(parseInt(document.querySelector('#zoomPct').textContent) - prev) <= 2",
            arg=zoom_before, timeout=5000)
        check("%s: 适应窗口按钮恢复缩放" % prefix,
              abs(int(page.locator("#zoomPct").inner_text().replace("%", ""))
                  - zoom_before) <= 2)
        shot(page, "%s_03_step2_preview.png" % prefix)

        # ---------- 步骤 2：图片布局（fit→fill）----------
        active_fit = page.locator('#fitGroup [data-fit="fit"]').get_attribute("class") or ""
        check("%s: 默认布局为「适应页面」" % prefix, "is-active" in active_fit, active_fit)
        src_before = page.evaluate("() => document.querySelector('#pageImg').src")
        page.click('#fitGroup [data-fit="fill"]')
        page.wait_for_function(
            "prev => {const i = document.querySelector('#pageImg');"
            " return !!i && i.src && i.src !== prev && i.naturalWidth > 0;}",
            arg=src_before, timeout=30000)
        fill_active = page.locator('#fitGroup [data-fit="fill"]').get_attribute("class") or ""
        check("%s: 切换「铺满页面」后预览更新" % prefix,
              "is-active" in fill_active and "测试" in file_text)
        check("%s: 铺满后边距自动为 0mm" % prefix,
              page.locator("#marginValue").inner_text().strip().startswith("0"))
        # 「文件信息」卡应同步反映当前布局（切换 fit 后不应停留在旧值）
        info_text = page.locator("#previewInfo").inner_text()
        check("%s: 文件信息同步显示「铺满页面」" % prefix,
              "铺满页面" in info_text, info_text.replace("\n", " | ")[:300])
        check("%s: 请求了 fit=fill 的布局预览" % prefix,
              any("fit=fill" in url for url in image_layout_requests),
              image_layout_requests[-3:])
        shot(page, "%s_04_step2_fill.png" % prefix)

        # ---------- 步骤 2：旋转 90° ----------
        src_before = page.evaluate("() => document.querySelector('#pageImg').src")
        page.click('#rotateGroup [data-rotate="90"]')
        page.wait_for_function(
            "prev => {const i = document.querySelector('#pageImg');"
            " return !!i && i.src && i.src !== prev && i.naturalWidth > 0;}",
            arg=src_before, timeout=30000)
        rot_active = page.locator('#rotateGroup [data-rotate="90"]').get_attribute("class") or ""
        check("%s: 旋转 90° 后预览更新" % prefix, "is-active" in rot_active)
        check("%s: 请求了 rotate=90 的布局预览" % prefix,
              any("rotate=90" in url for url in image_layout_requests))
        page.click("#resetLayoutBtn")
        page.wait_for_function(
            "() => {const b = document.querySelector('#fitGroup [data-fit=\"fit\"]');"
            " return b && b.classList.contains('is-active');}", timeout=10000)

        # ---------- 步骤 3：打印设置 ----------
        page.click("#step2NextBtn")
        page.wait_for_function(
            "() => {const p = document.querySelector('[data-step-panel=\"3\"]');"
            " return p && p.classList.contains('is-active');}", timeout=30000)
        page.wait_for_function(
            "() => document.querySelectorAll('#printerSelect option').length > 0",
            timeout=30000)
        printer_options = page.locator("#printerSelect option").count()
        paper_options = page.locator("#paperSelect option").count()
        duplex_buttons = page.locator("#duplexGroup button").count()
        quality_buttons = page.locator("#qualityGroup button").count()
        range_buttons = page.locator("#rangeQuick button").count()
        check("%s: 步骤 3 面板激活" % prefix,
              "is-active" in (page.locator('[data-step-panel="3"]')
                              .get_attribute("class") or ""))
        check("%s: 打印机下拉有选项（%d）" % (prefix, printer_options),
              printer_options >= 1)
        check("%s: 份数默认 1" % prefix,
              page.locator("#copiesInput").input_value() == "1")
        check("%s: 纸张选项 5 种" % prefix, paper_options == 5)
        check("%s: 单双面 3 个选项" % prefix, duplex_buttons == 3)
        check("%s: 打印质量 2 个选项" % prefix, quality_buttons == 2)
        check("%s: 页码范围 4 个快捷按钮" % prefix, range_buttons == 4)
        check("%s: 待打印文件清单非空" % prefix,
              page.locator("#printFileList .print-file-row").count() >= 1)
        checked = page.locator("#printFileList input[type=checkbox]:checked").count()
        check("%s: 待打印文件默认勾选" % prefix, checked >= 1)
        check("%s: 图片文件隐藏方向选择" % prefix,
              page.locator("#orientationRow").is_hidden())
        check("%s: 页码范围显示共 1 页" % prefix,
              page.locator("#rangePageCount").inner_text().strip() == "1")
        check("%s: 开始打印按钮可见（不点击）" % prefix,
              page.locator("#startPrintBtn").is_visible())
        check("%s: 打印记录卡片可见" % prefix,
              page.locator("#logsList").is_visible()
              or page.locator("#logsEmpty").is_visible())

        # 份数 stepper 生效
        page.click("#copiesPlus")
        page.wait_for_function(
            "() => document.querySelector('#copiesInput').value === '2'", timeout=5000)
        page.click("#copiesMinus")
        page.wait_for_function(
            "() => document.querySelector('#copiesInput').value === '1'", timeout=5000)
        check("%s: 份数 stepper 生效" % prefix, True)

        # 步骤条可回退
        page.click('#stepbar .step[data-step="2"]')
        page.wait_for_function(
            "() => document.querySelector('[data-step-panel=\"2\"]').classList.contains('is-active')",
            timeout=10000)
        page.click('#stepbar .step[data-step="3"]')
        page.wait_for_function(
            "() => document.querySelector('[data-step-panel=\"3\"]').classList.contains('is-active')",
            timeout=10000)
        check("%s: 步骤条可回退/前进" % prefix, True)
        shot(page, "%s_05_step3_settings.png" % prefix)

        # ---------- 布局 / 溢出 / 控制台检查 ----------
        for step in (1, 2, 3):
            page.click('#stepbar .step[data-step="%d"]' % step)
            page.wait_for_function(
                "n => document.querySelector('[data-step-panel=\"' + n + '\"]')"
                ".classList.contains('is-active')", arg=str(step), timeout=10000)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth")
            check("%s: 第 %d 步无横向滚动 (overflow=%spx)" % (prefix, step, overflow),
                  overflow <= 1)
        stepbar_box = page.locator("#stepbar").bounding_box()
        check("%s: 步骤条始终可见" % prefix,
              stepbar_box is not None and stepbar_box["height"] > 10
              and stepbar_box["width"] > 100)

        check("%s: 无 console error" % prefix, not console_errors, console_errors[:5])
        check("%s: 无页面 JS 异常" % prefix, not page_errors, page_errors[:5])
        check("%s: 无外部网络请求" % prefix, not external_requests,
              external_requests[:5])
        check("%s: 无失败 HTTP 响应" % prefix, not bad_responses, bad_responses[:5])
        check("%s: 无 alert/confirm 弹窗" % prefix, not dialogs, dialogs[:5])
    except Exception as exc:  # noqa: BLE001 - 记录异常并继续/收尾
        FAIL.append(("%s: 流程异常" % prefix, repr(exc)))
        print("  FAIL", prefix, "流程异常:", repr(exc)[:500])
        try:
            shot(page, "%s_99_error.png" % prefix)
        except Exception:
            pass
    finally:
        context.close()

    return {
        "label": label,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "external_requests": external_requests,
        "bad_responses": bad_responses,
        "dialogs": dialogs,
    }


def ensure_server():
    """返回 (server_or_None, base, note)。优先使用 5001（UI-SPEC 约定端口）。"""
    given = os.environ.get("PS_E2E_BASE", "").strip()
    if given:
        ps_lib.wait_health(given, timeout=20)
        return None, given.rstrip("/"), "复用外部实例 %s" % given

    preferred = ps_lib.E2E_PORT
    base = "http://127.0.0.1:%d" % preferred
    try:
        ps_lib.wait_health(base, timeout=1)
        return None, base, "复用已运行的 5001 实例（非本脚本启动）"
    except Exception:
        pass

    data_dir = os.path.join(RUNTIME_DIR, "e2e_data")
    server = ps_lib.start_server(ps_lib.pick_port(preferred), data_dir)
    return server, server.base, "本脚本启动实例（数据目录 %s）" % data_dir


def main():
    ps_lib.ensure_dir(SHOT_DIR)
    ps_lib.ensure_dir(RUNTIME_DIR)
    image_path = make_assets()
    server = None
    results = []
    note = ""
    try:
        server, base, note = ensure_server()
        print("E2E 目标:", base, "|", note)
        from playwright.sync_api import sync_playwright

        headed = os.environ.get("PS_E2E_HEADED") == "1"
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=not headed)
            try:
                profiles = [
                    ("mobile", {"width": 375, "height": 900}, True),
                    ("desktop", {"width": 1440, "height": 900}, False),
                ]
                for label, viewport, mobile in profiles:
                    results.append(run_flow(browser, base, label, viewport, mobile,
                                            image_path))
            finally:
                browser.close()
    finally:
        if server is not None:
            server.stop()

    print("\n== E2E 汇总 ==")
    print("PASS:", len(PASS), " FAIL:", len(FAIL))
    for name, detail in FAIL:
        print(" FAILED:", name, "|", str(detail)[:300])
    summary = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base": base if "base" in locals() else "",
        "note": note,
        "pass": len(PASS),
        "fail": len(FAIL),
        "failures": [{"name": n, "detail": str(d)} for n, d in FAIL],
        "profiles": results,
        "screenshots": sorted(os.listdir(SHOT_DIR)),
    }
    try:
        with open(RESULT_PATH, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print("结果 JSON:", RESULT_PATH)
    except Exception as e:  # noqa: BLE001
        print("写结果 JSON 失败:", e)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
