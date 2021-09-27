import threading
import functools
import enum
import inspect
from collections import UserDict
from types import SimpleNamespace

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
        'successful', 'phase', 'stop_finalize',
        'app_ctx', '_provided'
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
        self.app_ctx = {}
        self._provided = {}

    def provide(self, key, obj):
        if key in self._provided:
            raise KeyError(f'Key is already in use: {key}')
        self._provided[key] = obj

    def ask(self, key, default=None):
        return self._provided.get(key, default)


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


class BaseGateway(LocalStorage):
    def setup(self, app_ctx):
        pass

    def cleanup(self, app_ctx, route_ctx):
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

    def take_on(self, app_ctx, ctx):
        ''' Called before run or before direct use of the fixture.

            Not called if an exception is raised before,
            i.e. from previous try-to-taken fixture.
        '''
        pass

    def on_output(self, app_ctx, ctx):
        ''' Called after successful core-function run.

            Not called if an exception is raised before.
        '''
        pass

    def on_finalize(self, app_ctx, ctx):
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
    __slots__ = ('_local', '_on_checkout')

    @classmethod
    def make_from(cls, src_class):
        ret: 'src_class' = FixtureShop(cls._get_fixtures(src_class))
        return ret

    def __init__(self, fixtures_dict):
        self._local = threading.local()
        this = self._local.this = SimpleNamespace()
        this.fixtures = None
        self.open(fixtures_dict)
        self._on_checkout = None

    @property
    def fixtures(self):
        return self._local.this.fixtures

    @property
    def striped_fixtures(self):
        ret = {
            k: f if not isinstance(f, FixtureHolder) else f.value
            for k, f in self.fixtures.items()
        }
        return ret

    def open(self, fixtures):
        this = self._local.this
        this.opened = True
        this.backdoor_opened = False
        if fixtures is not None:
            this.fixtures = FixtureStorage(fixtures)
        return self

    def close(self):
        self._local.this.opened = False

    def open_backdoor(self):
        self._local.this.backdoor_opened = True

    def close_backdoor(self):
        self._local.this.backdoor_opened = False

    def on_checkout(self, cb):
        self._on_checkout = cb

    def __getattr__(self, k):
        this = self._local.this
        if not this.opened:
            raise RuntimeError('Shop is closed')
        f = this.fixtures[k]
        if not this.backdoor_opened:
            self._on_checkout and self._on_checkout(f)
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

    def init(self, app_ctx, ctx, staff_ctx, reverse_postproc=None):
        if reverse_postproc is not None:
            self._reverse_postproc_order = reverse_postproc

        local = self._safe_local = SimpleNamespace()
        local.involved = OrderedUniqSet()
        local.app_ctx = app_ctx
        local.ctx = ctx
        local.staff_ctx = staff_ctx
        staff_ctx.setdefault('fixtures_deps_cache', _DepsCache())

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
        app_ctx = local.app_ctx
        involved = local.involved
        not_involved = OrderedUniqSet()
        if is_expanded:
            not_involved.add(*[f for f in fixtures if f not in involved])
        else:
            deps_cache = local.staff_ctx['fixtures_deps_cache']
            [
                not_involved.add(*deps_cache[f])
                for f in fixtures if f not in involved
            ]
        involved.add(*not_involved)
        [f.take_on(app_ctx, ctx) for f in not_involved]

    def on_output(self):
        local = self._safe_local
        ctx = local.ctx
        app_ctx = local.app_ctx
        involved = local.involved
        if self._reverse_postproc_order:
            involved = reversed(involved)
        [obj.on_output(app_ctx, ctx) for obj in involved]

    def finalize(self):
        local = self._safe_local
        involved = local.involved
        if not involved:
            return True
        if self._reverse_postproc_order:
            involved_ = [*reversed(involved)]
        else:
            involved_ = [*involved]
        app_ctx = local.app_ctx
        ctx = local.ctx
        [involved.pop(f) and f.on_finalize(app_ctx, ctx) for f in involved_]


class Ctx:
    request = None
    response = None
    app_ctx = None

    def make_ctx(self, app_ctx, request, response):
        ret: Ctx = SimpleNamespace(
            app_ctx = app_ctx,
            request = request,
            response = response,
        )
        return ret


