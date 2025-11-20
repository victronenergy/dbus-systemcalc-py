import asyncio
from collections import defaultdict
import logging

try:
	from dbus_fast import Message, MessageType, Variant
except ImportError:
	from dbus_next import Message, MessageType, Variant

log = logging.getLogger(__name__)

BUS_DAEMON_IFACE = "org.freedesktop.DBus"
BUS_DAEMON_NAME = "org.freedesktop.DBus"
BUS_DAEMON_PATH = "/org/freedesktop/DBus"
IFACE = "com.victronenergy.BusItem"
SIGNAL = MessageType.SIGNAL # for performance

def servicetype(busname):
	return ".".join(busname.split(".")[:3])

def make_variant(value):
	""" Change python value into a dbus-next representation of variant. """
	# This only handles the basic types for now. Extend as required
	if value is None:
		return Variant("ai", [])
	if isinstance(value, float):
		return Variant("d", value)
	if isinstance(value, bool):
		return Variant("b", value)
	if isinstance(value, int):
		if value.bit_length() > 32:
			return Variant("x", value)
		else:
			return Variant("i", value)
	if isinstance(value, str):
		return Variant("s", value)
	if isinstance(value, list):
		if all (isinstance(v, int) | isinstance(v, float) for v in value):
			return Variant("ad", value)

class DbusException(Exception):
	pass

class Item(object):
	def __init__(self):
		self.value = None
		self.seen = False

	def update(self, value):
		self.value = None if value == [] else value
		self.seen = True

	def update_unseen(self, value):
		# update only if not seen yet. Used during scanning.
		if not self.seen:
			self.update(value)

class Service(object):
	""" Encapsulates a watched service. Set paths to the paths that should be
	    tracked. """

	handlers = {}
	paths = None # match all paths
	make_item = Item # So it can be overriden by subclasses

	def __init__(self, monitor, name, owner):
		self.monitor = monitor
		self.name = name
		self.owner = owner
		self.matches = []
		self.values = defaultdict(self.make_item)

	@classmethod
	def add_handler(cls, service, handler):
		cls.handlers[service] = handler

	@classmethod
	def handler(cls, service):
		""" Decorator, so the handler can also be manually added instead of
		    automatically via ServiceHandler. """
		def wrap(func):
			cls.add_handler(service, func)
			return func
		return wrap

	def update_items(self, items):
		updated = {}
		for path, attrs in items.items():
			if self.paths is None or path in self.paths:
				try:
					self.values[path].update(v := attrs["Value"].value)
				except KeyError:
					pass # No Value on this change, okay...
				else:
					updated[path] = v

		return updated

	def update_unseen_items(self, items):
		# This is identical to update_items, but only updates unseen items.
		# The duplication is to keep update_items free from logic for this
		# lesser-used method.
		updated = {}
		for path, attrs in items.items():
			if self.paths is None or path in self.paths:
				try:
					self.values[path].update_unseen(v := attrs["Value"].value)
				except KeyError:
					pass # No Value on this change, okay...
				else:
					updated[path] = v

		return updated

	async def fetch_value(self, path):
		""" Fetch a specific value, and cache it. """
		if self.paths is not None:
			self.paths.add(path)

		try:
			reply = await self.monitor.dbus_call(self.name, path, "GetValue", "")
		except DbusException:
			log.exception("fetch_value")
		else:
			self.values[path].update(v := reply[0].value)
			return v

		return None

	async def fetch_min(self, path):
		try:
			reply = await self.monitor.dbus_call(self.name, path, "GetMin", "")
		except DbusException:
			log.exception("fetch_max")
		else:
			return reply[0].value

	async def fetch_max(self, path):
		try:
			reply = await self.monitor.dbus_call(self.name, path, "GetMax", "")
		except DbusException:
			log.exception("fetch_max")
		else:
			return reply[0].value

	def get_value(self, path):
		try:
			return self.values.get(path).value
		except AttributeError:
			return None

	def set_value_async(self, path, value):
		return self.monitor.set_value_async(self.name, path, value)

	async def set_value(self, path, value):
		return await self.monitor.set_value(self.name, path, value)

	def seen(self, path):
		try:
			return self.values.get(path).seen
		except AttributeError:
			pass
		return False

class ServiceHandler(object):
	""" Keeps tracks of classes that handles services. Mix this into
	    your classes to have the monitor load it automatically when
	    the dbus service is discovered. """
	def __init_subclass__(cls, **kwargs):
		""" Since python3.6. This allows keeping track of any classes
		    that extends this one. """
		super().__init_subclass__(**kwargs)
		Service.add_handler(cls.servicetype, cls)

