# -*- coding: utf-8 -*-
"""pytest 公共 fixture：启动隔离的 PrintServer 实例（管理员 / 非管理员 / 小上限）。

测试约定：
- 所有测试数据放在 tests/_runtime/ 下，每次运行前清空，测试可独立重复。
- 绝不向真实打印机提交任务：打印相关只测参数校验失败路径或单元 mock。
"""
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
for _p in (PROJECT_DIR, TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 必须在 import print_server（单元测试）之前设置，避免应用把数据写进项目目录
os.environ.setdefault("PRINTSERVER_DATA_DIR",
                      os.path.join(TESTS_DIR, "_runtime", "unit_data"))
os.environ.setdefault("PYTHONUTF8", "1")

import ps_lib  # noqa: E402
import pytest  # noqa: E402
import requests  # noqa: E402

ps_lib.ensure_dir(os.path.join(ps_lib.RUNTIME_DIR, "unit_data"))


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: 依赖 Office COM 转换引擎的耗时用例")
    config.addinivalue_line("markers", "e2e: 端到端 UI 用例")


@pytest.fixture(scope="session")
def server_stack():
    """启动 3 个隔离实例：管理员(5011) / 非管理员(5012) / 1MB 上限(5013)。"""
    stack = {}
    try:
        stack["admin"] = ps_lib.start_server(
            ps_lib.pick_port(ps_lib.ADMIN_PORT),
            os.path.join(ps_lib.RUNTIME_DIR, "admin_data"),
        )
        stack["nonadmin"] = ps_lib.start_server(
            ps_lib.pick_port(ps_lib.NONADMIN_PORT),
            os.path.join(ps_lib.RUNTIME_DIR, "nonadmin_data"),
            {"PRINTSERVER_ADMIN_DISABLED": "1"},
        )
        stack["small"] = ps_lib.start_server(
            ps_lib.pick_port(ps_lib.SMALL_PORT),
            os.path.join(ps_lib.RUNTIME_DIR, "small_data"),
            {"PRINTSERVER_MAX_UPLOAD_MB": "1"},
        )
        yield stack
    finally:
        for server in stack.values():
            server.stop()


@pytest.fixture(scope="session")
def admin_server(server_stack):
    return server_stack["admin"]


@pytest.fixture(scope="session")
def nonadmin_server(server_stack):
    return server_stack["nonadmin"]


@pytest.fixture(scope="session")
def small_server(server_stack):
    return server_stack["small"]


@pytest.fixture(scope="session")
def admin_base(admin_server):
    return admin_server.base


@pytest.fixture(scope="session")
def nonadmin_base(nonadmin_server):
    return nonadmin_server.base


@pytest.fixture(scope="session")
def small_base(small_server):
    return small_server.base


@pytest.fixture(scope="session")
def admin_env(admin_base):
    """管理员实例 /api/session 内容（含 env 转换引擎能力）。"""
    return requests.get(admin_base + "/api/session", timeout=20).json()


@pytest.fixture(scope="session")
def office_available(admin_env):
    env = (admin_env or {}).get("env") or {}
    return bool(env.get("office") or env.get("wps") or env.get("libreoffice"))


@pytest.fixture()
def admin(admin_base):
    """每个测试一个全新会话（独立 sid，文件列表互相隔离）。"""
    sess = requests.Session()
    sess.headers["Accept"] = "application/json"
    yield sess
    sess.close()


@pytest.fixture()
def nonadmin(nonadmin_base):
    """非管理员实例上的全新普通用户会话。"""
    sess = requests.Session()
    sess.headers["Accept"] = "application/json"
    yield sess
    sess.close()
