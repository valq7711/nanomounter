import pytest
import types
from omfitt import BaseFixture


class Foo(BaseFixture):
    def __init__(self):
        pass

    def take_on(self, ctx):
        self._safe_local = types.SimpleNamespace(a='a')


class Bar(BaseFixture):
    def __init__(self, foo):
        self.foo = foo


class Baz(BaseFixture):
    def __init__(self, bar, foo):
        self.foo = foo
        self.bar = bar


class UseFix(BaseFixture):
    def __init__(self):
        self.use_fixtures(baz)
        self.use_fixtures(baz)


foo = Foo()
bar = Bar(foo)
baz = Baz(bar, foo)
use_fix = UseFix()


def test_deps():
    assert baz.with_deps == [foo, bar, baz]
    assert baz.with_deps is baz._with_deps_cached
    assert use_fix.__prerequisites__ == [baz]
    assert use_fix.with_deps == [foo, bar, baz, use_fix]
    assert use_fix.use_fixtures(foo) is foo
    assert use_fix.use_fixtures(foo, bar) == (foo, bar)
    assert use_fix._with_deps_cached is None
    assert use_fix.with_deps == [foo, bar, baz, use_fix]


def test_local():
    BaseFixture.__init_request_ctx__()
    foo.take_on({})
    foo._safe_local.a == 'a'


def test_local_err():
    BaseFixture.__init_request_ctx__()
    with pytest.raises(RuntimeError) as err:
        foo._safe_local.a
    assert 'py4web hint' in str(err.value)
