# -*- coding: utf-8 -*-
"""打印链路单元测试（绝不产生真实打印任务）。

覆盖：
- parse_page_range 页码范围解析
- _validate_print_request 打印参数校验（合法 / 非法）
- build_image_pdf 图片版式（A4/A5/4R/Letter/5R、fit/fill/actual、rotate、margin）
- _normalize_image_layout / _normalize_paper
- silent_print_pdf：mock 打印机 DC 与 Drawing 层，验证参数传递与分页逻辑
"""
import io
import os
import sys
import types

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
for _p in (PROJECT_DIR, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ps_lib  # noqa: E402
import print_server as ps  # noqa: E402

fitz = ps.fitz
PILImage = ps.PILImage
PAPER_MM = ps.PAPER_SIZES_MM
MM_TO_PT = 72.0 / 25.4


def mm2pt(mm):
    return mm * MM_TO_PT


# ---------------------------------------------------------------------------
# 页码范围解析
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,total,expected", [
    ("all", 3, [0, 1, 2]),
    ("", 3, [0, 1, 2]),
    (None, 2, [0, 1]),
    ("1", 3, [0]),
    ("2", 3, [1]),
    ("1-3", 3, [0, 1, 2]),
    ("1-5,7", 7, [0, 1, 2, 3, 4, 6]),
    ("7,1-2", 7, [0, 1, 6]),
    ("3-1", 3, [0, 1, 2]),            # 反向区间自动纠正
    ("1-100", 4, [0, 1, 2, 3]),       # 超出页数自动截断
    ("0", 3, [0, 1, 2]),              # 全部非法时回退为全部页
    ("9", 3, [0, 1, 2]),
    ("abc", 3, [0, 1, 2]),
    ("1,,2", 3, [0, 1]),
    (" 1 , 3 ", 3, [0, 2]),
    ("1,1,1", 3, [0]),                # 去重
])
def test_parse_page_range(text, total, expected):
    assert ps.parse_page_range(text, total) == expected


# ---------------------------------------------------------------------------
# 打印参数校验（monkeypatch 打印机列表，不接触真实打印机）
# ---------------------------------------------------------------------------
@pytest.fixture()
def fake_printers(monkeypatch):
    monkeypatch.setattr(ps, "enum_printers", lambda: ["MockPrinter A", "MockPrinter B"])
    monkeypatch.setattr(ps, "get_default_printer", lambda: "MockPrinter A")


def test_validate_defaults(fake_printers):
    params = ps._validate_print_request({})
    assert params == {
        "printer": "MockPrinter A",
        "copies": 1,
        "duplex": 1,
        "paper": "A4",
        "orientation": "portrait",
        "quality": "normal",
        "page_range": "all",
        "sheet_name": None,
        "print_area": None,
    }


def test_validate_full_params(fake_printers):
    params = ps._validate_print_request({
        "printer": "MockPrinter B",
        "copies": 3,
        "duplex": 2,
        "paper_size": "a5",
        "orientation": "landscape",
        "quality": "HIGH",
        "page_range": "1-5,7",
        "sheet_name": "Sheet1",
        "print_area": "A1:F20",
    })
    assert params["printer"] == "MockPrinter B"
    assert params["copies"] == 3
    assert params["duplex"] == 2
    assert params["paper"] == "A5"
    assert params["orientation"] == "landscape"
    assert params["quality"] == "high"
    assert params["page_range"] == "1-5,7"
    assert params["sheet_name"] == "Sheet1"
    assert params["print_area"] == "A1:F20"


@pytest.mark.parametrize("printer", ["不存在的打印机 XYZ", "mockprinter a"])
def test_validate_rejects_unknown_printer(fake_printers, printer):
    with pytest.raises(ValueError) as exc:
        ps._validate_print_request({"printer": printer, "copies": 1})
    assert "打印机" in str(exc.value)


def test_validate_no_printer_available(monkeypatch):
    monkeypatch.setattr(ps, "enum_printers", lambda: [])
    monkeypatch.setattr(ps, "get_default_printer", lambda: "")
    with pytest.raises(ValueError) as exc:
        ps._validate_print_request({})
    assert "没有可用的打印机" in str(exc.value)


