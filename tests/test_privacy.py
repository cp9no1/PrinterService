# -*- coding: utf-8 -*-
"""隐私隔离测试：不同浏览器会话互相不可见；管理员视图可看全部；非管理员 403。

安全约定：不触发真实打印（失败日志通过「不存在的打印机」校验失败路径产生）。
"""
import os

import pytest
import requests

import ps_lib


def upload_ok(sess, base, path):
    r = ps_lib.upload(sess, base, path)
    assert r.status_code == 200 and r.json().get("success") is True, r.text[:300]
    return r.json()


def _prepare_session_pair(base, prefix, tmpdir):
    """两个独立 cookie 会话；A 上传图片 + 文本并产生一条失败打印日志。"""
    a, b = requests.Session(), requests.Session()
    sid_a = a.get(base + "/api/session", timeout=20).json()["sid"]
    sid_b = b.get(base + "/api/session", timeout=20).json()["sid"]
    assert sid_a != sid_b, "两个浏览器会话应获得不同 sid"

    image_name = "%s图片.jpg" % prefix
    text_name = "%s笔记.txt" % prefix
    ps_lib.make_image(os.path.join(tmpdir, image_name), size=(800, 600))
    upload_ok(a, base, os.path.join(tmpdir, image_name))
    ps_lib.make_txt(os.path.join(tmpdir, text_name), text="%s 的隐私文本\n" % prefix)
    upload_ok(a, base, os.path.join(tmpdir, text_name))

    # 失败打印日志（打印机校验在 GDI 打印之前失败，不会真实打印）
    a.post(base + "/print_single",
           json={"name": image_name, "printer": "不存在的打印机 XYZ"}, timeout=30)

    return {
        "a": a, "b": b, "sid_a": sid_a, "sid_b": sid_b,
        "image": image_name, "text": text_name,
    }


@pytest.fixture(scope="module")
def admin_pair(admin_base):
    tmpdir = ps_lib.ensure_dir(os.path.join(ps_lib.RUNTIME_DIR, "privacy_admin_files"))
    pair = _prepare_session_pair(admin_base, "隐私A_", tmpdir)
    pair["base"] = admin_base
    try:
        yield pair
    finally:
        for key in ("a", "b"):
            pair[key].close()


@pytest.fixture(scope="module")
def nonadmin_pair(nonadmin_base):
    tmpdir = ps_lib.ensure_dir(os.path.join(ps_lib.RUNTIME_DIR, "privacy_nonadmin_files"))
    pair = _prepare_session_pair(nonadmin_base, "隐私C_", tmpdir)
    pair["base"] = nonadmin_base
    try:
        yield pair
    finally:
        for key in ("a", "b"):
            pair[key].close()


# ---------------------------------------------------------------------------
# 管理员实例：会话之间互不可见
# ---------------------------------------------------------------------------
def test_files_isolated_between_sessions(admin_pair):
    base = admin_pair["base"]
    files_a = admin_pair["a"].get(base + "/api/files", timeout=20).json()["files"]
    files_b = admin_pair["b"].get(base + "/api/files", timeout=20).json()["files"]
    names_a = {f["name"] for f in files_a}
    assert admin_pair["image"] in names_a and admin_pair["text"] in names_a, names_a
    assert files_b == [], "B 会话不应看到 A 的文件: %r" % files_b


@pytest.mark.parametrize("endpoint", [
    "/preview/{name}",
    "/api/render/{name}/1",
    "/api/page_count/{name}",
    "/api/image_layout/{name}",
    "/api/excel_info/{name}",
])
def test_b_cannot_access_a_files(admin_pair, endpoint):
    base = admin_pair["base"]
    name = admin_pair["image"]
    url = base + endpoint.format(name=name)
    r = admin_pair["b"].get(url, timeout=20)
    if endpoint.startswith("/preview/"):
        assert r.status_code == 404, (endpoint, r.status_code)
    else:
        body = r.json()
        assert body.get("success") is False, (endpoint, body)
        assert r.status_code in (200, 404), (endpoint, r.status_code)


def test_b_cannot_delete_a_file(admin_pair):
    base = admin_pair["base"]
    r = admin_pair["b"].post(base + "/delete_file",
                             json={"name": admin_pair["image"]}, timeout=20)
    assert r.json().get("success") is False, r.text[:300]
    files_a = admin_pair["a"].get(base + "/api/files", timeout=20).json()["files"]
    assert any(f["name"] == admin_pair["image"] for f in files_a), "A 的文件不应被 B 删除"


def test_logs_isolated_between_sessions(admin_pair):
    base = admin_pair["base"]
    logs_a = admin_pair["a"].get(base + "/api/logs?limit=20", timeout=20).json()["logs"]
    logs_b = admin_pair["b"].get(base + "/api/logs?limit=20", timeout=20).json()["logs"]
    assert any(x.get("display_name") == admin_pair["image"] for x in logs_a), logs_a
    assert "失败" in next(x["status"] for x in logs_a
                          if x.get("display_name") == admin_pair["image"])
    assert logs_b == [], "B 会话不应看到 A 的打印记录: %r" % logs_b


