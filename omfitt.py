import threading
import functools
import types
import enum
from collections import UserDict

__version__ = '0.0.1'
__author__ = "Valery Kucherov <valq7711@gmail.com>"
__copyright__ = "Copyright (C) 2021 Valery Kucherov"
__license__ = "MIT"


class OrderedUniqSet(dict):
    add = lambda s, *items: dict.update(s, {it: True for it in items})

    def __init__(self, items=None):
        super().__init__()
        if items:
            self.add(*items)


class ProcessPhase(str, enum.Enum):
    SETUP = 'setup'
    RUN = 'run'
    OUTPUT = 'output'
    FINALIZE = 'finalize'


class RouteContext:
    __slots__ = (
        'request', 'response', 'output', 'shared_data',
        'exception', 'finalize_exceptions',
        'successful', 'phase', 'stop_finalize'
    )

    def __init__(self):
        self.request = None
        self.response = None
        self.output = None
        self.shared_data = {}
        self.exception = None
        self.finalize_exceptions = []
        self.successful = True
        self.phase: ProcessPhase = None
        self.stop_finalize = False


class LocalStorage:
    __request_master_ctx__ = threading.local()

    @property
    def _safe_local(self):
        try:
            ret = self.__request_master_ctx__.request_ctx[self]
        except KeyError as err:
            msg = (
                'fitter hint: this is an attempt to access an uninitialized '
                'thread-local data of {}'
            ).format(self)
            raise RuntimeError(msg) from err
        return ret

    @_safe_local.setter
    def _safe_local(self, storage):
        return self.__mount_local__(self, storage)

    @classmethod
    def __init_request_ctx__(cls):
        cls.__request_master_ctx__.request_ctx = dict()

    @classmethod
    def __mount_local__(cls, self, storage):
        cls.__request_master_ctx__.request_ctx[self] = storage
        return storage


class BubbleWrap(LocalStorage):
    def setup(self):
        pass

    def cleanup(self, route_ctx):
        pass


class BaseFixture(LocalStorage):
    __track_deps_if_instance__ = []

    def __new__(cls, *args, **kwargs):
        self = super().__new__(cls)
        deps = self.__prerequisites__ = []
        track_classes = cls.__track_deps_if_instance__
        if deps is not None:
            for it in [*args, *kwargs.values()]:
                if (
                    isinstance(it, tuple(track_classes))
                    and it not in deps
                ):
                    deps.append(it)
        return self

    def take_on(self, ctx):
        ''' Called before run or before direct use of the fixture.

            Not called if an exception is raised before,
            i.e. from previous try-to-taken fixture.
        '''
        pass

    def on_output(self, ctx):
        ''' Called after successful core-function run.

            Not called if an exception is raised before.
        '''
        pass

    def on_finalize(self, ctx):
        ''' Called at the end of process.

            Not called if an exception is raised before with ctx.stop_finalize set to True.
        '''
        pass

    def use_fixtures(self, *fixtures):
        '''Use other fixtures with dependency tracking.

        self.some_fixture = self.use_fixtures(SomeFixture(...))
        foo, bar = self.use_fixtures(Foo(...), Bar(...))
        '''
        deps = self.__prerequisites__
        deps.extend([
            f for f in fixtures if f not in deps
        ])
        if len(fixtures) == 1:
            return fixtures[0]
        return fixtures

    @property
    def with_deps(self):
        '''Return a list of all dependencies with the fixture itself.

           [independent ... dependent]
        '''
        ret = OrderedUniqSet()
        for f in self.__prerequisites__:
            if isinstance(f, FixtureHolder):
                f = f.value
            ret.add(*f.with_deps)
        ret.add(self)
        return ret


BaseFixture.__track_deps_if_instance__.append(BaseFixture)


class FixtureStorage(UserDict):
    def __init__(self, fixtures_dict=None):
        super().__init__()
        self.data = fixtures_dict or {}

    def __setitem__(self, k, v):
        f = self.data[k]
        if not isinstance(f, FixtureHolder):
            raise TypeError(f'Fixture `{k}` must be FixtureHolder instance')
        f.set(v)

    def __setattr__(self, k, v):
        if k == 'data':
            return object.__setattr__(self, k, v)
        if k not in self.data:
            raise AttributeError(f'There is no `{k}` fixture')
        return self.__setitem__(k, v)


