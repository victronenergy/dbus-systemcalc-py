import json

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

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
		self._update_values(5000)
		data = json.loads(self._service._dbusobjects['/Batteries'])
		self.assertTrue(len(data) == 2)

		# Check battery service selection
		di = {b['instance']: b['active_battery_service'] for b in data}
		self.assertEqual(di, {0: True, 1: False})

		# Check customname
		di = {b['instance']: b['name'] for b in data}
		self.assertEqual(di, {0: "battery", 1: "Sled battery"})

		for b in data:
			for f in ("id", "voltage", "current"):
				assert f in b