class BubbleException(BaseException):
    def __init__(self, wrapped_exception):
        super().__init__(f'BubbleException for {str(wrapped_exception)}')
        self.wrapped_exception = wrapped_exception


class BaseProcessor:

    __slots__ = ('_local', 'inject_class')

    def __init__(self, inject_class = None):
        self.inject_class = inject_class or Ctx
        self._local = threading.local()
        this = self._local.this = SimpleNamespace()
        this.gateway = None
        this.ctx = None
        this.fun = None
        this.fixtures = None
        this.shop_fixtures_map = None
        this.fitter_ctx = None  # mounted context
        this.fixture_service = None
        this.exception_handlers = {}
        this.inject = None

    def _get_inject(self, fun):
        aspec = inspect.getfullargspec(fun)
        kwdefs = aspec.kwonlydefaults
        if not kwdefs:
            if aspec.defaults:
                kw_names = aspec.args[-len(aspec.defaults):]
                kwdefs = {k: v for k, v in zip(kw_names, aspec.defaults)}
            else:
                return None, None
        inject = [(k, v) for k, v in kwdefs.items() if isinstance(v, self.inject_class)]
        if not inject:
            return None, None
        arg_nm, ctx_maker = inject.pop()
        if inject:
            raise TypeError('Only one inject-arg allowed')
        return arg_nm, ctx_maker

    @property
    def ctx(self):
        return self._local.this.ctx

    def make_core_handler(self, fun, gateway, fixture_service,
                          front_fixtures, shop_fixtures_map, fitter_ctx,
                          exception_handlers=None):

        expanded_fixtures = fixture_service.expand_deps(*front_fixtures)
        exception_handlers = exception_handlers or {}
        if '*' not in exception_handlers:
            exception_handlers['*'] = self.exception_default_handler
        inject = self._get_inject(fun)

        this = SimpleNamespace()
        this.gateway = gateway
        this.ctx = None
        this.fun = fun
        this.fixture_service = fixture_service
        this.fixtures = expanded_fixtures
        this.shop_fixtures_map = shop_fixtures_map
        this.fitter_ctx = fitter_ctx
        this.exception_handlers = exception_handlers
        this.inject = inject

        @functools.wraps(fun)
        def handler(*args, **kwargs):
            self._local.this = this
            return self.gateway(*args, **kwargs)

        return handler

    def gateway(self, *args, **kwargs):
        this = self._local.this
        this.ctx = RouteContext()
        self.init_context()
        gateway = this.gateway
        if not gateway:
            return self.bubble_wrap(*args, **kwargs)
        app_ctx = this.fitter_ctx['app_ctx']
        gateway.setup(app_ctx, this.ctx)
        try:
            return self.bubble_wrap(*args, **kwargs)
        finally:
            gateway.cleanup(app_ctx, this.ctx)

    def bubble_wrap(self, *args, **kwargs):
        this = self._local.this
        exception_handlers = this.exception_handlers
        try:
            ret = self.process(*args, **kwargs)
            if this.ctx.finalize_exceptions:
                self.process_finalize_exceptions()
            return ret
        except BaseException as cur_ex:
            app_ctx = this.fitter_ctx['app_ctx']
            default_handler = exception_handlers['*']
            handler = exception_handlers.get(cur_ex.__class__, default_handler)
            max_rehandlered = 10
            ex_stack = [cur_ex]
            while len(ex_stack) < max_rehandlered:
                try:
                    return handler(app_ctx, this.ctx, cur_ex)
                except BubbleException as ex:
                    raise ex.wrapped_exception
                except BaseException as ex:
                    if ex is cur_ex:
                        return default_handler(app_ctx, this.ctx, cur_ex)
                    next_handler = exception_handlers.get(ex.__class__, default_handler)
                    ex_stack.append(cur_ex)
                    cur_ex = ex
                    handler = next_handler
            return default_handler(app_ctx, this.ctx, RuntimeError('Max rehandlered exceeded'))

    def process(self, *args, **kwargs):
        this = self._local.this
        ctx = this.ctx
        app_ctx = this.fitter_ctx['app_ctx']
        kw_name, ctx_maker = this.inject
        if kw_name:
            kwargs[kw_name] = ctx_maker.make_ctx(
                app_ctx, ctx.request, ctx.response
            )
        fs: FixtureService = this.fixture_service
        fs.init(app_ctx, ctx, this.fitter_ctx['staff_ctx'])
        opened_shops = [
            shop.open(fixtures)
            for shop, fixtures in this.shop_fixtures_map.items()
        ]
        try:
            ctx.phase = ProcessPhase.SETUP
            fs.use(*this.fixtures, is_expanded=True)
            ctx.phase = ProcessPhase.RUN
            ctx.output = this.fun(*args, **kwargs)
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

    def exception_default_handler(self, app_ctx, ctx, ex):
        raise

    def process_finalize_exceptions(self):
        pass