class FixtureShop:

    @classmethod
    def make_from(cls, src_class):
        ret: 'src_class' = FixtureShop(cls._get_fixtures(src_class))
        return ret

    def __init__(self, fixtures_dict):
        self._local = threading.local()
        self.open(fixtures_dict)
        self._on_checkout = None

    @property
    def fixtures(self):
        return self._local.fixtures

    @property
    def striped_fixtures(self):
        ret = {
            k: f if not isinstance(f, FixtureHolder) else f.value
            for k, f in self.fixtures.items()
        }
        return ret

    def open(self, fixtures):
        self._local.opened = True
        self._local.backdoor_opened = False
        if fixtures is not None:
            self._local.fixtures = FixtureStorage(fixtures)

    def close(self):
        self._local.opened = False

    def open_backdoor(self):
        self._local.backdoor_opened = True

    def close_backdoor(self):
        self._local.backdoor_opened = False

    def on_checkout(self, cb):
        self._on_checkout = cb

    def __getattr__(self, k):
        local = self._local
        if not local.opened:
            raise RuntimeError('Shop is closed')
        f = local.fixtures[k]
        if not local.backdoor_opened:
            self._on_checkout(f)
        return f

    @staticmethod
    def _get_fixtures(src_class):
        return {
            k: v for k, v in src_class.__dict__.items()
            if not k.startswith('_')
        }


class _DepsCache(dict):
    def __missing__(self, f):
        v = f.with_deps
        self.__setitem__(f, v)
        return v


class FixtureService(LocalStorage):

    def __init__(self, reverse_postproc=True):
        self._reverse_postproc_order = reverse_postproc
        self._shops = set()

    def init(self, ctx, fitter_ctx, reverse_postproc=None):
        if reverse_postproc is not None:
            self._reverse_postproc_order = reverse_postproc

        local = self._safe_local = types.SimpleNamespace()
        local.involved = OrderedUniqSet()
        local.ctx = ctx
        local.fitter_ctx = fitter_ctx
        fitter_ctx.setdefault('fixtures_deps_cache', _DepsCache())

    def serve(self, shop):
        if shop in self._shops:
            return
        shop.on_checkout(self.use)
        self._shops.add(shop)

    @staticmethod
    def expand_deps(*fixtures):
        ret = OrderedUniqSet()
        [ret.add(*f.with_deps) for f in fixtures]
        return list(ret)

    def use(self, *fixtures, is_expanded = False):
        local = self._safe_local
        ctx = local.ctx
        involved = local.involved
        not_involved = OrderedUniqSet()
        if is_expanded:
            not_involved.add(*[f for f in fixtures if f not in involved])
        else:
            deps_cache = local.fitter_ctx['fixtures_deps_cache']
            [
                not_involved.add(*deps_cache[f])
                for f in fixtures if f not in involved
            ]
        involved.add(*not_involved)
        [f.take_on(ctx) for f in not_involved]

    def on_output(self):
        local = self._safe_local
        ctx = local.ctx
        involved = local.involved
        if self._reverse_postproc_order:
            involved = reversed(involved)
        [obj.on_output(ctx) for obj in involved]

    def finalize(self):
        local = self._safe_local
        involved = local.involved
        if not involved:
            return True
        if self._reverse_postproc_order:
            involved_ = [*reversed(involved)]
        else:
            involved_ = [*involved]
        ctx = local.ctx
        [involved.pop(f) and f.on_finalize(ctx) for f in involved_]


