import context
import patches
import lgbattery_test as lg
from base import TestSystemCalcBase
from delegates.lgbattery import LgCircuitBreakerDetect

class MissingLgVoltageTest(TestSystemCalcBase):
	def setUp(self):
		lg.LgCircuitBreakerDetectTest.setUp(self)
	def test_missing_battery_voltage_does_not_stop_updates(self):
		self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Mode', 3)
		battery = 'com.victronenergy.battery.test'
		self._add_device(battery, {'/DeviceInstance': 2, '/ProductId': 0xb004,
			'/Dc/0/Voltage': None, '/Dc/0/Current': 0, '/Soc': 50})
		for _ in range(25):
			LgCircuitBreakerDetect.instance.update_values({'/VebusService': 'com.victronenergy.vebus.ttyO1'})
		self._check_values({'/Dc/Battery/Alarms/CircuitBreakerTripped': 0})
		self._monitor.set_value(battery, '/Dc/0/Voltage', 12.25)
		LgCircuitBreakerDetect.instance.update_values({'/VebusService': 'com.victronenergy.vebus.ttyO1'})
		self._check_values({'/Dc/Battery/Alarms/CircuitBreakerTripped': 0})
		self._monitor.set_value(battery, '/Dc/0/Voltage', 48)
		LgCircuitBreakerDetect.instance.update_values({'/VebusService': 'com.victronenergy.vebus.ttyO1'})
		self._check_values({'/Dc/Battery/Alarms/CircuitBreakerTripped': 2})
