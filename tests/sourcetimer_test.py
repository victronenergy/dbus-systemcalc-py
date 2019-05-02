# This adapts sys.path to include all relevant packages
import context

# Testing tools
from mock_gobject import timer_manager

# our own packages
from base import TestSystemCalcBase
from delegates import SourceTimers

# Monkey patching for unit tests
import patches

# Time travel patch
SourceTimers._get_time = lambda *a: (timer_manager.time / 1000)

class TestTimers(TestSystemCalcBase):
	vebus = 'com.victronenergy.vebus.ttyO1'
	settings = 'com.victronenergy.settings'

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device(self.vebus, product_name='Multi',
			values={
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/ActiveIn/L1/P': 123,
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/ActiveIn/Connected': 1,
				'/Ac/Out/L1/P': 100,
				'/Dc/0/Voltage': 12.25,
				'/Dc/0/Current': 8,
				'/DeviceInstance': 0,
				'/Hub4/AssistantId': 5,
				'/Hub4/Sustain': 0,
				'/Soc': 53.2,
				'/State': 3, # Bulk
				'/VebusMainState': 9, # Charging
				'/Mode': 3, # On
		})
		self._add_device(self.settings,
			values={
				'/Settings/SystemSetup/AcInput1': 1, # Grid
				'/Settings/SystemSetup/AcInput2': 2, # Generator
			})

	def test_timers(self):
		# self._monitor.set_value('com.victronenergy.settings', '/Settings/SystemSetup/AcInput1', 0)
		# Start at zero
		self._check_values({
			'/Timers/TimeOnGrid': 0,
			'/Timers/TimeOnGenerator': 0,
			'/Timers/TimeOnInverter': 0,
			'/Timers/TimeOff': 0,
		})

		# Let 20 seconds pass
		self._update_values(20000)
		self._check_values({
			'/Timers/TimeOnGrid': 20,
			'/Timers/TimeOnGenerator': 0,
			'/Timers/TimeOnInverter': 0,
			'/Timers/TimeOff': 0,
		})


		# Generator
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 1)
		self._update_values(10000)
		self._check_values({
			'/Timers/TimeOnGrid': 20,
			'/Timers/TimeOnGenerator': 10,
			'/Timers/TimeOnInverter': 0,
			'/Timers/TimeOff': 0,
		})

		# Inverter power
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', 0xF0)
		self._update_values(10000)
		self._check_values({
			'/Timers/TimeOnGrid': 20,
			'/Timers/TimeOnGenerator': 10,
			'/Timers/TimeOnInverter': 10,
			'/Timers/TimeOff': 0,
		})

		# Off
		self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', 0)
		self._update_values(10000)
		self._check_values({
			'/Timers/TimeOnGrid': 20,
			'/Timers/TimeOnGenerator': 10,
			'/Timers/TimeOnInverter': 10,
			'/Timers/TimeOff': 10,
		})