@pytest.mark.parametrize("raw,expected", [
    (0, 1), (-3, 1), (99, 10), (10, 10), ("4", 4), ("abc", 1), (None, 1),
])
def test_validate_copies_clamped(fake_printers, raw, expected):
    assert ps._validate_print_request({"copies": raw})["copies"] == expected


@pytest.mark.parametrize("raw,expected", [
    (1, 1), (2, 2), (3, 3), (0, 1), (4, 1), ("2", 2), ("x", 1), (None, 1),
])
def test_validate_duplex_normalized(fake_printers, raw, expected):
    assert ps._validate_print_request({"duplex": raw})["duplex"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("A4", "A4"), ("a4", "A4"), ("A5", "A5"), ("letter", "Letter"),
    ("4R", "4R"), ("5R", "5R"), ("B5", "A4"), (None, "A4"),
])
def test_validate_paper_normalized(fake_printers, raw, expected):
    assert ps._validate_print_request({"paper_size": raw})["paper"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("portrait", "portrait"),
    ("landscape", "landscape"),
    ("reverse_landscape", "reverse_landscape"),
    ("reverse_portrait", "reverse_portrait"),
    ("sideways", "portrait"),
    (None, "portrait"),
])
def test_validate_orientation_normalized(fake_printers, raw, expected):
    assert ps._validate_print_request({"orientation": raw})["orientation"] == expected


@pytest.mark.parametrize("area", ["A1", "A1:F20", "A1:B2,C3:D4", "a1:f20", "AA100:AZ200"])
def test_validate_print_area_valid(fake_printers, area):
    assert ps._validate_print_request({"print_area": area})["print_area"] == area


@pytest.mark.parametrize("area", [
    "Sheet1!A1", "A1:", ":A1", "1:2", "A1-F20", "A1:F20;", "A1:F20 ", "A1:F20,B", "区域",
])
def test_validate_print_area_invalid(fake_printers, area):
    if area == "A1:F20 ":
        # 允许首尾空格，会被 strip 后再校验
        assert ps._validate_print_request({"print_area": area})["print_area"] == "A1:F20"
        return
    with pytest.raises(ValueError) as exc:
        ps._validate_print_request({"print_area": area})
    assert "打印区域" in str(exc.value)


def test_validate_empty_optional_fields(fake_printers):
    params = ps._validate_print_request({"sheet_name": "", "print_area": "   "})
    assert params["sheet_name"] is None
    assert params["print_area"] is None


def test_validate_page_range_truncated(fake_printers):
    params = ps._validate_print_request({"page_range": "1-" + "9" * 200})
    assert len(params["page_range"]) == 100


# ---------------------------------------------------------------------------
# 纸张 / 版式规范化
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("A4", "A4"), ("a5", "A5"), ("LETTER", "Letter"), ("4r", "4R"),
    ("5R", "5R"), ("B5", "A4"), ("", "A4"), (None, "A4"),
])
def test_normalize_paper(raw, expected):
    assert ps._normalize_paper(raw) == expected


def test_normalize_image_layout_defaults():
    assert ps._normalize_image_layout({}) == {
        "fit": "fit", "rotate": 0, "margin_mm": 5.0,
        "paper": "A4", "page_orientation": "portrait",
    }
    assert ps._normalize_image_layout(None)["fit"] == "fit"
    assert ps._normalize_image_layout("not-a-dict")["fit"] == "fit"


@pytest.mark.parametrize("layout,checks", [
    ({"fit": "fill", "rotate": 90, "margin": 0, "paper": "A5", "page_orientation": "landscape"},
     {"fit": "fill", "rotate": 90, "margin_mm": 0.0, "paper": "A5", "page_orientation": "landscape"}),
    ({"fit": "bogus", "rotate": 45, "margin": -5, "paper": "B5", "page_orientation": "x"},
     {"fit": "fit", "rotate": 0, "margin_mm": 0.0, "paper": "A4", "page_orientation": "portrait"}),
    ({"fit": "actual", "rotate": 270, "margin": 20, "paper": "4R", "page_orientation": "portrait"},
     {"fit": "actual", "rotate": 270, "margin_mm": 20.0, "paper": "4R", "page_orientation": "portrait"}),
    ({"fit": "fit", "rotate": 360, "margin": 100, "paper": "5R", "page_orientation": "landscape"},
     {"fit": "fit", "rotate": 0, "margin_mm": 30.0, "paper": "5R", "page_orientation": "landscape"}),
])
def test_normalize_image_layout_values(layout, checks):
    result = ps._normalize_image_layout(layout)
    for key, value in checks.items():
        assert result[key] == value, "%s: %r != %r" % (key, result[key], value)


