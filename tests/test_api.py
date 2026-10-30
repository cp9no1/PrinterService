# -*- coding: utf-8 -*-
"""API 集成测试：对隔离启动的 PrintServer 实例发真实 HTTP 请求。

安全约定：本文件绝不向真实打印机提交打印任务。打印相关用例只覆盖
「参数校验失败」路径（在创建 GDI 打印任务前就返回错误）。
"""
import os
import re

import pytest
import requests

import ps_lib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PAPER_SIZES = ["A4", "A5", "Letter", "4R", "5R"]


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def upload_ok(sess, base, path, filename=None, timeout=300):
    r = ps_lib.upload(sess, base, path, filename, timeout=timeout)
    assert r.status_code == 200, "upload HTTP %s: %s" % (r.status_code, r.text[:500])
    body = r.json()
    assert body.get("success") is True, "upload 失败: %r" % body
    return body


def upload_as(sess, base, path, filename, timeout=300):
    """上传但不要求成功（用于不支持类型等负向用例）。"""
    return ps_lib.upload(sess, base, path, filename, timeout=timeout)


def assert_png(response, min_bytes=1000):
    assert response.status_code == 200, "HTTP %s: %s" % (response.status_code,
                                                        response.text[:300])
    assert response.headers.get("Content-Type", "").startswith("image/png"), \
        response.headers.get("Content-Type")
    assert response.content[:8] == PNG_MAGIC, response.content[:20]
    assert len(response.content) > min_bytes
    return response.content


def json_of(response):
    try:
        return response.json()
    except ValueError:
        pytest.fail("响应不是 JSON（HTTP %s）: %r" % (response.status_code,
                                                     response.text[:300]))


def find_file(files, name):
    return next((f for f in files if f.get("name") == name), None)


