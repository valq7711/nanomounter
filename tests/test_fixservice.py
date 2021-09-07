
import pytest
from omfitt import BaseFixture, FixtureService
from unittest.mock import MagicMock, call


class Fixture(BaseFixture):
    def __init__(self, name):
        self.name = name

    def take_on(self, ctx):
        self._safe_local = {}
        ctx(self.name)

    def on_output(self, ctx):
        ctx(self.name)

    def on_finalize(self, ctx):
        ctx(self.name)


@pytest.fixture
def foo_bar_baz():
    ret = Fixture('foo'), Fixture('bar'), Fixture('baz')
    BaseFixture.__init_request_ctx__()
    return ret


@pytest.fixture
def foo_bar_baz_deps(foo_bar_baz):
    foo_, bar_, baz_ = foo_bar_baz
    bar_.use_fixtures(foo_)
    baz_.use_fixtures(bar_)
    return foo_bar_baz


@pytest.fixture
def fx_service():
    return FixtureService()


def test_expand_deps_nodeps(fx_service: FixtureService, foo_bar_baz):
    foo, bar, baz = foo_bar_baz
    assert fx_service.expand_deps(*reversed(foo_bar_baz)) == [*reversed([foo, bar, baz])]


def test_expand_deps(fx_service: FixtureService, foo_bar_baz_deps):
    foo, bar, baz = foo_bar_baz_deps
    assert fx_service.expand_deps(*reversed(foo_bar_baz_deps)) == [foo, bar, baz]


def test_process_flow(fx_service: FixtureService, foo_bar_baz):
    foo, bar, baz = foo_bar_baz
    ctx = MagicMock()
    fx_service.init(ctx)
    assert not ctx.called
    fx_service.use(*foo_bar_baz)
    assert ctx.mock_calls == [call(f.name) for f in foo_bar_baz]

    ctx.reset_mock()
    fx_service.on_output()
    assert ctx.mock_calls == [call(f.name) for f in foo_bar_baz]

    ctx.reset_mock()
    fx_service.finalize()
    assert ctx.mock_calls == [call(f.name) for f in foo_bar_baz]


def test_process_flow_deps(fx_service: FixtureService, foo_bar_baz_deps):
    foo, bar, baz = foo_bar_baz_deps
    ctx = MagicMock()
    fx_service.init(ctx)
    assert not ctx.called
    fx_service.use(baz)
    assert ctx.mock_calls == [call(f.name) for f in foo_bar_baz_deps]

    ctx.reset_mock()
    fx_service.on_output()
    assert ctx.mock_calls == [call(f.name) for f in foo_bar_baz_deps]

    ctx.reset_mock()
    fx_service.finalize()
    assert ctx.mock_calls == [call(f.name) for f in foo_bar_baz_deps]
