import pytest
import types
from nanomounter import BaseFixture



class Foo(BaseFixture):
    def __init__(self):
        pass
        #self._safe_local = types.SimpleNamespace(a='a')

    def on_touch(self, ctx):
        self._safe_local = types.SimpleNamespace(a='a')

class Bar(BaseFixture):
    def __init__(self, foo):
        self.foo = foo
        pass
        #self._safe_local = types.SimpleNamespace(a='a')


class Baz(BaseFixture):
    def __init__(self, bar, foo):
        #self._safe_local = types.SimpleNamespace(a='a')
        self.foo = foo
        self.bar = bar


class UseFix(BaseFixture):
    def __init__(self):
        #self._safe_local = types.SimpleNamespace(a='a')
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


def test_local():
    BaseFixture.__init_request_ctx__()
    foo.on_touch({})
    foo._safe_local.a == 'a'

def test_local_err():
    BaseFixture.__init_request_ctx__()
    with pytest.raises(RuntimeError) as err:
        foo._safe_local.a
    assert 'py4web hint' in str(err.value)
