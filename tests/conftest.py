"""Offline by default. --integration makes unfinished team modules fail loudly."""
import ast
import inspect
import os
import textwrap

import pytest

os.environ.setdefault("ALLOW_NO_LLM", "1")


def pytest_addoption(parser):
    parser.addoption("--integration", action="store_true", help="require all team modules; never skip their stubs")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--integration"):
        marker = pytest.mark.skip(reason="release risk check; run --integration before submission")
        for item in items:
            if "release" in item.keywords:
                item.add_marker(marker)


@pytest.fixture
def require_implemented(request):
    def check(*functions):
        for function in functions:
            node = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
            body = [s for s in node.body if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)]
            stub = len(body) == 1 and isinstance(body[0], ast.Raise) and "NotImplementedError" in ast.unparse(body[0])
            if stub:
                message = f"Pending owner implementation: {function.__module__}.{function.__name__}"
                if request.config.getoption("--integration"):
                    pytest.fail(message, pytrace=False)
                pytest.skip(message + "; use --integration to enforce release gate")
    return check