# ---------------------------------------------------------------------------
# build_image_pdf：页面尺寸 / 绘制区域
# ---------------------------------------------------------------------------
def _make_test_image(tmp_path, size=(2000, 1000), name="pic.jpg"):
    path = os.path.join(str(tmp_path), name)
    ps_lib.make_image(path, size=size)
    return path


def _page_info(pdf_path):
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        images = page.get_images(full=True)
        bbox = page.get_image_bbox(images[0]) if images else None
        return page.rect, bbox, images
    finally:
        doc.close()


@pytest.mark.parametrize("paper,orientation", [
    ("A4", "portrait"), ("A4", "landscape"),
    ("A5", "portrait"), ("Letter", "portrait"),
    ("4R", "portrait"), ("5R", "portrait"),
])
def test_build_image_pdf_page_sizes(tmp_path, paper, orientation):
    image_path = _make_test_image(tmp_path)
    out = os.path.join(str(tmp_path), "out_%s_%s.pdf" % (paper, orientation))
    assert ps.build_image_pdf(image_path, out, fit="fit", rotate=0, margin_mm=5,
                              paper=paper, page_orientation=orientation)
    rect, bbox, _images = _page_info(out)
    w_mm, h_mm = PAPER_MM[paper]
    if orientation == "landscape":
        w_mm, h_mm = h_mm, w_mm
    assert ps_lib.approx(rect.width, mm2pt(w_mm), 0.6), (paper, orientation, rect)
    assert ps_lib.approx(rect.height, mm2pt(h_mm), 0.6), (paper, orientation, rect)
    assert bbox is not None and bbox.is_empty is False


def test_build_image_pdf_fit_bbox(tmp_path):
    image_path = _make_test_image(tmp_path)  # 2000x1000, 宽高比 2:1
    out = os.path.join(str(tmp_path), "fit.pdf")
    ps.build_image_pdf(image_path, out, fit="fit", rotate=0, margin_mm=5,
                       paper="A4", page_orientation="portrait")
    rect, bbox, _ = _page_info(out)
    page_w, page_h = rect.width, rect.height
    content_w, content_h = page_w - mm2pt(10), page_h - mm2pt(10)
    draw_w = content_w
    draw_h = content_w / 2.0
    assert ps_lib.approx(bbox.width, draw_w, 1.0), (bbox.width, draw_w)
    assert ps_lib.approx(bbox.height, draw_h, 1.0), (bbox.height, draw_h)
    assert ps_lib.approx(bbox.x0, mm2pt(5), 1.0)
    assert ps_lib.approx(bbox.y0, (page_h - draw_h) / 2.0, 1.5)
    # fit 不应铺满页面
    assert bbox.height < page_h * 0.6


def test_build_image_pdf_fill_covers_page_and_crops(tmp_path):
    image_path = _make_test_image(tmp_path)
    out = os.path.join(str(tmp_path), "fill.pdf")
    ps.build_image_pdf(image_path, out, fit="fill", rotate=0, margin_mm=0,
                       paper="A4", page_orientation="portrait")
    rect, bbox, images = _page_info(out)
    assert ps_lib.approx(bbox.x0, 0, 1.0)
    assert ps_lib.approx(bbox.y0, 0, 1.0)
    assert ps_lib.approx(bbox.x1, rect.width, 1.0)
    assert ps_lib.approx(bbox.y1, rect.height, 1.0)
    # fill 通过居中裁切实现，嵌入图片宽高比应 ≈ 页面宽高比（不能拉伸变形）
    img_w, img_h = images[0][2], images[0][3]
    assert ps_lib.approx(img_w / float(img_h), rect.width / rect.height, 0.03), (img_w, img_h)