class FixtureHolder:
    def __init__(self, fixture=None):
        self.value = fixture

    def set(self, fixture):
        self.value = fixture


class BaseAction:
    def __init__(self, fitter: 'Fitter'):
        self._fitter = fitter
        self._in_use = False
        self._registered = {}

    def __call__(self, *args, **kw):
        path, method, name, prop, kw = self._parse_action_args(args, kw)

        def registrar(fun):
            self._register(fun, route_args=(path, method, name, prop, kw))
            return fun
        return registrar

    def _register(self, fun, *, route_args=None, fixtures=None):
        if not (fixtures or route_args):
            raise TypeError('At least one arg is required: route_args or fixtures')
        meta = self._registered.setdefault(
            fun, SimpleNamespace(route_args=[], fixtures=[])
        )
        if route_args:
            meta.route_args.append(route_args)
        if fixtures:
            meta.fixtures.extend(fixtures)

    def make_handlers(self, app_ctx, app):
        yield from self._fitter.make_handlers(self._registered, app_ctx, app)

    @property
    def registered(self):
        return self._registered

    @property
    def fitter(self):
        return self._fitter

    def _parse_action_args(self, args, kw):
        return args, kw

    @property
    def uses(self):
        if not self._in_use:
            self._fitter.freeze_shops()
            self._in_use = True
        [[s.open(None), s.open_backdoor()] for s in self._shops]
        return self._uses

    def _uses(self, *fixtures):
        [[s.close_backdoor(), s.close()] for s in self._shops]
        fixtures = self._parse_uses_args(fixtures)

        def registrar(fun):
            '''
            meta = self._registered.setdefault(
                fun, SimpleNamespace(route_args=[], fixtures=[])
            )
            meta.fixtures.extend(fixtures)
            '''
            self._register(fun, fixtures=fixtures)
            return fun

        return registrar

    @property
    def _shops(self):
        return self._fitter._shops

    def _parse_uses_args(self, fixtures):
        return fixtures


class Fitter:
    def __init__(
            self,
            processor: BaseProcessor,
            fixture_service: FixtureService,
            shops,
            exception_handlers=None,
            default_fixtures=None
    ):

        # allow to implement `mounter`
        self.outer_wrappers, self.inner_wrappers = default_fixtures or ([], [])
        self.exception_handlers = exception_handlers or {}
        self.processor = processor
        self._fixture_service = fixture_service
        self._shops = tuple(shops)
        self._shops_frozen = False

    def error(self, exception_class=None, handler=None):
        if not handler:
            return lambda h: self.error(exception_class, h)
        self.exception_handlers[exception_class] = handler
        return handler

    def freeze_shops(self):
        if self._shops_frozen:
            return
        fs = self._fixture_service
        [fs.serve(s) for s in self._shops]
        self._shops_frozen = True

    @property
    def shops(self):
        return self._shops

    @shops.setter
    def shops(self, shops):
        if self._shops_frozen:
            raise AttributeError('After the first `action.use()`-call, the property becomes locked')
        self._shops = shops

    @property
    def shop(self) -> FixtureShop:
        if len(self._shops) > 1:
            raise AttributeError('`shop` is inaccessible since there is more than one shop')
        return self._shops[0]

    def make_handlers(self, registered, app_ctx, app):
        shops_striped_fixtures = {
            s: s.striped_fixtures
            for s in self._shops
        }
        fitter_ctx = dict(
            staff_ctx = {},  # used for cache
            app_ctx = app_ctx  # used as app_ctx (e.g. to store app_name)
        )
        exception_handlers = self.exception_handlers.copy()
        for fun, meta in registered.items():
            h = self._make_handler(
                fun, meta.fixtures, shops_striped_fixtures,
                fitter_ctx, exception_handlers, app
            )
            # yield handler for routing
            yield h, meta

    def _make_handler(self, fun, fixtures, shops_striped_fixtures,
                      fitter_ctx, exception_handlers, app):
        make_core_handler = self.processor.make_core_handler
        gateway = app
        fixtures = self.outer_wrappers + fixtures + self.inner_wrappers
        core_handler = make_core_handler(
            fun, gateway, self._fixture_service, fixtures,
            shops_striped_fixtures, fitter_ctx,
            exception_handlers
        )
        return core_handler