class Monitor(object):
	""" Monitors for service changes. """
	@classmethod
	async def create(cls, bus, *args, **kwargs):
		m = cls(bus, *args, **kwargs)

		bus.add_message_handler(m.handle_message)

		# Subscribe to NameOwnerChanged
		await m.add_match(arg0namespace="com.victronenergy",
			member="NameOwnerChanged")

		# Scan existing services. This can be done in parallel.
		await asyncio.gather(*(m.add_service(name, owner)
			for name, owner in await m.list_dbus_services()))

		return m

	def __init__(self, bus, itemsChanged=None, handlers=None):
		self.bus = bus
		self._services = {}
		self._servicesByName = {}
		self._itemsChanged = itemsChanged
		self._handlers = Service.handlers if handlers is None else handlers

	def itemsChanged(self, service, values):
		""" Default calls whatever was passed to the constructor, but
		    you can override this in a subclass. """
		if self._itemsChanged is not None:
			return self._itemsChanged(service, values)

	async def serviceAdded(self, service):
		""" Default method, called when service is added. """
		pass

	async def serviceRemoved(self, service):
		""" called when service is removed. """
		pass

	async def add_match(self, **kwargs):
		await self.bus.call(
			Message(
				destination=BUS_DAEMON_NAME,
				interface=BUS_DAEMON_IFACE,
				path=BUS_DAEMON_PATH,
				member="AddMatch",
				signature="s",
				body=[",".join(f"{k}={v}" for k, v in kwargs.items())]))

	async def remove_match(self, **kwargs):
		await self.bus.call(
			Message(
				destination=BUS_DAEMON_NAME,
				interface=BUS_DAEMON_IFACE,
				path=BUS_DAEMON_PATH,
				member="RemoveMatch",
				signature="s",
				body=[",".join(f"{k}={v}" for k, v in kwargs.items())]))

	async def get_dbus_name_owner(self, name):
		reply = await self.bus.call(
			Message(
				destination=BUS_DAEMON_NAME,
				interface=BUS_DAEMON_IFACE,
				path=BUS_DAEMON_PATH,
				member="GetNameOwner",
				signature="s",
				body=[name]))
		if reply.message_type != MessageType.ERROR:
			return reply.body[0]
		return None

	async def list_dbus_services(self):
		reply = await self.bus.call(
			Message(
				destination=BUS_DAEMON_NAME,
				interface=BUS_DAEMON_IFACE,
				path=BUS_DAEMON_PATH,
				member="ListNames",
				signature="",
				body=[]))

		if reply.message_type == MessageType.ERROR:
			return []

		services = []
		for n in reply.body[0]:
			if not n.startswith("com.victronenergy."): continue
			owner = await self.get_dbus_name_owner(n)
			if owner is not None:
				services.append((n, owner))

		return services


	def handle_message(self, msg):
		if msg.message_type != SIGNAL:
			return False # only signals handled below

		if msg.member == "ItemsChanged":
			try:
				service = self._services[msg.sender]
			except KeyError:
				pass # no such service
			else:
				updated = service.update_items(msg.body[0])
				if updated:
					self.itemsChanged(service, updated)
		elif msg.member == "PropertiesChanged":
			try:
				service = self._services[msg.sender]
			except KeyError:
				pass # no such service
			else:
				updated = service.update_items({ msg.path: msg.body[0] })
				if updated:
					self.itemsChanged(service, updated)
		elif msg.member == "NameOwnerChanged":
			return self.name_owner_changed(*msg.body)

	def name_owner_changed(self, name, old, new):
		asyncio.get_running_loop().create_task(self._name_owner_changed(name, old, new))

	async def _name_owner_changed(self, name, old, new):
		if old:
			await self.remove_service(name, old)
		if new:
			await self.add_service(name, new)

	async def add_service(self, name, owner):
		""" Returns a Service object if this is a service we know how
		    to handle. Otherwise None. """
		# If this service was already added by some other means, skip it
		if owner in self._services:
			return None

		try:
			self._services[owner] = service = self._handlers[servicetype(name)](
				self, name, owner)
		except KeyError:
			return None

		# Watch updates on this service only
		await asyncio.gather(
			self.add_match(interface="com.victronenergy.BusItem",
				sender=name,
				path="/",
				type="signal",
				member="ItemsChanged"),
			self.add_match(interface="com.victronenergy.BusItem",
				sender=name,
				type="signal",
				member="PropertiesChanged")
		)

		# Scan service, if it fails, remove the matches and give up
		if not await self.scan_service(service):
			await self._remove_matches(name)
			return None

		try:
			# If this succeeds, someone was waiting for it
			self._servicesByName[name].set_result(service)
		except KeyError:
			self._servicesByName[name] = f = asyncio.Future()
			f.set_result(service)

		await self.serviceAdded(service)
		return service

	async def _remove_matches(self, name):
		# Remove watches. These need to match the calls in add_service
		await asyncio.gather(
			self.remove_match(interface="com.victronenergy.BusItem",
				sender=name,
				path="/",
				type="signal",
				member="ItemsChanged"),
			self.remove_match(interface="com.victronenergy.BusItem",
				sender=name,
				type="signal",
				member="PropertiesChanged")
		)

	async def remove_service(self, name, owner):
		if owner in self._services:
			await self._remove_matches(name)

			service = self._services[owner]
			del self._services[owner]
			del self._servicesByName[name]
			await self.serviceRemoved(service)

	async def dbus_call(self, name, path, member, signature, *params, interface=IFACE):
		reply = await self.bus.call(Message(
			destination=name,
			interface=interface,
			path=path,
			member=member,
			signature=signature,
			body=list(params)))

		if reply.message_type == MessageType.ERROR:
			raise DbusException(reply.body[0])

		return reply.body

	async def scan_service(self, service):
		""" For simplicity, we simply call GetItems. Fallback
		    support for other methods can be added later if someone
		    wants it. """
		try:
			reply = await self.dbus_call(service.name, "/", "GetItems", "")
		except DbusException:
			log.exception("scan_service")
			return False
		else:
			# Only update items that has not been seen yet, via an
			# ItemsChanged/PropertiesChanged message. This avoids
			# overwriting more recent values with an older scan.
			service.update_unseen_items(reply[0])
			return True

	@property
	def services(self):
		return iter(s.result() for s in self._servicesByName.values() if s.done())

	def get_service(self, name):
		try:
			return self._servicesByName[name].result()
		except (KeyError, asyncio.InvalidStateError):
			pass
		return None

	def get_value(self, name, path, default=None):
		try:
			return self._servicesByName[name].result().get_value(path)
		except (KeyError, AttributeError, asyncio.InvalidStateError):
			pass
		return default

	def set_value_async(self, name, path, value):
		""" Similar naming to old velib method for fire and forget setting. """
		try:
			if not path in self._servicesByName[name].result().values:
				return -1
		except (KeyError, asyncio.InvalidStateError):
			return -1 # name not in services

		asyncio.get_running_loop().create_task(
			self.set_value(name, path, value))

	def seen(self, name, path):
		try:
			return self._servicesByName[name].result().seen(path)
		except (KeyError, AttributeError, asyncio.InvalidStateError):
			pass
		return False

	async def set_value(self, name, path, value):
		try:
			reply = await self.dbus_call(name, path, "SetValue", "v",
				make_variant(value))
		except DbusException:
			return -1

		return reply[0]

	async def wait_for_service(self, name):
		""" Returns Service object if already known, otherwise
		    await it. """
		try:
			return await self._servicesByName[name]
		except KeyError:
			self._servicesByName[name] = f = asyncio.Future()
			return await f

