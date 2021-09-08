import threading
import functools
import types
import enum

__version__ = '0.0.1'
__author__ = "Valery Kucherov <valq7711@gmail.com>"
__copyright__ = "Copyright (C) 2021 Valery Kucherov"
__license__ = "MIT"


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
                'py4web hint: this is an attempt to access an uninitialized '
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
        self._with_deps_cached = None
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
        self._with_deps_cached = None
        if len(fixtures) == 1:
            return fixtures[0]
        return fixtures

    @property
    def with_deps(self):
        '''Return a list of all dependencies with the fixture itself.

           [independent ... dependent]
        '''
        if self._with_deps_cached is not None:
            return self._with_deps_cached
        fixtures = []
        reversed_fixtures = []
        stack = [self]
        while stack:
            fixture = stack.pop()
            reversed_fixtures.append(fixture)
            stack.extend(getattr(fixture, "__prerequisites__", ()))
        for fixture in reversed(reversed_fixtures):
            if fixture not in fixtures:
                fixtures.append(fixture)
        self._with_deps_cached = tuple(fixtures)
        return fixtures


BaseFixture.__track_deps_if_instance__.append(BaseFixture)


class OrderedUniqSet(dict):
    add = lambda s, *items: dict.update(s, {it: True for it in items})

    def __init__(self, items=None):
        super().__init__()
        if items:
            self.add(*items)


class FixtureShop:

    @classmethod
    def make_from(cls, src_class):
        ret: 'src_class' = FixtureShop(cls._get_fixtures(src_class))
        return ret

    def __init__(self, fixtures_dict):
        self._local = threading.local()
        self.init_local(fixtures_dict)
        self._on_checkout = None

    def init_local(self, fixtures):
        self._local.backdoor_opened = False
        if fixtures is not None:
            self._local.fixtures = fixtures

    def open_backdoor(self):
        self._local.backdoor_opened = True

    def close_backdoor(self):
        self._local.backdoor_opened = False

    @property
    def fixtures(self):
        return self._local.fixtures

    @staticmethod
    def _get_fixtures(src_class):
        return {
            k: v for k, v in src_class.__dict__.items()
            if not k.startswith('_')
        }

    def on_checkout(self, cb):
        self._on_checkout = cb

    def __getattr__(self, k):
        f = self._local.fixtures[k]
        if not self._local.backdoor_opened:
            self._on_checkout(f)
        return f


class FixtureService(LocalStorage):

    def __init__(self, fixture_shop, reverse_postproc=True):
        self._reverse_postproc_order = reverse_postproc
        self._fixture_shop = fixture_shop
        self._fixture_shop.on_checkout(self.use)

    def init(self, ctx, shop_fixtures, reverse_postproc=None):
        if reverse_postproc is not None:
            self._reverse_postproc_order = reverse_postproc

        local = self._safe_local = types.SimpleNamespace()
        local.involved = OrderedUniqSet()
        local.ctx = ctx
        self._fixture_shop.init_local(shop_fixtures)

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
            [
                not_involved.add(*f.with_deps)
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
        self._local.ctx = None
        self._local.fun = None
        self._local.fixtures = None
        self._local.shop_fixtures = None

    @property
    def ctx(self):
        return self._local.ctx

    def init_context(self):
        pass

    def default_exception_handler(self, ctx, ex):
        raise

    def make_core_handler(self, fun, front_fixtures, shop_fixtures):
        expanded_fixtures = self._fixture_service.expand_deps(*front_fixtures)

        @functools.wraps(fun)
        def handler(*args, **kwargs):
            local = self._local
            local.ctx = None
            local.fun = fun
            local.fixtures = expanded_fixtures
            local.shop_fixtures = shop_fixtures
            return self.process(*args, **kwargs)

        return handler

    def process_inner(self, *args, **kwargs):
        local = self._local
        local.ctx = RouteContext()
        self.init_context()
        ctx = local.ctx
        fs = self._fixture_service
        fs.init(ctx, local.shop_fixtures)
        try:
            ctx.phase = ProcessPhase.SETUP
            fs.use(*local.fixtures, is_expanded=True)
            ctx.phase = ProcessPhase.RUN
            ctx.output = local.fun(*args, **kwargs)
            ctx.phase = ProcessPhase.OUTPUT
            fs.on_output()
            ctx.phase = ProcessPhase.FINALIZE
            return ctx.output
        except BaseException as ex:
            ctx.exception = ex
            ctx.successful = getattr(ex, 'successful', False)
            raise
        finally:
            while True:
                try:
                    if fs.finalize():
                        break
                except Exception as ex:
                    ctx.finalize_exceptions.append(ex)
                    if ctx.stop_finalize:
                        raise ex

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

    def process_finalize_exceptions(self):
        pass


class FixtureHolder:
    def __init__(self, fixture=None):
        self.value = fixture

    def set(self, fixture):
        self.value = fixture


class Fitter:

    def __init__(self, processor: BaseProcessor, fixture_shop: FixtureShop,
                 mounter=None):

        self._mounter = mounter
        self._processor = processor
        self.fixture_shop = fixture_shop
        self._registered = {}

    def _uses(self, *fixtures):
        self.fixture_shop.close_backdoor()

        def registrar(fun):
            meta = self._registered.setdefault(
                fun, types.SimpleNamespace(route_args=[], fixtures=[])
            )
            meta.fixtures.extend(fixtures)
            return fun

        return registrar

    def __call__(self, *args, **kw):
        def registrar(fun):
            meta = self._registered.setdefault(
                fun, types.SimpleNamespace(route_args=[], fixtures=[])
            )
            meta.route_args.append((args, kw))
            return fun
        return registrar

    @property
    def uses(self):
        self.fixture_shop.init_local(None)
        self.fixture_shop.open_backdoor()
        return self._uses

    @staticmethod
    def _get_striped_fixtures(fixtures):
        if isinstance(fixtures, (list, tuple)):
            ret = [
                f if not isinstance(f, FixtureHolder) else f.value
                for f in fixtures
            ]
        elif isinstance(fixtures, dict):
            ret = {
                k: f if not isinstance(f, FixtureHolder) else f.value
                for k, f in fixtures.items()
            }
        return ret

    def mount(self, mounter=None):
        mounter = mounter or self._mounter
        make_handler = self._processor.make_core_handler
        shop_fixtures = self._get_striped_fixtures(self.fixture_shop.fixtures)
        for fun, meta in self._registered.items():
            fixtures = self._get_striped_fixtures(meta.fixtures)
            h = make_handler(fun, fixtures, shop_fixtures)
            for args, kw in meta.route_args:
                mounter(*args, **kw)(h)