def test_build_image_pdf_fill_keeps_margin_when_given(tmp_path):
    image_path = _make_test_image(tmp_path)
    out = os.path.join(str(tmp_path), "fill5.pdf")
    ps.build_image_pdf(image_path, out, fit="fill", rotate=0, margin_mm=5,
                       paper="A4", page_orientation="portrait")
    rect, bbox, _ = _page_info(out)
    m = mm2pt(5)
    assert ps_lib.approx(bbox.x0, m, 1.0)
    assert ps_lib.approx(bbox.y0, m, 1.0)
    assert ps_lib.approx(bbox.x1, rect.width - m, 1.0)
    assert ps_lib.approx(bbox.y1, rect.height - m, 1.0)


@pytest.mark.parametrize("rotate,expected_aspect", [
    (0, 2.0),      # 2000x1000
    (90, 0.5),     # 旋转后 1000x2000
    (180, 2.0),
    (270, 0.5),
])
def test_build_image_pdf_rotate(tmp_path, rotate, expected_aspect):
    image_path = _make_test_image(tmp_path)
    out = os.path.join(str(tmp_path), "rotate_%s.pdf" % rotate)
    ps.build_image_pdf(image_path, out, fit="fit", rotate=rotate, margin_mm=5,
                       paper="A4", page_orientation="portrait")
    _rect, bbox, _ = _page_info(out)
    assert ps_lib.approx(bbox.width / bbox.height, expected_aspect, 0.03), \
        (rotate, bbox.width, bbox.height)


def test_build_image_pdf_actual_small_image(tmp_path):
    image_path = _make_test_image(tmp_path, size=(200, 100), name="small.jpg")
    out = os.path.join(str(tmp_path), "actual.pdf")
    ps.build_image_pdf(image_path, out, fit="actual", rotate=0, margin_mm=5,
                       paper="A4", page_orientation="portrait")
    rect, bbox, _ = _page_info(out)
    # 无 DPI 信息时按 96 DPI：200x100 px -> 150x75 pt
    assert ps_lib.approx(bbox.width, 150.0, 1.0), bbox
    assert ps_lib.approx(bbox.height, 75.0, 1.0), bbox
    center = ps_lib.bbox_center((bbox.x0, bbox.y0, bbox.x1, bbox.y1))
    assert ps_lib.approx(center[0], rect.width / 2.0, 1.5)
    assert ps_lib.approx(center[1], rect.height / 2.0, 1.5)


def test_build_image_pdf_actual_large_image_scaled(tmp_path):
    image_path = _make_test_image(tmp_path)  # 2000x1000 -> 1500x750 pt 超出内容区
    out = os.path.join(str(tmp_path), "actual_big.pdf")
    ps.build_image_pdf(image_path, out, fit="actual", rotate=0, margin_mm=5,
                       paper="A4", page_orientation="portrait")
    rect, bbox, _ = _page_info(out)
    assert bbox.width <= rect.width - mm2pt(10) + 1.0
    assert bbox.height <= rect.height - mm2pt(10) + 1.0


def test_build_image_pdf_landscape_page(tmp_path):
    image_path = _make_test_image(tmp_path)
    out = os.path.join(str(tmp_path), "landscape.pdf")
    ps.build_image_pdf(image_path, out, fit="fit", rotate=0, margin_mm=5,
                       paper="A4", page_orientation="landscape")
    rect, bbox, _ = _page_info(out)
    assert rect.width > rect.height
    assert bbox.width > bbox.height  # 图片仍为横向


def test_build_image_pdf_invalid_params_fallback(tmp_path):
    image_path = _make_test_image(tmp_path)
    out = os.path.join(str(tmp_path), "invalid.pdf")
    ps.build_image_pdf(image_path, out, fit="不存在的模式", rotate=45, margin_mm=None,
                       paper="B5", page_orientation="侧向")
    rect, bbox, _ = _page_info(out)
    # 非法纸张回退 A4 纵向；非法边距回退默认 5mm
    assert ps_lib.approx(rect.width, mm2pt(210), 0.6)
    assert ps_lib.approx(rect.height, mm2pt(297), 0.6)
    assert ps_lib.approx(bbox.x0, mm2pt(5), 1.0)


