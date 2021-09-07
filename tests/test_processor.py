
import pytest
from omfitt import BaseFixture, FixtureService, BaseProcessor, FixtureShop
from unittest.mock import MagicMock


class Proc(BaseProcessor):
    def init_context(self):
        pass


class Fixture(BaseFixture):
    def __init__(self, name, raise_=None):
        self.name = name
        if not raise_:
            raise_ = ['', None, None]
        self.raise_ = raise_

    def on_touch(self, ctx):
        cb_name, err_cls, stop_final = self.raise_
        self._safe_local = {}
        ctx.shared_data[self.name] = ['touch']
        ctx.shared_data[self.name + '_ph'] = [ctx.phase]
        if cb_name == 'on_touch':
            raise err_cls(self.name)

    def on_output(self, ctx):
        cb_name, err_cls, stop_final = self.raise_
        ctx.shared_data[self.name].append('out')
        ctx.shared_data[self.name + '_ph'].append(ctx.phase)
        ctx.output.append(self.name)
        if cb_name == 'on_output':
            raise err_cls(self.name)

    def finalize(self, ctx):
        cb_name, err_cls, stop_final = self.raise_
        ctx.shared_data[self.name].append('final')
        ctx.shared_data[self.name + '_ph'].append(ctx.phase)
        if cb_name == 'finalize':
            if stop_final:
                ctx.stop_finalize = True
            raise err_cls(self.name)


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
    'case, arg, foo_bar_baz',
    [
        [
            0, MagicMock(),
            [('foo', False), ('bar', ['on_output', RuntimeError, None]), ('baz', False)]
        ],
        [
            1, MagicMock(),
            [('foo', False), ('bar', ['finalize', RuntimeError, True]), ('baz', False)]
        ],
        [
            2, MagicMock(),
            [('foo', False), ('bar', ['on_touch', RuntimeError, None]), ('baz', False)]
        ],
    ],
    indirect=['foo_bar_baz']
)
def test_process_err(fx_proc: Proc, handler, foo_bar_baz, fx_service: FixtureService, arg, case):

    def rt(ctx, ex):
        ctx.shared_data['rt-handler'] = True
        raise KeyError()

    fx_proc.exception_handlers = {
        RuntimeError: rt,
        KeyError: lambda ctx, ex: 'kerr'
    }
    res = handler(arg)
    assert res == 'kerr'
    ctx = fx_proc.ctx
    assert ctx.shared_data['rt-handler']

    if case == 0:
        assert not ctx.successful
        assert not fx_service._safe_local.involved
        assert ctx.shared_data['foo_ph'] == ['request', 'output', 'output']
        assert ctx.shared_data['bar_ph'] == ['request', 'output', 'output']
        assert ctx.shared_data['baz_ph'] == ['run', 'output']
        assert ctx.shared_data['foo'] == ['touch', 'out', 'final']
        assert ctx.shared_data['bar'] == ['touch', 'out', 'final']
        assert ctx.shared_data['baz'] == ['touch', 'final']  # break output flow on bar
    if case == 1:
        assert ctx.successful
        assert [*fx_service._safe_local.involved] == [foo_bar_baz[-1]]
        assert ctx.shared_data['foo_ph'] == ['request', 'output', 'finalize']
        assert ctx.shared_data['bar_ph'] == ['request', 'output', 'finalize']
        assert ctx.shared_data['baz_ph'] == ['run', 'output']
        assert ctx.shared_data['foo'] == ['touch', 'out', 'final']
        assert ctx.shared_data['bar'] == ['touch', 'out', 'final']
        assert ctx.shared_data['baz'] == ['touch', 'out']  # break finalize flow on bar
    if case == 2:
        assert not ctx.successful
        assert not fx_service._safe_local.involved  # finalize run anyway
        assert ctx.shared_data['foo_ph'] == ['request', 'request']
        assert ctx.shared_data['bar_ph'] == ['request', 'request']
        assert ctx.shared_data['foo'] == ['touch', 'final']
        assert ctx.shared_data['bar'] == ['touch', 'final']
        # break at init-flow, so no run at all,
        # but baz touched while running core-handler
        assert 'baz' not in ctx.shared_data


