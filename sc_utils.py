from functools import update_wrapper
from collections import Mapping

VictronServicePrefix = 'com.victronenergy'


def safeadd(*values):
	""" Adds all parameters passed to this function. Parameters which are None
		are ignored. If all parameters are None, the function will return None
		as well.
	"""
	values = [v for v in values if v is not None]
	return sum(values) if values else None


def safemax(v0, v1):
	if v0 is None or v1 is None:
		return None
	return max(v0, v1)


def service_base_name(service_name):
	'''Returns the part of a Victron D-Bus service name that defines it type.
	Example: com.victronenergy.vebus.ttyO1 yields com.victronenergy.vebus'''
	if not service_name.startswith(VictronServicePrefix) or service_name[len(VictronServicePrefix)] != '.':
		raise Exception('Not a victron service')
	i = service_name.find('.', len(VictronServicePrefix) + 1)
	if i == -1:
		return service_name
	return service_name[:i]


def service_instance_name(service_name, instance):
	'''Combines service base name and device instance to a identifier that is unique for each D-Bus
	services without relying on communication port name etc.
	Example: com.victronenergy.grid.cgwacs_ttyUSB0_di30_mb1 yields com.victronenergy.grid/30'''
	return '%s/%s' % (service_base_name(service_name), instance)


def gpio_paths(etc_path):
	try:
		with open(etc_path, 'rt') as r:
			return r.read().strip().split()
	except IOError:
		return []


def copy_dbus_value(monitor, src_service, src_path, dest_service, dest_path, copy_invalid=False, offset=None):
	value = monitor.get_value(src_service, src_path)
	if copy_invalid or value is not None:
		if offset is not None: value += offset
		monitor.set_value(dest_service, dest_path, value)


class SmartDict(dict):
	def __getattr__(self, n):
		try:
			return self[n]
		except IndexError:
			raise AttributeError(n)
	def __setattr__(self, k, v):
		self[k] = v

class reify(object):
	""" Decorator for class methods. Turns the method into a property that
	    is evaluated once, and then replaces the property, effectively caching
		it and evaluating it only once. """
	def __init__(self, wrapped):
		self.wrapped = wrapped
		update_wrapper(self, wrapped)

	def __get__(self, inst, objtype=None):
		if inst is None:
			return self
		val = self.wrapped(inst)
		setattr(inst, self.wrapped.__name__, val)
		return val

class smart_dict(dict):
	# Dictionary that can be accessed via attributes.
	def __getattr__(self, k):
		try:
			v = self[k]
			if isinstance(v, Mapping):
				return self.__class__(v)
			return v
		except KeyError:
			raise AttributeError(k)
	def __setattr__(self, k, v):
		self[k] = v
