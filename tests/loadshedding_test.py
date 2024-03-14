import unittest
from datetime import datetime, date, time, timedelta

# This adapts sys.path to include all relevant packages
import context

# Testing tools
from mock_gobject import timer_manager

# our own packages
from delegates import LoadShedding
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

# Time travel patch
LoadShedding._get_time = lambda *a: timer_manager.datetime

@unittest.skip("Skip load-shedding tests for now")
class TestLoadShedding(TestSystemCalcBase):
	vebus = 'com.victronenergy.vebus.ttyO1'
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device(self.vebus, product_name='Multi',
			values={
				'/Ac/Control/IgnoreAcIn1': 0,
				'/Devices/0/Assistants': [0x55, 0x1] + (26 * [0]),
				'/Hub4/AssistantId': 5,
				'/VebusMainState': 9,
				'/State': 3,
				'/Soc': 40.0,
				'/ExtraBatteryCurrent': 5.0,
				'/Ac/NumberOfAcInputs': 1})

		self._add_device('com.victronenergy.solarcharger.ttyO2', {
			'/State': 252,
			'/Link/NetworkMode': 5,
			'/Link/ChargeVoltage': 55.0,
			'/Dc/0/Voltage': 50.0,
			'/Dc/0/Current': 5,
			'/FirmwareVersion': 0x129}, connection='VE.Direct')

		self._add_device('com.victronenergy.hub4',
			values={
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': None,
		})

		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/SystemSetup/AcInput1': 1,
				'/Settings/SystemSetup/AcInput2': 0,
			})

		self._update_values()

	def tearDown(self):
		LoadShedding.instance.release_control()

	def test_outage_with_preparation_charge(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		# Setup, load-shedding starts in 3 minutes, with 1 min prep time and
		# 1 minute margin, so:
		# 0 seconds:   Nothing happens
		# 10 seconds:  Prepartion Charging starts
		# 30 seconds: Disconnect happens one minute early
		# 40 seconds: Nothing happens
		# 50 seconds: Reconnect is initiated (because ReconnectMargin = 10)
		# 60 seconds: actual end of slot

		self._set_setting('/Settings/LoadSheddingApi/Mode', 1)
		self._set_setting('/Settings/LoadSheddingApi/MinSoc', 50.0)
		self._set_setting('/Settings/LoadSheddingApi/PreparationTime', 20)
		self._set_setting('/Settings/LoadSheddingApi/DisconnectMargin', 10)
		self._set_setting('/Settings/LoadSheddingApi/ReconnectMargin', 10)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Start', stamp+40)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Duration', 20)

		timer_manager.run(5000)
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
			},
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 0,
			}
		})

		timer_manager.run(5000)
		self._check_values({
			'/LoadShedding/Active': 2}) # Preparing
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
				'/Overrides/MaxDischargePower': -1,
			},
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 0,
			}
		})

		# SOC reaches target
		self._monitor.set_value(self.vebus, '/Soc', 52.0)
		timer_manager.run(5000)
		self._check_values({
			'/LoadShedding/Active': 2}) # Still preparing
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': 225, # 90% of PV
			},
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 0,
			}
		})

		timer_manager.run(15000)
		self._check_values({
			'/LoadShedding/Active': 1}) # Shedding
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 1, # Disconnect from grid
			}
		})

		timer_manager.run(20000)
		self._check_values({
			'/LoadShedding/Active': 0}) # No longer shedding
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 0, # Reconnect to grid
			}
		})

	def test_preparation_hysteresis(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/LoadSheddingApi/Mode', 1)
		self._set_setting('/Settings/LoadSheddingApi/MinSoc', 50.0)
		self._set_setting('/Settings/LoadSheddingApi/PreparationTime', 60)
		self._set_setting('/Settings/LoadSheddingApi/DisconnectMargin', 0)
		self._set_setting('/Settings/LoadSheddingApi/ReconnectMargin', 0)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Start', stamp+70)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Duration', 60)

		timer_manager.run(10000)
		self._check_values({
			'/LoadShedding/Active': 2}) # Preparing
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
				'/Overrides/MaxDischargePower': -1,
			},
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 0,
			}
		})

		# SOC close to target
		self._monitor.set_value(self.vebus, '/Soc', 49.0)
		timer_manager.run(10000)
		self._check_values({
			'/LoadShedding/Active': 2}) # Still preparing
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
				'/Overrides/MaxDischargePower': -1,
			}
		})

		# SOC reaches target
		self._monitor.set_value(self.vebus, '/Soc', 50.0)
		timer_manager.run(10000)
		self._check_values({
			'/LoadShedding/Active': 2}) # Still preparing
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': 200, # 80% of PV
			}
		})

		# SOC sinks back
		self._monitor.set_value(self.vebus, '/Soc', 49.0)
		timer_manager.run(10000)
		self._check_values({
			'/LoadShedding/Active': 2}) # Still preparing
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': 200, # 80% of PV
			}
		})

		self._monitor.set_value(self.vebus, '/Soc', 48.0)
		timer_manager.run(10000)
		self._check_values({
			'/LoadShedding/Active': 2}) # Still preparing
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1, # Charge again
				'/Overrides/MaxDischargePower': -1,
			}
		})

	def test_really_long_preparation_time(self):
		# If the preparetime is much longer than the duration of the window,
		# make sure it doesn't drop out of preparation
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/LoadSheddingApi/Mode', 1)
		self._set_setting('/Settings/LoadSheddingApi/MinSoc', 50.0)
		self._set_setting('/Settings/LoadSheddingApi/PreparationTime', 300)
		self._set_setting('/Settings/LoadSheddingApi/DisconnectMargin', 0)
		self._set_setting('/Settings/LoadSheddingApi/ReconnectMargin', 0)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Start', stamp+120)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Duration', 60)

		timer_manager.run(90000) # 30 seconds away from shedding
		self._check_values({
			'/LoadShedding/Active': 2}) # preparing