# ---------------------------------------------------------------------------
# 基础 / 页面 / 会话
# ---------------------------------------------------------------------------
def test_health(admin_base):
    r = requests.get(admin_base + "/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert body.get("version") == "3.0"


def test_index_page_renders_and_static_files(admin_base):
    r = requests.get(admin_base + "/", timeout=20)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("Content-Type", "")
    html = r.text
    assert "内网打印服务" in html
    assert '<html lang="zh-cn">' in html
    assert "PRINTSERVER_BOOT" in html
    assert "/static/app.js" in html and "/static/app.css" in html
    assert "{{" not in html, "Jinja 模板变量未被渲染"
    for path, ctype in (("/static/app.js", "javascript"),
                        ("/static/app.css", "text/css"),
                        ("/static/favicon.ico", "image")):
        resp = requests.get(admin_base + path, timeout=20)
        assert resp.status_code == 200, path
        assert ctype in resp.headers.get("Content-Type", ""), (path,
                                                               resp.headers.get("Content-Type"))
        assert len(resp.content) > 500, path


def test_session_contract_impl(admin, admin_base):
    r = admin.get(admin_base + "/api/session", timeout=20)
    assert r.status_code == 200
    body = json_of(r)
    assert body.get("success") is True
    assert re.match(r"^[0-9a-f]{32}$", body.get("sid") or ""), body.get("sid")
    assert body.get("version") == "3.0"
    assert body.get("is_admin") is True
    server = body.get("server") or {}
    assert isinstance(server.get("ip"), str) and server.get("ip")
    assert isinstance(server.get("port"), int) and server.get("port") > 0
    assert body.get("max_upload_mb") == 200
    env = body.get("env") or {}
    for key in ("pymupdf", "office", "wps", "libreoffice"):
        assert key in env, key
    assert env.get("pymupdf") is True
    assert body.get("paper_sizes") == PAPER_SIZES
    assert isinstance(body.get("printers"), list)
    assert isinstance(body.get("default_printer"), str)
    cookie = admin.cookies.get("printserver_sid")
    assert cookie, "缺少 printserver_sid 会话 cookie"
    # sid 在后续请求中保持稳定
    body2 = admin.get(admin_base + "/api/session", timeout=20).json()
    assert body2.get("sid") == body.get("sid")


def test_two_browsers_get_different_sids(admin_base):
    a, b = requests.Session(), requests.Session()
    try:
        sid_a = a.get(admin_base + "/api/session", timeout=20).json()["sid"]
        sid_b = b.get(admin_base + "/api/session", timeout=20).json()["sid"]
        assert sid_a != sid_b
    finally:
        a.close()
        b.close()


def test_printers_api(admin, admin_base):
    body = json_of(admin.get(admin_base + "/api/printers", timeout=30))
    assert body.get("success") is True
    printers = body.get("printers")
    assert isinstance(printers, list)
    assert all(isinstance(p, str) and p.strip() for p in printers)
    default = body.get("default") or ""
    if printers:
        assert isinstance(default, str)
        if default:
            assert default in printers


def test_qr_admin(admin_base):
    r = requests.get(admin_base + "/api/qr", timeout=30)
    assert_png(r, min_bytes=200)
    assert "X-QR-URL" in r.headers, "二维码接口应返回 X-QR-URL 头"


def test_unknown_api_returns_json_404(admin_base):
    r = requests.get(admin_base + "/api/not-exist-xyz", timeout=10)
    assert r.status_code == 404
    body = json_of(r)
    assert body.get("success") is False
    assert "接口不存在" in body.get("message", "")


# ---------------------------------------------------------------------------
# 上传：图片 / PDF / 文本 / Excel / DOCX / 中文名 / 非法输入
# ---------------------------------------------------------------------------
def test_upload_image_chinese_name(admin, admin_base, tmp_path):
    path = os.path.join(str(tmp_path), "中文照片测试.jpg")
    ps_lib.make_image(path, size=(1600, 1200))
    body = upload_ok(admin, admin_base, path)
    assert body["name"] == "中文照片测试.jpg"
    assert body["display_name"] == "中文照片测试.jpg"
    assert body["kind"] == "image"
    assert body["converted"] is True
    assert body["page_count"] == 1
    assert body["status"] == "已就绪"
    assert body["pdf_name"] == "中文照片测试.pdf"
    assert body["size"] > 0

    files = json_of(admin.get(admin_base + "/api/files", timeout=20))["files"]
    entry = find_file(files, "中文照片测试.jpg")
    assert entry is not None
    for key in ("display_name", "size", "created_time", "kind", "converted",
                "pdf_name", "page_count", "status", "status_color"):
        assert key in entry, key
    assert entry["converted"] is True
    assert entry["status_color"] == "success"
    assert entry["created_time"]


def test_upload_pdf(admin, admin_base, tmp_path):
    path = ps_lib.make_pdf(os.path.join(str(tmp_path), "三页文档.pdf"), pages=3)
    body = upload_ok(admin, admin_base, path)
    assert body["kind"] == "pdf"
    assert body["converted"] is True
    assert body["page_count"] == 3
    assert body["pdf_name"] == "三页文档.pdf"


def test_upload_text_and_preview(admin, admin_base, tmp_path):
    path = ps_lib.make_txt(os.path.join(str(tmp_path), "测试笔记.txt"))
    body = upload_ok(admin, admin_base, path)
    assert body["kind"] == "text"
    assert body["converted"] is True
    assert body["page_count"] >= 1
    # 契约：/preview 在有转换后 PDF 时优先返回 PDF
    preview = admin.get(admin_base + "/preview/测试笔记.txt", timeout=20)
    assert preview.status_code == 200
    assert "application/pdf" in preview.headers.get("Content-Type", "")
    # 渲染接口可正常出图
    png = admin.get(admin_base + "/api/render/测试笔记.txt/1?dpi=100", timeout=30)
    assert_png(png)


def test_upload_xlsx_and_excel_apis(admin, admin_base, tmp_path, office_available):
    path = ps_lib.make_xlsx(os.path.join(str(tmp_path), "表格测试.xlsx"), sheets=2)
    body = upload_ok(admin, admin_base, path)
    assert body["kind"] == "sheet"
    assert body["converted"] is office_available, \
        "Excel 转换结果与转换引擎能力不一致: %r" % body
    if not office_available:
        pytest.skip("无 Office/WPS/LibreOffice 转换引擎，跳过 Excel 内容断言")

    info = json_of(admin.get(admin_base + "/api/excel_info/表格测试.xlsx", timeout=30))
    assert info.get("success") is True, info
    assert info.get("sheets") == ["Sheet1", "数据2"], info.get("sheets")
    assert info.get("sheet_count") == 2

    preview = json_of(admin.get(admin_base + "/api/excel_preview/表格测试.xlsx?sheet=Sheet1",
                                timeout=30))
    assert preview.get("success") is True, preview
    assert preview.get("rows"), preview
    assert preview["rows"][0][0] == "R1C1"
    assert preview["col_count"] >= 10
    assert len(preview["headers"]) == preview["col_count"]


def test_upload_docx(admin, admin_base, tmp_path, office_available):
    path = ps_lib.make_docx(os.path.join(str(tmp_path), "文档测试.docx"))
    body = upload_ok(admin, admin_base, path)
    assert body["kind"] == "doc"
    assert body["converted"] is office_available, \
        "DOCX 转换结果与转换引擎能力不一致: %r" % body
    if office_available:
        assert body["page_count"] >= 1


@pytest.mark.parametrize("filename", ["病毒样本.exe", "noext", "压缩包.zip"])
def test_upload_rejects_unsupported_type(admin, admin_base, tmp_path, filename):
    path = os.path.join(str(tmp_path), "dummy.bin")
    with open(path, "wb") as f:
        f.write(b"not a real file")
    r = upload_as(admin, admin_base, path, filename)
    assert r.status_code == 200
    body = json_of(r)
    assert body.get("success") is False, body
    assert "不支持" in body.get("message", "")


def test_upload_without_file_field(admin, admin_base):
    r = admin.post(admin_base + "/upload", data={"foo": "bar"}, timeout=20)
    body = json_of(r)
    assert body.get("success") is False
    assert "没有文件" in body.get("message", "")


def test_upload_filename_path_traversal_sanitized(admin, admin_base, tmp_path,
                                                 admin_server):
    path = ps_lib.make_txt(os.path.join(str(tmp_path), "safe.txt"))
    r = upload_as(admin, admin_base, path, "../../逃逸文件.txt")
    assert r.status_code == 200
    body = json_of(r)
    assert body.get("success") is True, body
    assert body["name"] == "逃逸文件.txt", body["name"]
    assert "/" not in body["name"] and "\\" not in body["name"]
    assert not os.path.exists(os.path.join(admin_server.data_dir, "uploads",
                                           "逃逸文件.txt")), "文件被写到会话目录之外"


def test_upload_same_name_overwrites(admin, admin_base, tmp_path):
    path = os.path.join(str(tmp_path), "覆盖测试.txt")
    ps_lib.make_txt(path, text="第一版内容\n")
    upload_ok(admin, admin_base, path)
    first_preview = admin.get(admin_base + "/preview/覆盖测试.txt", timeout=20).content

    ps_lib.make_txt(path, text="第二版内容：这是一个更长一些的覆盖测试文本，用来确认同名上传会覆盖旧文件。\n")
    upload_ok(admin, admin_base, path)

    files = json_of(admin.get(admin_base + "/api/files", timeout=20))["files"]
    matches = [f for f in files if f["name"] == "覆盖测试.txt"]
    assert len(matches) == 1
    assert matches[0]["size"] == os.path.getsize(path)
    second_preview = admin.get(admin_base + "/preview/覆盖测试.txt", timeout=20).content
    assert first_preview != second_preview, "同名覆盖后转换出的 PDF 应已更新"


# ---------------------------------------------------------------------------
# page_count / render / image_layout
# ---------------------------------------------------------------------------
def test_page_count_api(admin, admin_base, tmp_path):
    path = ps_lib.make_pdf(os.path.join(str(tmp_path), "页数.pdf"), pages=4)
    body = upload_ok(admin, admin_base, path)
    assert body["page_count"] == 4

    r = admin.get(admin_base + "/api/page_count/页数.pdf", timeout=20)
    j = json_of(r)
    assert j.get("success") is True and j.get("page_count") == 4
    assert j.get("pdf_name") == "页数.pdf"

    # 也接受 pdf 存储名
    r2 = admin.get(admin_base + "/api/page_count/" + body["pdf_name"], timeout=20)
    assert json_of(r2).get("page_count") == 4

    missing = admin.get(admin_base + "/api/page_count/不存在.pdf", timeout=20)
    assert missing.status_code == 404
    assert json_of(missing).get("success") is False


def test_render_png(admin, admin_base, tmp_path):
    path = ps_lib.make_pdf(os.path.join(str(tmp_path), "渲染.pdf"), pages=3)
    upload_ok(admin, admin_base, path)

    png = assert_png(admin.get(admin_base + "/api/render/渲染.pdf/1?dpi=100", timeout=30))
    size, bbox = ps_lib.content_bbox(png)
    assert ps_lib.approx(size[0], 210 / 25.4 * 100, 3), size
    assert ps_lib.approx(size[1], 297 / 25.4 * 100, 3), size
    assert bbox is not None

    png3 = assert_png(admin.get(admin_base + "/api/render/渲染.pdf/3?dpi=100", timeout=30))
    assert png3 != png, "不同页渲染结果不应完全相同"

    out_of_range = admin.get(admin_base + "/api/render/渲染.pdf/4", timeout=20)
    assert out_of_range.status_code == 500
    assert json_of(out_of_range).get("success") is False

    missing = admin.get(admin_base + "/api/render/不存在.pdf/1", timeout=20)
    assert missing.status_code == 404

    # dpi 参数被限制在 60~240
    big = assert_png(admin.get(admin_base + "/api/render/渲染.pdf/1?dpi=9999", timeout=30))
    size_big, _ = ps_lib.content_bbox(big)
    assert size_big[0] <= 210 / 25.4 * 240 + 4, size_big


def _upload_layout_image(admin, admin_base, tmp_path):
    path = os.path.join(str(tmp_path), "版式照片.jpg")
    ps_lib.make_image(path, size=(2000, 1000))  # 宽高比 2:1，整幅非白
    upload_ok(admin, admin_base, path)
    return "版式照片.jpg"


def _layout(admin, admin_base, name, **params):
    query = "&".join("%s=%s" % (k, v) for k, v in params.items())
    return admin.get(admin_base + "/api/image_layout/%s?%s" % (name, query), timeout=60)


def test_image_layout_fit_fill_actual(admin, admin_base, tmp_path):
    name = _upload_layout_image(admin, admin_base, tmp_path)
    dpi = 100
    page_w_px = 210 / 25.4 * dpi
    page_h_px = 297 / 25.4 * dpi
    margin_px = 5 / 25.4 * dpi

    fit_png = assert_png(_layout(admin, admin_base, name, fit="fit", rotate=0,
                                 margin=5, paper="A4",
                                 page_orientation="portrait", dpi=dpi))
    fit_size, fit_bbox = ps_lib.content_bbox(fit_png)
    assert ps_lib.approx(fit_size[0], page_w_px, 3), fit_size
    assert ps_lib.approx(fit_size[1], page_h_px, 3), fit_size
    content_w = page_w_px - 2 * margin_px
    draw_h = content_w / 2.0
    assert ps_lib.approx(fit_bbox[2] - fit_bbox[0], content_w, 3), fit_bbox
    assert ps_lib.approx(fit_bbox[3] - fit_bbox[1], draw_h, 3), fit_bbox
    assert ps_lib.approx(fit_bbox[0], margin_px, 2), fit_bbox

    fill_png = assert_png(_layout(admin, admin_base, name, fit="fill", rotate=0,
                                  margin=0, paper="A4",
                                  page_orientation="portrait", dpi=dpi))
    fill_size, fill_bbox = ps_lib.content_bbox(fill_png)
    assert fill_size == fit_size
    assert ps_lib.approx(fill_bbox[0], 0, 2) and ps_lib.approx(fill_bbox[1], 0, 2)
    assert ps_lib.approx(fill_bbox[2], fill_size[0], 2)
    assert ps_lib.approx(fill_bbox[3], fill_size[1], 2)
    assert fit_png != fill_png, "fit 与 fill 预览图不应相同"
    assert fill_bbox[2] - fill_bbox[0] > fit_bbox[2] - fit_bbox[0]

    # 原始大小：2000x1000 -> 96dpi 下 1500x750pt，A4 内容区放不下，会缩小到内容区
    actual_png = assert_png(_layout(admin, admin_base, name, fit="actual", rotate=0,
                                    margin=5, paper="A4",
                                    page_orientation="portrait", dpi=dpi))
    _actual_size, actual_bbox = ps_lib.content_bbox(actual_png)
    assert actual_bbox[2] - actual_bbox[0] <= content_w + 3


def test_image_layout_rotate(admin, admin_base, tmp_path):
    name = _upload_layout_image(admin, admin_base, tmp_path)
    png0 = assert_png(_layout(admin, admin_base, name, fit="fit", rotate=0,
                              margin=5, paper="A4", page_orientation="portrait", dpi=100))
    png90 = assert_png(_layout(admin, admin_base, name, fit="fit", rotate=90,
                               margin=5, paper="A4", page_orientation="portrait", dpi=100))
    _size0, bbox0 = ps_lib.content_bbox(png0)
    _size90, bbox90 = ps_lib.content_bbox(png90)
    ratio0 = (bbox0[2] - bbox0[0]) / float(bbox0[3] - bbox0[1])
    ratio90 = (bbox90[2] - bbox90[0]) / float(bbox90[3] - bbox90[1])
    assert ps_lib.approx(ratio0, 2.0, 0.05), bbox0
    assert ps_lib.approx(ratio90, 0.5, 0.02), bbox90
    assert png0 != png90


@pytest.mark.parametrize("paper,orientation,expected_mm", [
    ("A4", "portrait", (210.0, 297.0)),
    ("A5", "portrait", (148.0, 210.0)),
    ("4R", "portrait", (102.0, 152.0)),
    ("5R", "portrait", (127.0, 178.0)),
    ("A4", "landscape", (297.0, 210.0)),
])
def test_image_layout_paper_sizes(admin, admin_base, tmp_path, paper, orientation,
                                  expected_mm):
    name = _upload_layout_image(admin, admin_base, tmp_path)
    png = assert_png(_layout(admin, admin_base, name, fit="fit", rotate=0, margin=5,
                             paper=paper, page_orientation=orientation, dpi=100))
    size, bbox = ps_lib.content_bbox(png)
    assert ps_lib.approx(size[0], expected_mm[0] / 25.4 * 100, 3), (paper, orientation, size)
    assert ps_lib.approx(size[1], expected_mm[1] / 25.4 * 100, 3), (paper, orientation, size)
    assert bbox is not None


def test_image_layout_margin_effect(admin, admin_base, tmp_path):
    name = _upload_layout_image(admin, admin_base, tmp_path)
    png5 = assert_png(_layout(admin, admin_base, name, fit="fit", rotate=0, margin=5,
                              paper="A4", page_orientation="portrait", dpi=100))
    png20 = assert_png(_layout(admin, admin_base, name, fit="fit", rotate=0, margin=20,
                               paper="A4", page_orientation="portrait", dpi=100))
    _s5, bbox5 = ps_lib.content_bbox(png5)
    _s20, bbox20 = ps_lib.content_bbox(png20)
    width5 = bbox5[2] - bbox5[0]
    width20 = bbox20[2] - bbox20[0]
    assert width5 - width20 > 80, (width5, width20)
    assert ps_lib.approx(bbox20[0], 20 / 25.4 * 100, 2)


def test_image_layout_rejects_non_image(admin, admin_base, tmp_path):
    path = ps_lib.make_txt(os.path.join(str(tmp_path), "不是图片.txt"))
    upload_ok(admin, admin_base, path)
    r = admin.get(admin_base + "/api/image_layout/不是图片.txt", timeout=20)
    body = json_of(r)
    assert body.get("success") is False
    assert "不是图片" in body.get("message", "")


# ---------------------------------------------------------------------------
# 预览 / 删除
# ---------------------------------------------------------------------------
def test_preview_image_pdf_text(admin, admin_base, tmp_path):
    image_path = os.path.join(str(tmp_path), "预览图.png")
    ps_lib.make_image(image_path, size=(400, 300))
    image_body = upload_ok(admin, admin_base, image_path)
    r = admin.get(admin_base + "/preview/预览图.png", timeout=20)
    assert r.status_code == 200
    # 契约：有转换后 PDF 时 preview 返回 PDF，否则返回源图片
    if image_body["converted"]:
        assert "application/pdf" in r.headers.get("Content-Type", "")
    else:
        assert r.headers.get("Content-Type", "").startswith("image/")

    pdf_name = "预览文档.pdf"
    ps_lib.make_pdf(os.path.join(str(tmp_path), pdf_name), pages=1)
    upload_ok(admin, admin_base, os.path.join(str(tmp_path), pdf_name))
    r = admin.get(admin_base + "/preview/" + pdf_name, timeout=20)
    assert r.status_code == 200
    assert "application/pdf" in r.headers.get("Content-Type", "")

    txt_name = "预览文本.txt"
    ps_lib.make_txt(os.path.join(str(tmp_path), txt_name))
    txt_body = upload_ok(admin, admin_base, os.path.join(str(tmp_path), txt_name))
    r = admin.get(admin_base + "/preview/" + txt_name, timeout=20)
    if txt_body["converted"]:
        assert r.status_code == 200 and "application/pdf" in r.headers.get("Content-Type", "")
    else:
        assert r.status_code == 200 and "text/plain" in r.headers.get("Content-Type", "")

    missing = admin.get(admin_base + "/preview/没有这个文件.pdf", timeout=20)
    assert missing.status_code == 404

    traversal = admin.get(admin_base + "/preview/..%2F..%2Fsecret.txt", timeout=20)
    assert traversal.status_code in (400, 404), traversal.status_code
    assert "secret" not in traversal.text.lower() or "文件" in traversal.text


def test_delete_file(admin, admin_base, tmp_path):
    name = "待删除文件.txt"
    upload_ok(admin, admin_base, ps_lib.make_txt(os.path.join(str(tmp_path), name)))
    files = json_of(admin.get(admin_base + "/api/files", timeout=20))["files"]
    assert find_file(files, name)

    r = admin.post(admin_base + "/delete_file", json={"name": name}, timeout=20)
    body = json_of(r)
    assert body.get("success") is True, body
    files = json_of(admin.get(admin_base + "/api/files", timeout=20))["files"]
    assert find_file(files, name) is None

    again = json_of(admin.post(admin_base + "/delete_file", json={"name": name}, timeout=20))
    assert again.get("success") is False
    assert "不存在" in again.get("message", "")

    empty = json_of(admin.post(admin_base + "/delete_file", json={}, timeout=20))
    assert empty.get("success") is False
    assert "文件名" in empty.get("message", "")


# ---------------------------------------------------------------------------
# 上传大小限制（独立小上限实例）
# ---------------------------------------------------------------------------
def test_upload_413_over_limit(small_base, tmp_path):
    sess = requests.Session()
    try:
        session = sess.get(small_base + "/api/session", timeout=20).json()
        assert session.get("max_upload_mb") == 1, session.get("max_upload_mb")

        # 小文件应正常上传，证明实例本身可用
        ok = ps_lib.upload(sess, small_base, ps_lib.make_txt(
            os.path.join(str(tmp_path), "小文件.txt")))
        assert ok.status_code == 200 and ok.json().get("success") is True

        big = ps_lib.make_big_file(os.path.join(str(tmp_path), "超大文件.jpg"),
                                   1536 * 1024)
        r = ps_lib.upload(sess, small_base, big, timeout=120)
        assert r.status_code == 413, "期望 413，实际 %s: %s" % (r.status_code,
                                                              r.text[:300])
        body = r.json()
        assert body.get("success") is False
        assert "大小限制" in body.get("message", ""), body
    finally:
        sess.close()


# ---------------------------------------------------------------------------
# 打印参数校验（不产生真实打印）
# ---------------------------------------------------------------------------
def test_print_single_validation_only(admin, admin_base, tmp_path):
    name = "打印参数.txt"
    upload_ok(admin, admin_base, ps_lib.make_txt(os.path.join(str(tmp_path), name)))

    missing = json_of(admin.post(admin_base + "/print_single", json={}, timeout=20))
    assert missing.get("success") is False
    assert "缺少文件名" in missing.get("message", "")

    no_file = admin.post(admin_base + "/print_single",
                         json={"name": "不存在文件.txt"}, timeout=20)
    assert no_file.status_code == 404
    assert json_of(no_file).get("success") is False

    bogus = json_of(admin.post(admin_base + "/print_single",
                               json={"name": name, "printer": "不存在的打印机 XYZ"},
                               timeout=30))
    assert bogus.get("success") is False
    assert "打印机" in bogus.get("message", "")

    bad_area = json_of(admin.post(admin_base + "/print_single",
                                  json={"name": name, "printer": "不存在的打印机 XYZ",
                                        "print_area": "Sheet1!A1"}, timeout=30))
    assert bad_area.get("success") is False
    # 先校验打印区域（与打印机校验顺序无关，二者都在真实打印前失败）
    assert ("打印区域" in bad_area.get("message", "")
            or "打印机" in bad_area.get("message", "")), bad_area

    bad_area2 = json_of(admin.post(admin_base + "/print_single",
                                   json={"name": name, "print_area": "!!!bad!!!"},
                                   timeout=30))
    assert bad_area2.get("success") is False
    assert "打印区域" in bad_area2.get("message", ""), bad_area2


def test_print_all_validation_only(admin, admin_base):
    bogus = json_of(admin.post(admin_base + "/print_all",
                               json={"printer": "不存在的打印机 XYZ"}, timeout=30))
    assert bogus.get("success") is False
    assert "打印机" in bogus.get("message", "")

    # 全新空会话：参数合法（用真实默认打印机）但无文件 -> 不会触发任何打印
    sess = requests.Session()
    try:
        session = sess.get(admin_base + "/api/session", timeout=20).json()
        printer = session.get("default_printer") or ""
        if not printer:
            pytest.skip("没有默认打印机，跳过空会话 /print_all 用例")
        body = json_of(sess.post(admin_base + "/print_all",
                                 json={"printer": printer}, timeout=30))
        assert body.get("success") is False
        assert "没有可打印的文件" in body.get("message", ""), body
        # 确认没有新增打印日志（未发生真实打印）
        logs = json_of(sess.get(admin_base + "/api/logs", timeout=20)).get("logs", [])
        assert logs == [], logs
    finally:
        sess.close()


def test_logs_after_validation_failure(admin, admin_base, tmp_path):
    name = "日志测试.txt"
    upload_ok(admin, admin_base, ps_lib.make_txt(os.path.join(str(tmp_path), name)))
    admin.post(admin_base + "/print_single",
               json={"name": name, "printer": "不存在的打印机 XYZ"}, timeout=30)
    body = json_of(admin.get(admin_base + "/api/logs?limit=20", timeout=20))
    assert body.get("success") is True
    assert body.get("is_admin") is True
    logs = body.get("logs") or []
    entry = next((x for x in logs if x.get("display_name") == name), None)
    assert entry is not None, logs
    assert "失败" in entry.get("status", ""), entry
    assert entry.get("time")

    limited = json_of(admin.get(admin_base + "/api/logs?limit=1", timeout=20))
    assert len(limited.get("logs", [])) <= 1