def test_build_image_pdf_negative_margin_clamped_to_zero(tmp_path):
    image_path = _make_test_image(tmp_path)
    out = os.path.join(str(tmp_path), "neg_margin.pdf")
    ps.build_image_pdf(image_path, out, fit="fit", rotate=0, margin_mm=-10,
                       paper="A4", page_orientation="portrait")
    rect, bbox, _ = _page_info(out)
    assert ps_lib.approx(bbox.x0, 0.0, 0.6)
    assert ps_lib.approx(rect.width, mm2pt(210), 0.6)


def test_build_image_pdf_bytes_and_render(tmp_path):
    image_path = _make_test_image(tmp_path)
    pdf_bytes = ps.build_image_pdf_bytes(image_path, fit="fit", rotate=0, margin_mm=5,
                                         paper="A4", page_orientation="portrait")
    assert pdf_bytes[:5] == b"%PDF-"
    png = ps.render_image_layout_png(image_path, dpi=100, fit="fit", rotate=0,
                                     margin_mm=5, paper="A4", page_orientation="portrait")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    size, bbox = ps_lib.content_bbox(png)
    assert ps_lib.approx(size[0], 210 / 25.4 * 100, 3), size
    assert ps_lib.approx(size[1], 297 / 25.4 * 100, 3), size
    assert bbox is not None


# ---------------------------------------------------------------------------
# silent_print_pdf：mock 打印机，验证参数与分页，不产生真实打印
# ---------------------------------------------------------------------------
class _FakeDC(object):
    def __init__(self, caps):
        self._caps = caps
        self.calls = []

    def GetDeviceCaps(self, cap):
        return self._caps.get(cap, 0)

    def GetHandleOutput(self):
        return object()

    def StartDoc(self, name):
        self.calls.append("StartDoc")

    def EndDoc(self):
        self.calls.append("EndDoc")

    def StartPage(self):
        self.calls.append("StartPage")

    def EndPage(self):
        self.calls.append("EndPage")

    def DeleteDC(self):
        self.calls.append("DeleteDC")


class _FakeDib(object):
    instances = []

    def __init__(self, img):
        self.size = img.size
        self.draws = []
        _FakeDib.instances.append(self)

    def draw(self, handle, box):
        self.draws.append(box)


def _patch_printer(monkeypatch, dcs, created):
    """用假 DC 替换打印机调用；dcs 为按调用顺序返回的 (dc, used_devmode) 列表。"""
    queue = list(dcs)

    def fake_create(printer_name, paper_size="A4", duplex=1, force_default=False):
        created["calls"].append({
            "printer": printer_name, "paper": paper_size,
            "duplex": duplex, "force_default": force_default,
        })
        dc, used_devmode = queue.pop(0)
        return dc, None, [], used_devmode

    monkeypatch.setattr(ps, "enum_printers", lambda: ["MockPrinter A"])
    monkeypatch.setattr(ps, "_create_printer_dc", fake_create)
    monkeypatch.setattr(ps, "_close_printer_dc", lambda dc, raw: None)
    monkeypatch.setattr(ps, "ImageWin", types.SimpleNamespace(Dib=_FakeDib))
    _FakeDib.instances = []
    return created


def _working_dc():
    return _FakeDC({
        ps.win32con.HORZRES: 2400,
        ps.win32con.VERTRES: 3300,
        ps.win32con.PHYSICALWIDTH: 2400,
        ps.win32con.PHYSICALHEIGHT: 3300,
        ps.win32con.PHYSICALOFFSETX: 10,
        ps.win32con.PHYSICALOFFSETY: 20,
    })


def test_silent_print_params_and_pagination(monkeypatch, tmp_path):
    pdf = ps_lib.make_pdf(os.path.join(str(tmp_path), "doc.pdf"), pages=3)
    dc = _working_dc()
    created = {"calls": []}
    _patch_printer(monkeypatch, [(dc, True)], created)

    result = ps.silent_print_pdf(pdf, "MockPrinter A", copies=2, duplex=2,
                                 orientation="portrait", page_range="1,3",
                                 paper_size="A5", quality="high")
    assert len(created["calls"]) == 1
    call = created["calls"][0]
    assert call["printer"] == "MockPrinter A"
    assert call["paper"] == "A5"
    assert call["duplex"] == 2
    assert result["pages"] == 2
    assert dc.calls.count("StartDoc") == 2
    assert dc.calls.count("EndDoc") == 2
    assert dc.calls.count("StartPage") == 4
    assert dc.calls.count("EndPage") == 4
    # 每页都实际绘制，且绘制区域在可打印区域内
    assert len(_FakeDib.instances) == 4
    for dib in _FakeDib.instances:
        assert len(dib.draws) == 1
        x0, y0, x1, y1 = dib.draws[0]
        assert 0 <= x0 < x1 <= 2400 + 10
        assert 0 <= y0 < y1 <= 3300 + 20


