
from _pytest.python_api import raises
import pytest
import types
from nanomounter import BaseFixture, FixtureService, BaseProcessor, FixtureShop
from unittest.mock import MagicMock, call, patch



class Proc(BaseProcessor):
    def init_context(self):
        pass


class Fixture(BaseFixture):
    def __init__(self, name, raise_=False):
        self.name = name
        self.raise_ = raise_

    def on_touch(self, ctx):
        self._safe_local = {}
        ctx.shared_data[self.name] = ['touch']
        ctx.shared_data[self.name + '_ph'] = [ctx.phase]

    def on_output(self, ctx):
        ctx.shared_data[self.name].append('out')
        ctx.shared_data[self.name + '_ph'].append(ctx.phase)
        ctx.output.append(self.name)
        if self.raise_:
            raise RuntimeError(self.name)

    def finalize(self, ctx):
        ctx.shared_data[self.name].append('final')
        ctx.shared_data[self.name + '_ph'].append(ctx.phase)


@pytest.fixture
def foo_bar_baz(request):
    ret = [Fixture(f, r) for f, r in request.param]
    BaseFixture.__init_request_ctx__()
    return ret

@pytest.fixture
def ferr():
    ret = Fixture('ferr', True)
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

@pytest.fixture
def shop(foo_bar_baz, fx_service):
    foo_, bar_, baz_ = foo_bar_baz
    @FixtureShop.make_from
    class Shop:
        foo = foo_
        bar = bar_
        baz = baz_

    Shop.on_checkout(fx_service.use)
    return Shop

@pytest.fixture
def fx_proc(fx_service):
    #patch('nanomounter.RouteContext', autospec=True).start()
    return Proc(fx_service, {})

@pytest.fixture
def handler(fx_proc: Proc, foo_bar_baz, shop):
    foo_, bar_ = foo_bar_baz[:2]
    def core(arg=None):
        if arg:
            arg('core')
            shop.baz
        return ['a']
    return fx_proc.make_core_handler(core, [foo_, bar_])


@pytest.mark.parametrize(
    'arg,foo_bar_baz',
    [
        [
            MagicMock(),
            [('foo', False), ('bar', False), ('baz', False)]
        ],
        [
            None,
            [('foo', False), ('bar', False), ('baz', False)]
        ],
    ],
    indirect=['foo_bar_baz']
)
def test_process(fx_proc: Proc, handler, foo_bar_baz, fx_service: FixtureService, arg):
    res = handler(arg)
    ctx = fx_proc._local.ctx
    if arg:
        assert res == ['a', 'foo', 'bar', 'baz']
        arg.assert_called_with('core')
        assert ctx.shared_data['baz'] == ['touch', 'out', 'final']
        assert ctx.shared_data['baz_ph'] == ['run', 'output', 'finalize']
    else:
        assert res == ['a', 'foo', 'bar']

    assert ctx.successful
    assert ctx.shared_data['foo'] == ['touch', 'out', 'final']
    assert ctx.shared_data['bar'] == ['touch', 'out', 'final']

    assert ctx.shared_data['foo_ph'] == ['request', 'output', 'finalize']
    assert ctx.shared_data['bar_ph'] == ['request', 'output', 'finalize']

    assert fx_proc._local.fixtures == foo_bar_baz[:-1]
    assert not fx_service._safe_local.involved



@pytest.mark.parametrize(
    'arg,foo_bar_baz',
    [
        [
            MagicMock(),
            [('foo', False), ('bar', True), ('baz', False)]
        ]
    ],
    indirect=['foo_bar_baz']
)
def test_process_err(fx_proc: Proc, handler, foo_bar_baz, fx_service: FixtureService, arg):
    fx_proc.exception_handlers = {
        RuntimeError: lambda ctx: 'runerr'
    }
    arg = MagicMock()
    res = handler(arg)
    assert res == 'runerr'
    ctx = fx_proc._local.ctx
    assert not ctx.successful
    assert not fx_service._safe_local.involved
    assert ctx.shared_data['foo_ph'] == ['request', 'output', 'output']
    assert ctx.shared_data['bar_ph'] == ['request', 'output', 'output']
    assert ctx.shared_data['foo'] == ['touch', 'out', 'final']
    assert ctx.shared_data['bar'] == ['touch', 'out', 'final']
    assert ctx.shared_data['baz'] == ['touch', 'final']  # break output flow on bar




