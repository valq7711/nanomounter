# omfitt (One More FITTer)
Actions fitter for py4web.

An example to showcase the idea (see https://github.com/web2py/py4web/tree/futuristic)

## This is reusable module that can be imported by applications
```python
# apps/reusable/__init__.py
import os
from py4web import Action, App
from omfitt import FixtureShop, BaseFixture, FixtureHolder


class Titleizer(BaseFixture):
    def on_output(self, app_ctx, ctx):
        ctx.output = ctx.output.title()


class Counter(BaseFixture):
    def __init__(self):
        self.cnt = 1
        self.clients = {}

    def take_on(self, app_ctx, ctx):
        self.clients.update(ctx.ask('client'))


@FixtureShop.make_from
class shop:
    # fixture to be set by client applications  - see below
    client_counter = FixtureHolder()

    # fitter-fixture - the same for all clients,
    # so you can easily implement something like CAS!
    own_counter = Counter()
    titleizer = Titleizer()


action = Action(shop)
app = App(__name__, action)


@action("api/index")
@action.uses(shop.titleizer)
def api_index():
    c_cnt = shop.client_counter.cnt
    o_cnt = shop.own_counter.cnt
    clients = str(shop.own_counter.clients)
    shop.client_counter.cnt += 1
    shop.own_counter.cnt += 1
    app.response.headers['Content-Type'] = 'text/plain'
    return (
        'hello world! client_counter= {} / total= {} \n clients counters: {}'
        .format(c_cnt, o_cnt, clients)
    )
```


## This is client application
```python
# apps/client/__init__.py

import os
from py4web import Action, App
from omfitt import BaseFixture
from ..reusable import app as reusable_app


class Counter(BaseFixture):
    def __init__(self) -> None:
        self.cnt = 1

    def take_on(self, app_ctx, ctx):
        # expose counter value so that the following
        # fixtures can access it via ctx.ask('client')
        ctx.provide('client', {app_ctx.name: self.cnt})


app = App(_name__, Action())
ctx = app.mount()

reusable_app.shop.fixtures.client_counter = Counter()
reusable_app.mount('first', ctx, base_url='first')

reusable_app.shop.fixtures.client_counter = Counter()
reusable_app.mount('second', ctx, base_url='second')

# now you can try
#  http://127.0.0.1:8000/client/first/api/index
#  http://127.0.0.1:8000/client/second/api/index
#
# refresh the pages several times to see that each route
# has its own client_counter and common total
```
