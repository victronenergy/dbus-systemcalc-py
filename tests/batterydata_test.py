# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

# tested classes
from delegates import BatteryData

class MockBatteryConfiguration(object):
	def __init__(self, service, name, enabled):
		self.service = str(service)
		self.name = None if name is None else str(name)
		self.enabled = bool(enabled)

def mock_load_configured_batteries(instance, configs, *args):
	instance.configured_batteries = {y.service: y for y in (MockBatteryConfiguration(x["service"],
		x.get("name", None), x.get("enabled", False)) for x in configs)}
	instance.confcount = len(configs)

class TestHubSystem(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device('com.victronenergy.battery.ttyO1',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 25,
				'/Soc': 15.3,
				'/DeviceInstance': 0})
		self._add_device('com.victronenergy.battery.ttyO2',
			product_name='battery',
			values={
				'/Dc/0/Voltage': 12.15,
				'/Dc/0/Current': 5.3,
				'/Dc/0/Power': 65,
				'/Dc/0/Temperature': 25,
				'/Soc': 15.3,
				'/DeviceInstance': 1,
				'/CustomName': 'Sled battery'})


	def test_batteries_path(self):
		mock_load_configured_batteries(BatteryData.instance, [
			{"name": None, "service": "com.victronenergy.battery/0", "enabled": True},
			{"name": None, "service": "com.victronenergy.battery/1", "enabled": True},
		])

		self._update_values(5000)
		data = self._service._dbusobjects['/Batteries']
		self.assertTrue(len(data) == 2)

		# Check battery service selection
		di = {b['instance']: b['active_battery_service'] for b in data}
		self.assertEqual(di, {0: True, 1: False})

		# Check customname
		di = {b['instance']: b['name'] for b in data}
		self.assertEqual(di, {0: "battery", 1: "Sled battery"})

		for b in data:
			for f in ("id", "voltage", "current", "name"):
				assert f in b

	def test_battery_naming(self):
		# Alternate name is used over ProductName|CustomName if available
		mock_load_configured_batteries(BatteryData.instance, [
			{"name": "Thruster Bank", "service": "com.victronenergy.battery/1", "enabled": True},
		])

		self._update_values(5000)
		data = self._service._dbusobjects['/Batteries']

		# Check that name matches config
		di = {b['instance']: b['name'] for b in data}
		self.assertEqual(di[1], "Thruster Bank")

	def test_main_battery_always_listed(self):
		# Main battery is always shown, even with no config
		mock_load_configured_batteries(BatteryData.instance, [])

		self._update_values(5000)
		data = self._service._dbusobjects['/Batteries']
		self.assertTrue(len(data) == 1)
		self.assertEqual(data[0]['name'], "battery")
