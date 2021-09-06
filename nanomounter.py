import threading
import functools
import types

__version__ = '0.0.1'
__author__ = "Valery Kucherov <valq7711@gmail.com>"
__copyright__ = "Copyright (c) 2009-2018, Marcel Hellkamp; Copyright (C) 2021 Valery Kucherov"
__license__ = "MIT"


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
        self.phase = None
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

    def on_touch(self, ctx):
        pass  # called when a request arrives or fixture is touched in core-function

    def on_output(self, ctx):
        pass  # called after successful core-function run

    def finalize(self, ctx):
        pass  # called anyawy after core-function run

    def use_fixtures(self, *fixtures):
        '''
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
        self.init_local()
        self.fixtures = fixtures_dict
        self._on_checkout = None

    def init_local(self):
        self._local.backdoor_opened = False

    def open_backdoor(self):
        self._local.backdoor_opened = True

    def close_backdoor(self):
        self._local.backdoor_opened = False

    @staticmethod
    def _get_fixtures(src_class):
        return {
            k: v for k, v in src_class.__dict__.items()
            if isinstance(v, BaseFixture)
        }

    def on_checkout(self, cb):
        self._on_checkout = cb

    def __getattr__(self, k):
        f = self.fixtures[k]
        if not self._local.backdoor_opened:
            self._on_checkout(f)
        return f


class FixtureService(LocalStorage):

    def init(self, ctx):
        local = self._safe_local = types.SimpleNamespace()
        local.involved = OrderedUniqSet()
        local.ctx = ctx

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
        [f.on_touch(ctx) for f in not_involved]

    def on_output(self):
        local = self._safe_local
        ctx = local.ctx
        involved = local.involved
        [obj.on_output(ctx) for obj in involved]

    def finalize(self):
        local = self._safe_local
        involved = local.involved
        if not involved:
            return True
        ctx = local.ctx
        [involved.pop(f) and f.finalize(ctx) for f in [*involved]]


class BaseProcessor:

    __slots__ = ('_local', 'exception_handlers')

    def __init__(self, fixture_service, exception_handlers):
        self._fixture_service: FixtureService = fixture_service
        self.exception_handlers = exception_handlers or {}
        self._local = threading.local()
        self._local.ctx = None
        self._local.fun = None
        self._local.fixtures = None

    def default_exception_handler(self, ctx):
        raise

    def make_core_handler(self, fun, front_fixtures):
        expanded_fixtures = self._fixture_service.expand_deps(front_fixtures)

        @functools.wraps(fun)
        def handler(*args, **kwargs):
            local = self._local
            local.ctx = None
            local.fun = fun
            local.fixtures = expanded_fixtures
            return self.process(*args, **kwargs)

        return handler

    def init_context(self):
        pass

    def process_inner(self, *args, **kwargs):
        local = self._local
        ctx = local.ctx = RouteContext()
        self.init_context()
        fs = self._fixture_service
        fs.init(ctx)
        try:
            ctx.phase = 'request'
            fs.use(*local.fixtures, is_expanded=True)
            ctx.phase = 'run'
            ctx.output = self.fun(*args, **kwargs)
            ctx.phase = 'output'
            fs.on_output()
            ctx.phase = 'finalize'
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
        except BaseException as ex:
            default_handler = self.default_exception_handler
            handler = self.exception_handlers.get(ex.__class__, default_handler)
            max_rehandlered = 10
            cnt = 0
            while cnt < max_rehandlered:
                try:
                    return handler(self._local.ctx)
                except BaseException as ex:
                    next_handler = self.exception_handlers.get(ex.__class__, default_handler)
                    if next_handler is handler:
                        raise
                    handler = next_handler
                    cnt += 1
            raise RuntimeError('Max rehandlered exceeded')

    def process_finalize_exceptions(self):
        pass


class Action:

    def __init__(self, processor, fx_shop, mounter=None):
        self._mounter = mounter
        self._processor: BaseProcessor = processor
        self._fx_shop: FixtureShop = fx_shop
        self._registered = {}

    def _uses(self, *fixtures):
        self._fx_shop.close_backdoor()

        def registrar(fun):
            meta = self._registered.setdefault(
                fun, types.SimpleNamespace(routes_args=[], fixtures=[])
            )
            meta.fixtures.extend(fixtures)
            return fun

        return registrar

    def __call__(self, *args, **kw):
        def registrar(fun):
            meta = self._registered.setdefault(
                fun, types.SimpleNamespace(routes_args=[], fixtures=[])
            )
            meta.route_args.append((args, kw))
            return fun
        return registrar

    @property
    def uses(self):
        self._fx_shop.init_local()
        self._fx_shop.open_backdoor()
        return self._uses

    def mount(self, mounter=None):
        mounter = mounter or self._mounter
        make_handler = self._processor.make_core_handler
        for fun, meta in self._registered.items():
            h = make_handler(fun, meta.fixtures)
            for args, kw in meta.route_args:
                mounter(*args, **kw)(h)

