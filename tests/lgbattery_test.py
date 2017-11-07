# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches


class LgCircuitBreakerDetectTest(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device('com.victronenergy.vebus.ttyO1', product_name='Multi',
		values={
			'/Ac/ActiveIn/L1/P': 123,
			'/Ac/ActiveIn/ActiveInput': 0,
			'/Ac/ActiveIn/Connected': 1,
			'/Ac/Out/L1/P': 100,
			'/Dc/0/Voltage': 12.25,
			'/Dc/0/Current': 8,
			'/DeviceInstance': 0,
			'/Hub4/AssistantId': 5,
			'/Hub4/Sustain': 0,
			'/Dc/0/MaxChargeCurrent': None,
			'/Soc': 53.2,
			'/State': 3,  # Bulk
			'/VebusMainState': 9})

	def test_lg_circuit_breaker_detect(self):
		self._update_values()
		self._check_values({'/Dc/Battery/Alarms/CircuitBreakerTripped': None})
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Mode', 3)
		self._add_device('com.victronenergy.battery.ttyO2',
						product_name='battery',
						values={
								'/Dc/0/Voltage': 12.3,
								'/Dc/0/Current': 5.3,
								'/Dc/0/Power': 65,
								'/Soc': 15.3,
								'/DeviceInstance': 2,
								'/ProductId': 0xb004})
		self._update_values()
		self._check_values({'/Dc/Battery/Alarms/CircuitBreakerTripped': 0})
		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Dc/0/Current', 0)
		for voltage in [53, 53, 53, 53, 54.2, 54.1, 54.2, 53.9, 53.95, 54.0, 54.3,
			41.7, 43.5, 41.8, 42.5, 42.0, 42.3, 41.9, 42.3]:
			self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Dc/0/Voltage', voltage)
			self._update_values()
			self._check_values({'/Dc/Battery/Alarms/CircuitBreakerTripped': 0})
		self._monitor.set_value('com.victronenergy.battery.ttyO2', '/Dc/0/Voltage', 41.8)
		self._update_values()
		self._check_values({'/Dc/Battery/Alarms/CircuitBreakerTripped': 2})
		self._remove_device('com.victronenergy.battery.ttyO2')
		self._check_values({'/Dc/Battery/Alarms/CircuitBreakerTripped': None})
