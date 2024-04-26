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

class TestLoadShedding(TestSystemCalcBase):
	vebus = 'com.victronenergy.vebus.ttyO1'
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)
		self._add_device(self.vebus, product_name='Multi',
			values={
				'/Ac/Control/IgnoreAcIn1': 0,
				'/Ac/State/AcIn1Available': 1,
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
			'/LoadShedding/Active': 1}) # Preparing
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
			'/LoadShedding/Active': 1}) # Still preparing
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
			'/LoadShedding/Active': 2}) # Shedding
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 1, # Disconnect from grid
			}
		})

		timer_manager.run(20000)
		self._check_values({
			'/LoadShedding/Active': 5}) # Recovery
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
			'/LoadShedding/Active': 1}) # Preparing
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
			'/LoadShedding/Active': 1}) # Still preparing
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
			'/LoadShedding/Active': 1}) # Still preparing
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
			'/LoadShedding/Active': 1}) # Still preparing
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': 200, # 80% of PV
			}
		})

		self._monitor.set_value(self.vebus, '/Soc', 48.0)
		timer_manager.run(10000)
		self._check_values({
			'/LoadShedding/Active': 1}) # Still preparing
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
			'/LoadShedding/Active': 1}) # preparing

	def test_full_loadshedding_cycle(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())
		self._set_setting('/Settings/LoadSheddingApi/Mode', 1)
		self._set_setting('/Settings/LoadSheddingApi/MinSoc', 50.0)
		self._set_setting('/Settings/LoadSheddingApi/PreparationTime', 300)
		self._set_setting('/Settings/LoadSheddingApi/DisconnectMargin', 60)
		self._set_setting('/Settings/LoadSheddingApi/ReconnectMargin', 120)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Start', stamp+120)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Duration', 240)

		timer_manager.run(30000) # 90 seconds to shedding
		self._check_values({
			'/LoadShedding/Active': 1}) # preparing

		timer_manager.run(60000) # 60 seconds to shedding, pre-emptive disconnect
		self._check_values({
			'/LoadShedding/Active': 2}) # preparing

		timer_manager.run(90000) # 30 seconds in, power still on
		self._check_values({
			'/LoadShedding/Active': 2}) # preparing

		# power fails, 60 seconds in
		self._monitor.set_value(self.vebus, '/Ac/State/AcIn1Available', 0)
		timer_manager.run(30000)
		self._check_values({
			'/LoadShedding/Active': 3}) # Shedding

		# Power returns, 120 seconds in
		self._monitor.set_value(self.vebus, '/Ac/State/AcIn1Available', 1)
		timer_manager.run(60000)
		self._check_values({
			'/LoadShedding/Active': 4}) # reconnect delay

		# 180 seconds in, reconnect delay expires
		timer_manager.run(60000)
		self._check_values({
			'/LoadShedding/Active': 5}) # recovery

		# 240 seconds in, schedule is over
		timer_manager.run(60000)
		self._check_values({
			'/LoadShedding/Active': 0}) # recovery

	def test_always_reconnect_at_schedule_end_1(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())
		self._set_setting('/Settings/LoadSheddingApi/Mode', 1)
		self._set_setting('/Settings/LoadSheddingApi/MinSoc', 50.0)
		self._set_setting('/Settings/LoadSheddingApi/PreparationTime', 0)
		self._set_setting('/Settings/LoadSheddingApi/DisconnectMargin', 60)
		self._set_setting('/Settings/LoadSheddingApi/ReconnectMargin', 60)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Start', stamp+60)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Duration', 60)

		timer_manager.run(60000) # 60 seconds to shedding, pre-emptive disconnect
		self._check_values({
			'/LoadShedding/Active': 2}) # pre-emptive disconnect
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 1,
			}
		})

		timer_manager.run(60000) # 60 seconds to shedding, pre-emptive disconnect
		self._check_values({
			'/LoadShedding/Active': 0}) # pre-emptive disconnect
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 0,
			}
		})

	def test_always_reconnect_at_schedule_end_2(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())
		self._set_setting('/Settings/LoadSheddingApi/Mode', 1)
		self._set_setting('/Settings/LoadSheddingApi/MinSoc', 50.0)
		self._set_setting('/Settings/LoadSheddingApi/PreparationTime', 0)
		self._set_setting('/Settings/LoadSheddingApi/DisconnectMargin', 0)
		self._set_setting('/Settings/LoadSheddingApi/ReconnectMargin', 60)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Start', stamp+60)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Duration', 60)

		timer_manager.run(60000)
		self._check_values({
			'/LoadShedding/Active': 2})
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 1,
			}
		})

		self._monitor.set_value(self.vebus, '/Ac/State/AcIn1Available', 0)
		timer_manager.run(30000)
		self._check_values({
			'/LoadShedding/Active': 3})

		timer_manager.run(30000)
		self._check_values({
			'/LoadShedding/Active': 0})
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 0,
			}
		})

	def test_always_reconnect_at_schedule_end_3(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())
		self._set_setting('/Settings/LoadSheddingApi/Mode', 1)
		self._set_setting('/Settings/LoadSheddingApi/MinSoc', 50.0)
		self._set_setting('/Settings/LoadSheddingApi/PreparationTime', 0)
		self._set_setting('/Settings/LoadSheddingApi/DisconnectMargin', 0)
		self._set_setting('/Settings/LoadSheddingApi/ReconnectMargin', 60)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Start', stamp+30)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Duration', 90)

		timer_manager.run(30000)
		self._check_values({
			'/LoadShedding/Active': 2})
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 1,
			}
		})

		self._monitor.set_value(self.vebus, '/Ac/State/AcIn1Available', 0)
		timer_manager.run(30000)
		self._check_values({
			'/LoadShedding/Active': 3})

		self._monitor.set_value(self.vebus, '/Ac/State/AcIn1Available', 1)
		timer_manager.run(30000)
		self._check_values({
			'/LoadShedding/Active': 4})

		timer_manager.run(30000)
		self._check_values({
			'/LoadShedding/Active': 0})
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 0,
			}
		})

	def test_no_support_for_grid_availability(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())
		self._set_setting('/Settings/LoadSheddingApi/Mode', 1)
		self._set_setting('/Settings/LoadSheddingApi/MinSoc', 50.0)
		self._set_setting('/Settings/LoadSheddingApi/PreparationTime', 0)
		self._set_setting('/Settings/LoadSheddingApi/DisconnectMargin', 0)
		self._set_setting('/Settings/LoadSheddingApi/ReconnectMargin', 60)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Start', stamp+30)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Duration', 90)

		# No firmware support
		self._monitor.set_value(self.vebus, '/Ac/State/AcIn1Available', None)

		timer_manager.run(35000)
		self._check_values({
			'/LoadShedding/Active': 3})
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 1,
			}
		})

		timer_manager.run(30000)
		self._check_values({
			'/LoadShedding/Active': 3}) # ReconnectMargin still in play
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 1,
			}
		})

		timer_manager.run(30000) # ReconnectMargin as passed
		self._check_values({
			'/LoadShedding/Active': 5}) # Ready to connect
		self._check_external_values({
			self.vebus: {
				'/Ac/Control/IgnoreAcIn1': 0,
			}
		})

	def test_multi_rs(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())
		self._set_setting('/Settings/LoadSheddingApi/Mode', 1)
		self._set_setting('/Settings/LoadSheddingApi/MinSoc', 50.0)
		self._set_setting('/Settings/LoadSheddingApi/PreparationTime', 0)
		self._set_setting('/Settings/LoadSheddingApi/DisconnectMargin', 0)
		self._set_setting('/Settings/LoadSheddingApi/ReconnectMargin', 60)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Start', stamp+10)
		self._set_setting('/Settings/LoadSheddingApi/Schedule/0/Duration', 90)

		self._remove_device(self.vebus)

		self._add_device('com.victronenergy.multi.socketcan_can0_001', {
			'/Ac/In/1/Type': 1,
			'/Mode': 3,

		}, connection='VE.Can')

		timer_manager.run(30000)
		self._check_values({
			'/LoadShedding/Active': 3})
		self._check_external_values({
			'com.victronenergy.multi.socketcan_can0_001': {
				'/Mode': 2, # Inverter only
			}
		})

		timer_manager.run(40000)
		self._check_values({
			'/LoadShedding/Active': 5})
		self._check_external_values({
			'com.victronenergy.multi.socketcan_can0_001': {
				'/Mode': 3, # On
			}
		})
