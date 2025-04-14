from aiovelib.client import make_variant, Monitor, ServiceHandler, DbusException
from aiovelib.client import Service

SETTINGS_SERVICE = "com.victronenergy.settings"
SETTINGS_INTERFACE = "com.victronenergy.Settings"
SETTINGS_PATH = "/Settings"

class SettingException(DbusException):
	pass

class Setting(dict):
	def __init__(self, path, default, _min=None, _max=None, silent=False, alias=None):
		super().__init__(path=make_variant(path), default=make_variant(default))
		if _min is not None:
			self["min"] = make_variant(_min)
		if _max is not None:
			self["max"] = make_variant(_max)
		if silent:
			self["silent"] = make_variant(1)
		self.alias = alias

class SettingsService(Service):
	servicetype = SETTINGS_SERVICE
	paths = set() # Empty set
	aliases = {}

	async def add_settings(self, *settings):
		# Update the alaises
		self.aliases.update((s.alias, s["path"].value) for s in settings)

		# add settings
		reply = await self.monitor.dbus_call(SETTINGS_SERVICE, "/",
			"AddSettings", "aa{sv}", list(settings),
			interface=SETTINGS_INTERFACE)

		# process results, store current values. This avoids an additional
		# call to GetValue.
		for result in reply[0]:
			path = result["path"].value
			if result["error"].value == 0:
				self.paths.add(path)
				self.values[path].update(result["value"].value)
			else:
				raise SettingException(path)

	def alias(self, a):
		return self.aliases.get(a)

if __name__ == "__main__":
	import asyncio
	try:
		from dbus_fast.aio import MessageBus
		from dbus_fast.constants import BusType
	except ImportError:
		from dbus_next.aio import MessageBus
		from dbus_next.constants import BusType

	class MyMonitor(Monitor):
		def itemsChanged(self, service, values):
			""" Callback """
			for p, v in values.items():
				print (f"{service.name}{p} changed to {v}")

	class MySettingsService(SettingsService, ServiceHandler):
		pass

	async def main():
		bus = await MessageBus(bus_type=BusType.SESSION).connect()
		monitor = await MyMonitor.create(bus)

		service = await monitor.wait_for_service(SETTINGS_SERVICE)
		await service.add_settings(
			Setting("/Settings/AioVelib/OptionA", 3, 0, 5),
			Setting("/Settings/AioVelib/OptionB", "x")
		)

		await bus.wait_for_disconnect()

	asyncio.get_event_loop().run_until_complete(main())