# ---------------------------------------------------------------------------
# 管理员视图：/api/files/all 与 ?sid= 跨会话访问
# ---------------------------------------------------------------------------
def test_admin_sees_all_sessions(admin_pair):
    base = admin_pair["base"]
    body = admin_pair["a"].get(base + "/api/files/all", timeout=30).json()
    assert body.get("success") is True
    sessions = body.get("sessions") or []
    entry = next((s for s in sessions if s.get("sid") == admin_pair["sid_a"]), None)
    assert entry is not None, sessions
    names = {f["name"] for f in entry.get("files", [])}
    assert admin_pair["image"] in names and admin_pair["text"] in names, names
    # B 会话（无文件）不应被当作有文件的会话展示
    b_entry = next((s for s in sessions if s.get("sid") == admin_pair["sid_b"]), None)
    assert b_entry is None or not b_entry.get("files")


def test_admin_can_access_other_session_by_sid(admin_pair):
    base = admin_pair["base"]
    sid = admin_pair["sid_a"]
    image = admin_pair["image"]

    preview = admin_pair["b"].get(base + "/preview/%s?sid=%s" % (image, sid), timeout=20)
    assert preview.status_code == 200
    assert "application/pdf" in preview.headers.get("Content-Type", "")

    render = admin_pair["b"].get(base + "/api/render/%s/1?sid=%s" % (image, sid), timeout=30)
    assert render.status_code == 200 and render.content[:8] == b"\x89PNG\r\n\x1a\n"

    layout = admin_pair["b"].get(
        base + "/api/image_layout/%s?sid=%s" % (image, sid), timeout=30)
    assert layout.status_code == 200 and layout.content[:4] == b"\x89PNG"

    page_count = admin_pair["b"].get(
        base + "/api/page_count/%s?sid=%s" % (image, sid), timeout=20).json()
    assert page_count.get("success") is True and page_count.get("page_count") == 1


# ---------------------------------------------------------------------------
# 普通用户视图（PRINTSERVER_ADMIN_DISABLED=1）
# ---------------------------------------------------------------------------
def test_nonadmin_session_flags(nonadmin_pair):
    base = nonadmin_pair["base"]
    body = nonadmin_pair["a"].get(base + "/api/session", timeout=20).json()
    assert body.get("is_admin") is False
    assert body.get("success") is True


@pytest.mark.parametrize("method,path,json_body", [
    ("get", "/api/files/all", None),
    ("get", "/api/qr", None),
    ("post", "/api/admin/clean", {}),
])
def test_nonadmin_forbidden_endpoints(nonadmin_pair, method, path, json_body):
    base = nonadmin_pair["base"]
    r = getattr(nonadmin_pair["a"], method)(base + path, json=json_body, timeout=20)
    assert r.status_code == 403, (path, r.status_code, r.text[:200])
    assert r.json().get("success") is False


def test_nonadmin_logs_do_not_leak(nonadmin_pair):
    base = nonadmin_pair["base"]
    logs_b = nonadmin_pair["b"].get(base + "/api/logs?limit=50&all=1", timeout=20).json()
    assert logs_b.get("is_admin") is False
    assert logs_b.get("logs") == [], "普通用户即使传 all=1 也不应看到他人记录: %r" % logs_b


def test_nonadmin_cannot_use_sid_param(nonadmin_pair):
    base = nonadmin_pair["base"]
    sid = nonadmin_pair["sid_a"]
    image = nonadmin_pair["image"]

    preview = nonadmin_pair["b"].get(base + "/preview/%s?sid=%s" % (image, sid),
                                     timeout=20)
    assert preview.status_code == 404, preview.status_code
    assert "无权" in preview.text or "不存在" in preview.text

    render = nonadmin_pair["b"].get(base + "/api/render/%s/1?sid=%s" % (image, sid),
                                    timeout=20)
    assert render.status_code == 404
    assert render.json().get("success") is False

    delete = nonadmin_pair["b"].post(base + "/delete_file",
                                     json={"name": image, "sid": sid}, timeout=20)
    assert delete.json().get("success") is False
    files_a = nonadmin_pair["a"].get(base + "/api/files", timeout=20).json()["files"]
    assert any(f["name"] == image for f in files_a), "普通用户不应能跨会话删除"


def test_nonadmin_admin_clean_denied_keeps_files(nonadmin_pair):
    base = nonadmin_pair["base"]
    r = nonadmin_pair["b"].post(base + "/api/admin/clean", json={}, timeout=20)
    assert r.status_code == 403
    files_a = nonadmin_pair["a"].get(base + "/api/files", timeout=20).json()["files"]
    assert len(files_a) >= 2, files_a
