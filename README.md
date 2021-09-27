# omfitt (One More FITTer)
Actions fitter for py4web.

```diff
- Example is out of date, sorry :(
```

An example to showcase the idea

## This is reusable module that can be imported by applications
```python
# apps/asset/fitter_srv.py

from omfitt import BaseProcessor, Fitter, FixtureShop, BaseFixture, FixtureService, LocalStorage, FixtureHolder
from py4web.core import bottle

bottle.default_app().add_hook("before_request", LocalStorage.__init_request_ctx__)


class Processor(BaseProcessor):
    pass


class Titleizer(BaseFixture):
    def on_output(self, ctx):
        ctx.output = ctx.output.title()


@FixtureShop.make_from
class shop:
    # fixture to be set by client applications  - see below
    client_session = FixtureHolder()

    # fitter-fixture - the same for all clients,
    # so you can easily implement something like CAS!
    titleizer = Titleizer()


fs = FixtureService()

fs.serve(shop)
fitter = Fitter(Processor(fs), [shop])


@fitter("api/index")
@fitter.uses(shop.titleizer)
def api_index():
    return 'hellow world! counter= {}'.format(shop.client_session.cnt)
```


## This is client application
```python
# /apps/fitter_client/__init__.py

from py4web import action
from omfitt import BaseFixture
from apps.asset.fitter_srv import fitter


class Session(BaseFixture):
    def __init__(self):
        self.cnt = 0

    def take_on(self, ctx):
        self.cnt += 1


def make_router(base):
    def router(route, *a, **kw):
        return lambda f: action(f'{base}/{route}', *a, **kw)(f)
    return router


fitter.shop.fixtures.client_session = Session()
fitter.mount(make_router('first'))


# mount fitter on another base route with another session
# i.e. this can be repeated in any other application
fitter.shop.fixtures['client_session'] = Session()
fitter.mount(make_router('second'))

# now you can try
#  http://127.0.0.1:8000/fitter_client/first/api/index
#  http://127.0.0.1:8000/fitter_client/second/api/index
#
# refresh the pages several times to see that each route has its own session counter
```