class BaseProcessor:

    __slots__ = ('_local', 'exception_handlers')

    def __init__(self, fixture_service, exception_handlers=None):
        self._fixture_service: FixtureService = fixture_service
        self.exception_handlers = exception_handlers or {}
        self._local = threading.local()
        self._local.bubble_wrap = None
        self._local.ctx = None
        self._local.fun = None
        self._local.fixtures = None
        self._local.shop_fixtures_map = None
        self._local.fitter_ctx = None  # mounted context

    @property
    def ctx(self):
        return self._local.ctx

    def make_core_handler(self, fun, bubble_wrap, front_fixtures, shop_fixtures_map, fitter_ctx):
        expanded_fixtures = self._fixture_service.expand_deps(*front_fixtures)
        process = self.bubble_wrpapped_process if bubble_wrap else self.process

        @functools.wraps(fun)
        def handler(*args, **kwargs):
            local = self._local
            local.bubble_wrap = bubble_wrap
            local.ctx = None
            local.fun = fun
            local.fixtures = expanded_fixtures
            local.shop_fixtures_map = shop_fixtures_map
            local.fitter_ctx = fitter_ctx
            return process(*args, **kwargs)

        return handler

    def bubble_wrpapped_process(self, *args, **kwargs):
        bubble_wrap = self._local.bubble_wrap
        bubble_wrap.setup()
        try:
            return self.process(*args, **kwargs)
        finally:
            bubble_wrap.cleanup(self._local.ctx)

    def process(self, *args, **kwargs):
        try:
            ret = self.process_inner(*args, **kwargs)
            if self._local.ctx.finalize_exceptions:
                self.process_finalize_exceptions()
            return ret
        except BaseException as cur_ex:
            default_handler = self.default_exception_handler
            handler = self.exception_handlers.get(cur_ex.__class__, default_handler)
            max_rehandlered = 10
            ex_stack = [cur_ex]
            while len(ex_stack) < max_rehandlered:
                try:
                    return handler(self._local.ctx, cur_ex)
                except BaseException as ex:
                    next_handler = self.exception_handlers.get(ex.__class__, default_handler)
                    if ex in ex_stack:
                        raise
                    ex_stack.append(cur_ex)
                    cur_ex = ex
                    handler = next_handler
            raise RuntimeError('Max rehandlered exceeded')

    def process_inner(self, *args, **kwargs):
        local = self._local
        local.ctx = RouteContext()
        self.init_context()
        ctx = local.ctx
        fs = self._fixture_service
        fs.init(ctx, local.fitter_ctx)
        opened_shops = [[shop.open(fixtures), shop][-1] for shop, fixtures in local.shop_fixtures_map.items()]
        try:
            ctx.phase = ProcessPhase.SETUP
            fs.use(*local.fixtures, is_expanded=True)
            ctx.phase = ProcessPhase.RUN
            ctx.output = local.fun(*args, **kwargs)
            [opened_shops.pop().close() for _ in [*opened_shops]]
            ctx.phase = ProcessPhase.OUTPUT
            fs.on_output()
            ctx.phase = ProcessPhase.FINALIZE
            return ctx.output
        except BaseException as ex:
            ctx.exception = ex
            ctx.successful = not getattr(ex, 'is_error', True)
            raise
        finally:
            [opened_shops.pop().close() for _ in [*opened_shops]]
            while True:
                try:
                    if fs.finalize():
                        break
                except Exception as ex:
                    ctx.finalize_exceptions.append(ex)
                    if ctx.stop_finalize:
                        raise ex

    def init_context(self):
        pass

    def default_exception_handler(self, ctx, ex):
        raise

    def process_finalize_exceptions(self):
        pass


class FixtureHolder:
    def __init__(self, fixture=None):
        self.value = fixture

    def set(self, fixture):
        self.value = fixture


class Fitter:
    def __init__(self, processor: BaseProcessor, shops,
                 mounter=None, bubble_wrap = None):
        self.mounter = mounter
        self.bubble_wrap = bubble_wrap
        self._processor = processor
        self._shops = shops
        self._registered = {}

    @property
    def shop(self):
        if len(self._shops) > 1:
            raise AttributeError('`shop` is inaccessible since there is more than one shop')
        return self._shops[0]

    @property
    def uses(self):
        [[s.open(None), s.open_backdoor()] for s in self._shops]
        return self._uses

    def _uses(self, *fixtures):
        [[s.close_backdoor(), s.close()] for s in self._shops]

        def registrar(fun):
            meta = self._registered.setdefault(
                fun, types.SimpleNamespace(route_args=[], fixtures=[])
            )
            meta.fixtures.extend(fixtures)
            return fun

        return registrar

    def mount(self, mounter=None, bubble_wrap=None):
        mounter = mounter or self.mounter
        bubble_wrap = bubble_wrap or self.bubble_wrap
        make_handler = self._processor.make_core_handler
        shops_striped_fixtures = {
            s: s.striped_fixtures
            for s in self._shops
        }
        fitter_ctx = {}
        for fun, meta in self._registered.items():
            h = make_handler(fun, bubble_wrap, meta.fixtures, shops_striped_fixtures, fitter_ctx)
            for args, kw in meta.route_args:
                mounter(*args, **kw)(h)

    def __call__(self, *args, **kw):
        def registrar(fun):
            meta = self._registered.setdefault(
                fun, types.SimpleNamespace(route_args=[], fixtures=[])
            )
            meta.route_args.append((args, kw))
            return fun
        return registrar

    @staticmethod
    def _get_striped_fixtures(fixtures):
        ret = [
            f if not isinstance(f, FixtureHolder) else f.value
            for f in fixtures
        ]
        return ret