def test_silent_print_clamps_copies(monkeypatch, tmp_path):
    pdf = ps_lib.make_pdf(os.path.join(str(tmp_path), "doc2.pdf"), pages=1)
    dc = _working_dc()
    created = {"calls": []}
    _patch_printer(monkeypatch, [(dc, False)], created)
    ps.silent_print_pdf(pdf, "MockPrinter A", copies=99, page_range="all")
    assert dc.calls.count("StartDoc") == 10
    # A4 + 单面走默认 DC 路径（force_default=True）
    assert created["calls"][0]["force_default"] is True


def test_silent_print_falls_back_to_default_dc(monkeypatch, tmp_path):
    """自定义纸张 DC 创建成功但可打印区域无效时，回退默认 DC 重试一次。"""
    pdf = ps_lib.make_pdf(os.path.join(str(tmp_path), "doc5.pdf"), pages=1)
    broken_dc = _FakeDC({})  # 所有 GetDeviceCaps 返回 0
    good_dc = _working_dc()
    created = {"calls": []}
    _patch_printer(monkeypatch, [(broken_dc, True), (good_dc, False)], created)

    result = ps.silent_print_pdf(pdf, "MockPrinter A", paper_size="A5", duplex=2)
    assert len(created["calls"]) == 2
    assert created["calls"][0]["force_default"] is False
    assert created["calls"][1]["force_default"] is True
    assert result["pages"] == 1
    assert any("回退" in w for w in result["warnings"])


def test_silent_print_rejects_unknown_printer(monkeypatch, tmp_path):
    pdf = ps_lib.make_pdf(os.path.join(str(tmp_path), "doc3.pdf"), pages=1)
    called = {"create": False}

    def fake_create(*args, **kwargs):
        called["create"] = True
        raise AssertionError("不应创建打印机 DC")

    monkeypatch.setattr(ps, "enum_printers", lambda: ["MockPrinter A"])
    monkeypatch.setattr(ps, "_create_printer_dc", fake_create)
    with pytest.raises(RuntimeError) as exc:
        ps.silent_print_pdf(pdf, "不存在的打印机 XYZ")
    assert "打印机" in str(exc.value)
    assert called["create"] is False


def test_silent_print_rejects_missing_file():
    with pytest.raises(RuntimeError) as exc:
        ps.silent_print_pdf(os.path.join(str(ps_lib.RUNTIME_DIR), "no-such.pdf"),
                            "MockPrinter A")
    assert "PDF文件不存在" in str(exc.value)


def test_silent_print_rejects_empty_printer(monkeypatch, tmp_path):
    pdf = ps_lib.make_pdf(os.path.join(str(tmp_path), "doc4.pdf"), pages=1)
    monkeypatch.setattr(ps, "enum_printers", lambda: [])
    with pytest.raises(RuntimeError) as exc:
        ps.silent_print_pdf(pdf, "")
    assert "未选择打印机" in str(exc.value)


# ---------------------------------------------------------------------------
# 打印方向旋转（纯内存）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("orientation,expected_size", [
    ("portrait", (100, 50)),
    ("landscape", (50, 100)),
    ("reverse_landscape", (50, 100)),
    ("reverse_portrait", (100, 50)),
    ("unknown", (100, 50)),
])
def test_apply_print_orientation(orientation, expected_size):
    img = PILImage.new("RGB", (100, 50), (255, 255, 255))
    out = ps._apply_print_orientation(img, orientation)
    assert out.size == expected_size


def test_image_target_dpi_minimum():
    assert ps._image_target_dpi_for_layout(mm2pt(210), mm2pt(297), mm2pt(5)) >= 600
