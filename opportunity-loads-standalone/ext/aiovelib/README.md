# aiovelib

This is a library for implementing the [Victron d-bus protocol][1] with
python's [asyncio][2] and [dbus-next][3], and to maybe replace the current
[GLib implementation][4] over time or where it makes sense.

### Asyncio usage note

This library now uses `asyncio.get_running_loop()` instead of
`asyncio.get_event_loop()`, which means:

- A running event loop is required, call the library only from async code.
- No implicit fallback loop. Calling APIs that schedule tasks from plain sync
  code or other threads without a loop will raise RuntimeError.
- Make sure to service gets closed properly, either by `await service.close()`
  or by using an async context manager via `async with Service(...)`
- Async callbacks supported. Callbacks like onchange may be async def; they
  are scheduled on the active loop.

If you really need to use the library from sync code, you must create and
manage your own event loop explicitly.

## Components

### aiovelib.service

Use this to implement a new dbus service.

```
from dbus_next.constants import BusType
from dbus_next.aio import MessageBus
from aiovelib.service import Service, IntegerItem, DoubleItem, TextItem

async def main():
	bus = await MessageBus(bus_type=BusType.SESSION).connect()
	service = Service(bus, 'com.victronenergy.grid.example')
	service.add_item(IntegerItem('/Int', 1, writeable=True))
	service.add_item(DoubleItem('/Double', 2.0, writeable=True))
	service.add_item(TextItem('/Text', 'This is text', writeable=True))

	await service.register()
	await bus.wait_for_disconnect()

asyncio.run(main())

```

Updates to paths can be sent by using the Context Manager implementation,
which will bundle all changes in a single `ItemsChanged` notification.

```
with service as ctx:
	ctx['/Int'] = 11
	ctx['/Double'] = 3.141592654
```

### aiovelib.client

This is build around the class `aiovelib.client.Monitor`. `Monitor` listens
for `ItemsChanged` and `PropertiesChanged` signals and keeps track of the
value of remote services and values.

To monitor or communicate with a remote service, Extend the
`aiovelib.client.Service` class and mix in the `ServiceHandler` class, then set
`servicetype' to the name of the service that will be tracked. The monitor will
automatically use your class for tracking the service when it is seen on the
bus.

```
from aiovelib.client import Service, ServiceHandler

class GridMeterService(Service, ServiceHandler):
	servicetype = "com.victronenergy.grid"
```

By default all paths on the service are monitored, but you can monitor
only specific paths by defining `paths`.

```
class GridMeterService(Service, ServiceHandler):
	servicetype = "com.victronenergy.grid"
	paths = { "/Ac/Power", "/Ac/L1/Voltage" }
```

To get a callback whenever a path changes, either pass the optional
itemsChanged parameter when constructing the monitor object, or override it and
implement `itemsChanged`.

```
from dbus_next.constants import BusType
from dbus_next.aio import MessageBus
from aiovelib.client import Monitor

class MyMonitor(Monitor):
	def itemsChanged(self, service, values):
		for p, v in values.items():
			print (f"{service.name}{p} changed to {v}")

bus = await MessageBus(bus_type=BusType.SESSION).connect()
monitor = await MyMonitor.create(bus)
```

or

```
from dbus_next.constants import BusType
from dbus_next.aio import MessageBus
from aiovelib.client import Monitor

def on_update(self, service, values):
		for p, v in values.items():
			print (f"{service.name}{p} changed to {v}")

bus = await MessageBus(bus_type=BusType.SESSION).connect()
monitor = await Monitor.create(bus, on_update)

```

The monitor also includes a way to set values on a remove service. From
synchronous code, you can call `Monitor.set_value_async` to set values.
From Asynchronous code, call `await monitor.set_value` instead.

Caveat: This client is deliberately stripped down and supports only the
`GetItems` method of scanning an entire service. The older methods, namely
to call `GetValue` and `GetText` on the root, is not implemented. If you
need to communicate with a service that does not yet support `GetItems`, it
is suggested that you first implement `GetItems` for that service.

### aiovelib.localsettings

`aiovelib.localsettings.SettingsService` adds a special service class
on  top of `aiovelib.client.Monitor`, that specifically talks to localsettings.
It also implements the `AddSettings` call, so projects can add their settings
to localsettings.

To interface with localsettings, extend `SettingsService`, and again mix in
the `ServiceHandler` class to mark this as the handler for
`com.victronenergy.settings`.

```
from aiovelib.client import Monitor
from aiovelib.localsettings import SettingsService, Setting
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

class MySettings(SettingsService, ServiceHandler):
	pass
```

Settings can be added by await-ing `add_settings`:

```
await service.add_settings(
	Setting("/Settings/AioVelib/OptionA", 3, 0, 5),
	Setting("/Settings/AioVelib/OptionB", "x")
)
```

And in similar fashion to the any other clients, you can monitor for changes
using the `Monitor` object and a custom itemsChanged callback.

Caveat: As with the client implementation, some older support is deliberately
left out. Support for the older `AddSetting` or `AddSilentSetting` calls
are not implemented.

## Code maturity

This is very new and may well change considerably as projects need the library
to support more edge cases.

## Installing with pip

This supports setuptools, and can be installed with pip. In practice we will
likely use it as a submodule, so that each project can carry its own
version and a system-wide update doesn't break multiple service.

[1]: https://github.com/victronenergy/venus/wiki/dbus-api
[2]: https://docs.python.org/3/library/asyncio.html
[3]: https://github.com/altdesktop/python-dbus-next
[4]: https://github.com/victronenergy/velib_python/
