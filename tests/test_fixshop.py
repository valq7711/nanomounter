
import pytest
import types
from orgapyzer import BaseFixture, FixtureShop
from unittest.mock import MagicMock


class Fixture(BaseFixture):
    def on_touch(self, ctx):
        self._safe_local = {}


@pytest.fixture
def foo_bar():
    ret = Fixture(), Fixture()
    BaseFixture.__init_request_ctx__()
    return ret



@pytest.fixture
def shop(foo_bar):
    foo_, bar_ = foo_bar
    @FixtureShop.make_from
    class Shop:
        foo = foo_
        bar = bar_
    return Shop


def test_type(shop):
    assert type(shop) is FixtureShop


def test_fixture_dict(shop, foo_bar):
    foo, bar = foo_bar
    assert shop.fixtures['foo'] is foo
    assert shop.fixtures['bar'] is bar


def test_on_checkout(shop, foo_bar):
    foo, bar = foo_bar
    foo.on_touch({})
    cb = MagicMock()
    shop.on_checkout(cb)
    assert shop.foo is foo
    assert cb.called


def test_backdoor(shop, foo_bar):
    foo, bar = foo_bar
    foo.on_touch({})
    cb = MagicMock()
    shop.on_checkout(cb)
    shop.open_backdoor()
    assert shop.foo is foo
    assert not cb.called
    shop.close_backdoor()
    assert shop.bar is bar
    assert cb.called