class EmptyObj:
    def __getattr__(self, k):
        return None


empty_ctx = EmptyObj()


class BaseCtx:
    def __init__(self, app, name, master_ctx: 'BaseCtx' = None, props=None,):

        self.name = name
        self.app = app
        self.parent = None
        self.children = {}
        # mount-specific props like base_url, routes_map, static/template folder ...
        self.props = props

        self.named_routes = {}
        self.routes = []

        if master_ctx:
            if name in master_ctx.children:
                raise KeyError(f'Name is already in use: {name}')
            master_ctx.children[name] = self
            self.parent = master_ctx
        else:
            master_ctx = empty_ctx

        self.root = master_ctx.root or self
        self.mount_stack = (master_ctx.mount_stack or tuple()) + (self,)


class BaseApp:
    def __init__(self, action: BaseAction):
        self._action = action
        self._local = threading.local()
        self._app_props = dict()
        self._app_methods = dict()

    def add_prop(self, name, prop: 'BaseAppProp'):
        self._app_props[name] = prop

    def add_method(self, name, meth: 'BaseAppMethod'):
        self._app_methods[name] = meth
        setattr(self, name, meth)

    def mount(self, name, master_ctx=None, **props):
        app_ctx = self._make_ctx(name, master_ctx, props)
        for h, meta in self._action.make_handlers(app_ctx, self):
            for args in meta.route_args:
                self._mount_route(app_ctx, h, args)
        return app_ctx

    def _make_ctx(self, name, master_ctx, props):
        return BaseCtx(self, name, master_ctx, props)

    def _mount_route(self, ctx: BaseCtx, fun, route_args):
        pass

    def __getattr__(self, p):
        prop: 'BaseAppProp' = self._app_props[p]
        ret = prop.setup()
        if ret is None:
            ret = prop
        self._local.used_props.append([p, prop])
        setattr(self, p, ret)
        return ret

    def __getitem__(self, app_name):
        """Return initialized child app."""
        local = self._local
        app = local.used_apps.get(app_name)
        if app:
            return app
        ctx = local.ctx.children[app_name]
        app = ctx.app
        app.setup(ctx, None)
        local.used_apps[app_name] = app
        return app

    # process hooks
    def setup(self, ctx: BaseCtx, route_ctx: RouteContext):
        local = self._local
        local.ctx = ctx
        local.used_methods = set()
        local.used_props = []
        local.used_apps = {}
        # route_ctx.provide('url', self.url)

    def cleanup(self, ctx=None, route_ctx=None):
        local = self._local
        [[prop.cleanup(), self.__dict__.pop(p)] for p, prop in self._local.used_props]
        [app.cleanup() for app in local.used_apps.values()]
        [meth.cleanup() for meth in local.used_methods]
        local.ctx = None
        local.used_methods = None
        local.used_props = None
        local.used_apps = None


class BaseAppProp:
    def __init__(self, app: BaseApp):
        self._app = app

    def setup(self):
        return self

    def cleanup(self):
        pass


class BaseAppMethod(BaseAppProp):
    def __init__(self, app):
        self._app = app
        self._app_local = app._local

    def __call__(self):
        app_used_methods = self._app_local.used_methods
        if self not in app_used_methods:
            self.setup()
            app_used_methods.add(self)
