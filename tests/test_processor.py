
import pytest
from omfitt import BaseFixture, FixtureService, BaseProcessor, FixtureShop, ProcessPhase
from unittest.mock import MagicMock


class Proc(BaseProcessor):
    __slots__ = BaseProcessor.__slots__

    pass


class Fixture(BaseFixture):
    def __init__(self, name, raise_=None):
        self.name = name
        if not raise_:
            raise_ = ['', None, None]
        self.raise_ = raise_

    def take_on(self, app_ctx, ctx):
        cb_name, err_cls, stop_final = self.raise_
        self._safe_local = {}
        ctx.shared_data[self.name] = ['touch']
        ctx.shared_data[self.name + '_ph'] = [ctx.phase]
        if cb_name == 'take_on':
            raise err_cls(self.name)

    def on_output(self, app_ctx, ctx):
        cb_name, err_cls, stop_final = self.raise_
        ctx.shared_data[self.name].append('out')
        ctx.shared_data[self.name + '_ph'].append(ctx.phase)
        ctx.output.append(self.name)
        if cb_name == 'on_output':
            raise err_cls(self.name)

    def on_finalize(self, app_ctx, ctx):
        cb_name, err_cls, stop_final = self.raise_
        ctx.shared_data[self.name].append('final')
        ctx.shared_data[self.name + '_ph'].append(ctx.phase)
        if cb_name == 'on_finalize':
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
def shop(foo_bar_baz):
    foo_, bar_, baz_ = foo_bar_baz

    @FixtureShop.make_from
    class Shop:
        foo = foo_
        bar = bar_
        baz = baz_

    return Shop


@pytest.fixture
def fx_service(shop):
    fs = FixtureService(reverse_postproc=False)
    fs.serve(shop)
    return fs


@pytest.fixture
def fx_proc():
    return Proc()


@pytest.fixture
def except_handlers():
    def eh(app_ctx, route_ctx, ex):
        raise ex
    return {
        '*': eh
    }


@pytest.fixture
def handler(fx_proc: Proc, foo_bar_baz, shop, fx_service, request):
    except_handlers = getattr(request, 'param', None)
    foo_, bar_ = foo_bar_baz[:2]

    def core(arg=None):
        if arg:
            arg('core')
            shop.baz
        return ['a']
    return fx_proc.make_core_handler(
        core,
        None,
        fx_service,
        [foo_, bar_],
        {shop: shop.fixtures},
        {
            'app_ctx': {},
            'staff_ctx': {}
        },
        except_handlers
    )


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
    this = fx_proc._local.this
    ctx = this.ctx
    if arg:
        assert res == ['a', 'foo', 'bar', 'baz']
        arg.assert_called_with('core')
        assert ctx.shared_data['baz'] == ['touch', 'out', 'final']
        assert ctx.shared_data['baz_ph'] == [ProcessPhase.RUN, ProcessPhase.OUTPUT, ProcessPhase.FINALIZE]
    else:
        assert res == ['a', 'foo', 'bar']

    assert ctx.successful
    assert ctx.shared_data['foo'] == ['touch', 'out', 'final']
    assert ctx.shared_data['bar'] == ['touch', 'out', 'final']

    assert ctx.shared_data['foo_ph'] == [ProcessPhase.SETUP, ProcessPhase.OUTPUT, ProcessPhase.FINALIZE]
    assert ctx.shared_data['bar_ph'] == [ProcessPhase.SETUP, ProcessPhase.OUTPUT, ProcessPhase.FINALIZE]

    assert this.fixtures == foo_bar_baz[:-1]
    assert not fx_service._safe_local.involved


def rt(app_ctx, ctx, ex):
    ctx.shared_data['rt-handler'] = True
    raise KeyError()


@pytest.mark.parametrize(
    'case, arg, foo_bar_baz, handler',
    [
        [
            0, MagicMock(),
            [('foo', False), ('bar', ['on_output', RuntimeError, None]), ('baz', False)],
            {
                RuntimeError: rt,
                KeyError: lambda app_ctx, ctx, ex: 'kerr'
            }

        ],
        [
            1, MagicMock(),
            [('foo', False), ('bar', ['on_finalize', RuntimeError, True]), ('baz', False)],
            {
                RuntimeError: rt,
                KeyError: lambda app_ctx, ctx, ex: 'kerr'
            }
        ],
        [
            2, MagicMock(),
            [('foo', False), ('bar', ['take_on', RuntimeError, None]), ('baz', False)],
            {
                RuntimeError: rt,
                KeyError: lambda app_ctx, ctx, ex: 'kerr'
            }
        ],
    ],
    indirect=['foo_bar_baz', 'handler']
)
def test_process_err(fx_proc: Proc, handler, foo_bar_baz, fx_service: FixtureService, arg, case):

    res = handler(arg)
    assert res == 'kerr'
    ctx = fx_proc.ctx
    assert ctx.shared_data['rt-handler']

    S, R, O, F = [ProcessPhase.SETUP, ProcessPhase.RUN, ProcessPhase.OUTPUT, ProcessPhase.FINALIZE]

    if case == 0:
        assert not ctx.successful
        assert not fx_service._safe_local.involved
        assert ctx.shared_data['foo_ph'] == [S, O, O]
        assert ctx.shared_data['bar_ph'] == [S, O, O]
        assert ctx.shared_data['baz_ph'] == [R, O]
        assert ctx.shared_data['foo'] == ['touch', 'out', 'final']
        assert ctx.shared_data['bar'] == ['touch', 'out', 'final']
        assert ctx.shared_data['baz'] == ['touch', 'final']  # break output flow on bar
    if case == 1:
        assert ctx.successful
        assert [*fx_service._safe_local.involved] == [foo_bar_baz[-1]]
        assert ctx.shared_data['foo_ph'] == [S, O, F]
        assert ctx.shared_data['bar_ph'] == [S, O, F]
        assert ctx.shared_data['baz_ph'] == [R, O]
        assert ctx.shared_data['foo'] == ['touch', 'out', 'final']
        assert ctx.shared_data['bar'] == ['touch', 'out', 'final']
        assert ctx.shared_data['baz'] == ['touch', 'out']  # break finalize flow on bar
    if case == 2:
        assert not ctx.successful
        assert not fx_service._safe_local.involved  # finalize run anyway
        assert ctx.shared_data['foo_ph'] == [S, S]
        assert ctx.shared_data['bar_ph'] == [S, S]
        assert ctx.shared_data['foo'] == ['touch', 'final']
        assert ctx.shared_data['bar'] == ['touch', 'final']
        # break at init-flow, so no run at all,
        # but baz touched while running core-handler
        assert 'baz' not in ctx.shared_data
