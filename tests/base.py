import unittest
import dbus_systemcalc
import mock_gobject
from mock_dbus_monitor import MockDbusMonitor
from mock_dbus_service import MockDbusService
from mock_settings_device import MockSettingsDevice


class MockSystemCalc(dbus_systemcalc.SystemCalc):
	def _create_dbus_monitor(self, *args, **kwargs):
		return MockDbusMonitor(*args, **kwargs)

	def _create_settings(self, *args, **kwargs):
		return MockSettingsDevice(*args, **kwargs)

	def _create_dbus_service(self):
		return MockDbusService('com.victronenergy.system')


class TestSystemCalcBase(unittest.TestCase):
	def __init__(self, methodName='runTest'):
		unittest.TestCase.__init__(self, methodName)

	def setUp(self):
		mock_gobject.timer_manager.reset()
		self._system_calc = MockSystemCalc()
		self._monitor = self._system_calc._dbusmonitor
		self._service = self._system_calc._dbusservice

	def _update_values(self, interval=1000):
		mock_gobject.timer_manager.run(interval)

	def _add_device(self, service, values, connected=True, product_name='dummy', connection='dummy'):
		values['/Connected'] = 1 if connected else 0
		values['/ProductName'] = product_name
		values['/Mgmt/Connection'] = connection
		values.setdefault('/DeviceInstance', 0)
		self._monitor.add_service(service, values)

	def _remove_device(self, service):
		self._monitor.remove_service(service)

	def _set_setting(self, path, value):
		self._system_calc._settings[self._system_calc._settings.get_short_name(path)] = value

	def _check_settings(self, values):
		settings = {k: v[1] for k, v in self._system_calc._settings._settings.iteritems()}
		msg = ('{}\t{}\t{}'.format(k, v, settings.get(k)) \
			for k, v in values.iteritems())
		msg = '\n'.join(msg)

		tests = (settings.get(k) == v for k, v in values.iteritems())
		self.assertTrue(all(tests), '\n'+msg)

	def _check_values(self, values):
		ok = True
		for k, v in values.items():
			v2 = self._service[k] if k in self._service else None
			if isinstance(v, (int, float)) and v2 is not None:
				d = abs(v - v2)
				if d > 1e-6:
					ok = False
					break
			else:
				if v != v2:
					ok = False
					break
		if ok:
			return
		msg = ''
		for k, v in values.items():
			msg += '{0}:\t{1}'.format(k, v)
			if k in self._service:
				msg += '\t{}'.format(self._service[k])
			msg += '\n'
		self.assertTrue(ok, msg)

	def _check_external_values(self, values):
		"""Checks a list of values from external (ie. not com.victronenergy.system) services.
		Example for values:
		['com.victronenergy.vebus.ttyO1', { '/State': 3, '/Mode': 7 },
		'com.victronenergy.hub4', { '/MaxChargePower' : 342 }]
		"""
		ok = True
		for service, objects in values.items():
			for path, value in objects.items():
				v = self._monitor.get_value(service, path)
				if isinstance(value, (int, float)) and v is not None:
					d = abs(value - v)
					if d > 1e-6:
						ok = False
						break
				else:
					if v != value:
						ok = False
						break
		if ok:
			return
		msg = ''
		for service, objects in values.items():
			for path, value in objects.items():
				msg += '{0}:\t{1}'.format(path, value)
				v = self._monitor.get_value(service, path)
				msg += '\t{}'.format(v)
				msg += '\n'
		self.assertTrue(ok, msg)