if __name__ == "__main__":
	try:
		from dbus_fast.aio import MessageBus
		from dbus_fast.constants import BusType
	except ImportError:
		from dbus_next.aio import MessageBus
		from dbus_next.constants import BusType

	class MyMonitor(Monitor):
		def itemsChanged(self, service, values):
			for p, v in values.items():
				print (f"{service.name}{p} changed to {v}")

	class GridService(Service, ServiceHandler):
		servicetype = "com.victronenergy.grid"
		paths = { "/Int", "/Double", "/Text" }

	class SettingsService(Service, ServiceHandler):
		servicetype = "com.victronenergy.settings"
		paths = { "/Settings/Vrmlogger/LogInterval" }

	async def main():
		bus = await MessageBus(bus_type=BusType.SESSION).connect()
		monitor = await MyMonitor.create(bus)

		await asyncio.sleep(2)
		monitor.set_value_async("com.victronenergy.grid.example", "/Double", 55.1)
		monitor.set_value_async("com.victronenergy.grid.example", "/Int", 44)
		monitor.set_value_async("com.victronenergy.grid.example", "/Text", "Modified by me!")

		print ("/Nothing seen:", monitor.seen("com.victronenergy.grid.example", "/Nothing"))
		print ("/Double seen:", monitor.seen("com.victronenergy.grid.example", "/Double"))

		await bus.wait_for_disconnect()

	asyncio.run(main())
