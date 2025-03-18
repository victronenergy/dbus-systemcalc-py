from datetime import datetime, date, time, timedelta

# This adapts sys.path to include all relevant packages
import context

# Testing tools
from mock_gobject import timer_manager

# our own packages
import dbus_systemcalc
from delegates import DynamicEss
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches
import logging

# Time travel patch
DynamicEss._get_time = lambda *a: timer_manager.datetime

class TestDynamicEss(TestSystemCalcBase):
	vebus = 'com.victronenergy.vebus.ttyO1'
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		logging.getLogger().setLevel(logging.DEBUG)
		TestSystemCalcBase.setUp(self)
		self._add_device(self.vebus, product_name='Multi',
			values={
				'/Devices/0/Assistants': [0x55, 0x1] + (26 * [0]),
				'/Hub4/AssistantId': 5,
				'/VebusMainState': 9,
				'/Ac/ActiveIn/ActiveInput': 0,
				"/Ac/Out/L1/P": 0,
				"/Ac/Out/L2/P": 0,
				"/Ac/Out/L3/P": 0,
				'/Ac/NumberOfAcInputs': 1,
				'/State': 3,
				'/Soc': 55.0,
				'/ExtraBatteryCurrent': 0})

		self._add_device('com.victronenergy.grid.ttyUSB0', {
			'/Ac/L1/Power': 0,
			'/Ac/L2/Power': 0,
			'/Ac/L3/Power': 0,
			'/Connected': 1,
			'/DeviceInstance': 30,
		})

		self._add_device('com.victronenergy.hub4',
			values={
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower': -1,
				'/Overrides/Setpoint': None,
				'/Overrides/FeedInExcess': 0
		})

		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/CGwacs/MaxFeedInPower': -1,
				'/Settings/CGwacs/PreventFeedback': 0,
				'/Settings/SystemSetup/AcInput1': 1,
			})
		
		self._set_setting('/Settings/DynamicEss/BatteryCapacity', 10.0)
		self._set_setting('/Settings/DynamicEss/SystemEfficiency', 90.0)

		self._update_values()

	def tearDown(self):
		DynamicEss.instance.release_control()

	def test_1_SCHEDULED_SELFCONSUME(self):
		
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 1) # Self consume
	
		timer_manager.run(7000)

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 1,
			'/DynamicEss/ReactiveStrategy': 1,
			'/DynamicEss/LastScheduledStart': stamp
		})

		self.validate_self_consume
	
	def test_2_SCHEDULED_CHARGE_ALLOW_GRID(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		#Set a 10 kWh battery, so charging 1% soc should equal 100 Watt chargerate. (times 1.1 cause ac/dc)
		self._set_setting('/Settings/DynamicEss/BatteryCapacity', 10.0)
		self._monitor.set_value(self.vebus, '/Soc', 50.0)
		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 60)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 1)
	
		timer_manager.run(5000)

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 1,
			'/DynamicEss/ReactiveStrategy': 2,
			'/DynamicEss/LastScheduledStart': stamp,
		})

		#	(percent * capacity * 36000) / duration	
		expected_rate = round(1.1 * (10 * 10 * 36000) / 3600)
		
		#assert equality based on /100, to eliminate seconds the delegate needs to calculate. 
		self.assertAlmostEqual(expected_rate/100.0, self._service["/DynamicEss/ChargeRate"]/100.0, 1)
		
		self.validate_charge_state(expected_rate)

	def test_6_SCHEDULED_DISCHARGE(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		#Set a 10 kWh battery, so charging 1% soc should equal 100 Watt chargerate. (times 1.1 cause ac/dc)
		self._set_setting('/Settings/DynamicEss/BatteryCapacity', 10.0)
		self._monitor.set_value(self.vebus, '/Soc', 50.0)
		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 40)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 1)
	
		timer_manager.run(5000)

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 1,
			'/DynamicEss/ReactiveStrategy': 6,
			'/DynamicEss/LastScheduledStart': stamp,
		})

		#	(percent * capacity * 36000) / duration	
		expected_rate = round(1.1 * (10 * 10 * 36000) / 3600) * -1
		
		#assert equality based on /100, to eliminate seconds the delegate needs to calculate. 
		self.assertAlmostEqual(expected_rate/100.0, self._service["/DynamicEss/ChargeRate"]/100.0, 1)
		
		self.validate_discharge_state(expected_rate)



	def test_9_IDLE_MAINTAIN_TARGETSOC(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		#Set a 10 kWh battery, so charging 1% soc should equal 100 Watt chargerate. (times 1.1 cause ac/dc)
		self._set_setting('/Settings/DynamicEss/BatteryCapacity', 10.0)
		self._monitor.set_value(self.vebus, '/Soc', 50.0)
		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 50)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 1)
	
		timer_manager.run(5000)

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 1,
			'/DynamicEss/ReactiveStrategy': 9,
			'/DynamicEss/LastScheduledStart': stamp,
		})

		self.validate_idle_state()



	def test_12_SCHEDULED_CHARGE_NO_GRID(self):
		#this strategy is currently not used. Replaced with 14 - SELFCONSUME_NO_GRID
		pass  # to implement

	def test_13_SCHEDULED_MINIMUM_DISCHARGE(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		#Set a 10 kWh battery, so charging 1% soc should equal 100 Watt chargerate. (times 1.1 cause ac/dc)
		self._set_setting('/Settings/DynamicEss/BatteryCapacity', 10.0)
		self._monitor.set_value(self.vebus, '/Soc', 50.0)
		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 3) #ProGrid should trigger #13
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 40)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 1)
	
		timer_manager.run(5000)

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 1,
			'/DynamicEss/ReactiveStrategy': 13,
			'/DynamicEss/LastScheduledStart': stamp,
		})

		#	(percent * capacity * 36000) / duration	
		expected_rate = round(1.1 * (10 * 10 * 36000) / 3600) * -1
		
		#assert equality based on /100, to eliminate seconds the delegate needs to calculate. 
		self.assertAlmostEqual(expected_rate/100.0, self._service["/DynamicEss/ChargeRate"]/100.0, 1)
		self.validate_discharge_state(expected_rate)

	def test_14_SELFCONSUME_NO_GRID(self):
		# keep battery charged should be entered, when we have 100 soc = 100 tsoc and
		# 250 Watt solar plus.
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 3) 
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 90) 
		self._set_setting('/Settings/DynamicEss/Schedule/0/Restrictions', 2) 

		self._monitor.set_value(self.vebus, '/Soc', 80)
		
		self._monitor.set_value("com.victronenergy.grid.ttyUSB0", "/Ac/L1/Power", -300)
		self._add_device('com.victronenergy.pvinverter.mock31', {
			'/Ac/L1/Power': 300,
			'/Ac/L2/Power': 0,
			'/Ac/L3/Power': 0,
			'/Position': 0,
			'/Connected': 1,
			'/DeviceInstance': 31,
		})

		self._update_values()
		timer_manager.run(7000)

		self._check_values({
			'/Ac/In/NumberOfAcInputs': 1,
			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'grid',
			'/Ac/In/0/ServiceName': 'com.victronenergy.grid.ttyUSB0',
			'/Ac/In/0/DeviceInstance': 30,
			'/Ac/In/0/Connected': 1,
			'/Ac/ActiveIn/L1/Power': -300,
			'/Ac/ActiveIn/L2/Power': 0,
			'/Ac/ActiveIn/L3/Power': 0,
		})

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 1,
			'/DynamicEss/ReactiveStrategy': 14,
			'/DynamicEss/LastScheduledStart': stamp,
		})

		self.validate_self_consume(300)

	def test_15_IDLE_NO_OPPORTUNITY(self):
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		#Set a 10 kWh battery, so charging 1% soc should equal 100 Watt chargerate. (times 1.1 cause ac/dc)
		self._set_setting('/Settings/DynamicEss/BatteryCapacity', 10.0)
		self._monitor.set_value(self.vebus, '/Soc', 50.0)
		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 3)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Restrictions', 1) #restriction and no solar should enter idle with M2B coping
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 60)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 1)
	
		timer_manager.run(5000)

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 1,
			'/DynamicEss/ReactiveStrategy': 15,
			'/DynamicEss/LastScheduledStart': stamp,
		})

		self.validate_idle_state()



	def test_17_SELFCONSUME_INCREASED_DISCHARGE(self):
		# keep battery charged should be entered, when we have 100 soc = 100 tsoc and
		# 250 Watt solar plus.
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 3) 
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 90) 

		self._monitor.set_value(self.vebus, '/Soc', 100)
		self._monitor.set_value("com.victronenergy.grid.ttyUSB0", "/Ac/L1/Power", 2000)
		self._add_device('com.victronenergy.pvinverter.mock31', {
			'/Ac/L1/Power': 0,
			'/Ac/L2/Power': 0,
			'/Ac/L3/Power': 0,
			'/Position': 0,
			'/Connected': 1,
			'/DeviceInstance': 31,
		})

		self._update_values()
		timer_manager.run(7000)

		self._check_values({
			'/Ac/In/NumberOfAcInputs': 1,
			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'grid',
			'/Ac/In/0/ServiceName': 'com.victronenergy.grid.ttyUSB0',
			'/Ac/In/0/DeviceInstance': 30,
			'/Ac/In/0/Connected': 1,
			'/Ac/ActiveIn/L1/Power': 2000,
			'/Ac/ActiveIn/L2/Power': 0,
			'/Ac/ActiveIn/L3/Power': 0,
			'/Ac/Consumption/L1/Power': 2000,
			'/Ac/Consumption/L2/Power': 0,
			'/Ac/Consumption/L3/Power': 0,
		})

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 1,
			'/DynamicEss/ReactiveStrategy': 17,
			'/DynamicEss/LastScheduledStart': stamp,
		})

		#	(percent * capacity * 36000) / duration	
		expected_rate = round(1.1 * (10 * 10 * 36000) / 3600) * -1
		
		#assert equality based on /100, to eliminate seconds the delegate needs to calculate. 
		self.assertAlmostEqual(expected_rate/100.0, self._service["/DynamicEss/ChargeRate"]/100.0, 1)
		self.validate_self_consume()

	def test_18_KEEP_BATTERY_CHARGED(self):
		# keep battery charged should be entered, when we have 100 soc = 100 tsoc and
		# 250 Watt solar plus.
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 2) 
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 100) 

		self._monitor.set_value(self.vebus, '/Soc', 100)
		self._monitor.set_value("com.victronenergy.grid.ttyUSB0", "/Ac/L1/Power", -200)
		self._monitor.set_value("com.victronenergy.grid.ttyUSB0", "/Ac/L2/Power", -200)
		self._monitor.set_value("com.victronenergy.grid.ttyUSB0", "/Ac/L3/Power", -200)

		self._add_device('com.victronenergy.pvinverter.mock31', {
			'/Ac/L1/Power': 200,
			'/Ac/L2/Power': 200,
			'/Ac/L3/Power': 200,
			'/Position': 0,
			'/Connected': 1,
			'/DeviceInstance': 31,
		})

		self._update_values()
		timer_manager.run(7000)

		self._check_values({
			'/Ac/In/NumberOfAcInputs': 1,

			'/Ac/In/0/Source': 1,
			'/Ac/In/0/ServiceType': 'grid',
			'/Ac/In/0/ServiceName': 'com.victronenergy.grid.ttyUSB0',
			'/Ac/In/0/DeviceInstance': 30,
			'/Ac/In/0/Connected': 1,
			'/Ac/ActiveIn/L1/Power': -200,
			'/Ac/ActiveIn/L2/Power': -200,
			'/Ac/ActiveIn/L3/Power': -200,
			'/Ac/Consumption/L1/Power': 0,
			'/Ac/Consumption/L2/Power': 0,
			'/Ac/Consumption/L3/Power': 0,
		})

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 1,
			'/DynamicEss/ReactiveStrategy': 18,
			'/DynamicEss/LastScheduledStart': stamp,
			'/DynamicEss/ChargeRate': 250,
		})

		#rate in this state should be 250 watt fixed.
		self.validate_charge_state(250)

	def test_92_DESS_DISABLED(self):
		
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 0)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Strategy', 1) # Self consume
	
		timer_manager.run(7000)

		#check internal values
		self._check_values({
			'/DynamicEss/Active': 0,
			'/DynamicEss/ReactiveStrategy': 92
		})

		self.validate_self_consume()

	def test_93_SELFCONSUME_UNEXPECTED_EXCEPTION(self):
		#can't really be tested, because in a ideal delegate, we don't have unexpected exceptions.
		pass

	def test_94_SELFCONSUME_FAULTY_CHARGERATE(self):
		#can't really be tested, because in a ideal delegate, we don't have input-options to provoke a faulty charge rate.
		pass

	def test_95_UNKNOWN_OPERATING_MODE(self):
		#currently, there is a operating mode independent logic, so this is not validated.
		pass

	def test_96_ESS_LOW_SOC(self):
		# mock environment doesn't seem to produce the desired SystemState-Value to validate this.
		pass  # to implement

	def test_97_SELFCONSUME_UNMAPPED_STATE(self):
		# in an ideal delegate, we don't have unmapped states. So, doesn't make sence to add a unmapped state for testing
		pass

	def test_98_SELFCONSUME_UNPREDICTED(self):
		# in an ideal delegate, we don't have unpredicted inputs. So, doesn't make sence to add a unpredicted input-set state for testing
		# cuase by the time we know that input set, it would become predicted and to be implemented.
		pass  

	def test_99_NO_WINDOW(self):
		self._set_setting('/Settings/DynamicEss/Mode', 1)
		timer_manager.run(10000) #give DESS time to pick up settings.

		# Nothing is overriden, slot has not arrived. DESS Should be inactive for now
		self._check_values({
			'/DynamicEss/Active': 0,
			'/DynamicEss/ReactiveStrategy': 99,
		})

		self.validate_self_consume()

		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/FeedInExcess': 0 #for the NO_WINDOW Test, should default to system configuration. 
		}})

	def validate_self_consume(self, maxChargePower=None):
		from delegates import Dvcc

		#validate external values
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1
		}})

		self.assertEqual(maxChargePower, Dvcc.instance.internal_maxchargepower)

	def validate_charge_state(self, rate):
		from delegates import Dvcc

		#validate external values
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 1,
				'/Overrides/Setpoint': None,
				'/Overrides/MaxDischargePower': -1
		}})

		self.assertAlmostEqual(rate/100.0, Dvcc.instance.internal_maxchargepower/100.0,1)
	
	def validate_discharge_state(self, rate):
		from delegates import Dvcc

		#validate external values
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/Setpoint': -32000
		}})

		self.assertAlmostEqual(rate/100.0, self._monitor.get_value('com.victronenergy.hub4','/Overrides/MaxDischargePower')/-100.0,1)
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)
	
	def validate_idle_state(self):
		#validate external values
		self._check_external_values({
			'com.victronenergy.hub4': {
				'/Overrides/ForceCharge': 0,
				'/Overrides/MaxDischargePower':1
		}})
		#TODO check more settings to validate idle state.

	def test_hysteresis(self):
		""" Test case for batteries that don't report whole numbers, but
		    jumps between SOC values and don't always hit match target SOC
		    exactly. Use case jitters between 43.8% and 44.4%. """
		from delegates import Dvcc
		now = timer_manager.datetime
		stamp = int(now.timestamp())

		self._set_setting('/Settings/DynamicEss/Mode', 1)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Start', stamp)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Duration', 3600)
		self._set_setting('/Settings/DynamicEss/Schedule/0/Soc', 44)
		self._set_setting('/Settings/DynamicEss/Schedule/0/AllowGridFeedIn', 1)

		self._monitor.set_value(self.vebus, '/Soc', 44.4)
		timer_manager.run(5000)
		self.assertGreaterEqual(self._monitor.get_value('com.victronenergy.hub4','/Overrides/MaxDischargePower'),  1.0) # Controlled discharge

		self._monitor.set_value(self.vebus, '/Soc', 43.8)
		timer_manager.run(5000)
		# SOC is reached, idle
		self.assertEqual(self._monitor.get_value('com.victronenergy.hub4','/Overrides/MaxDischargePower'), 1.0)
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)

		# Returns to 44.4, remain in idle
		self._monitor.set_value(self.vebus, '/Soc', 44.4)
		timer_manager.run(5000)
		self.assertEqual(self._monitor.get_value('com.victronenergy.hub4','/Overrides/MaxDischargePower') , 1.0)
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)

		# Increases to 45.1%, go back to discharge.
		self._monitor.set_value(self.vebus, '/Soc', 45.1)
		timer_manager.run(5000)
		self.assertGreaterEqual(self._monitor.get_value('com.victronenergy.hub4','/Overrides/MaxDischargePower'), 1.0) # Controlled discharge

		# Idle again
		self._monitor.set_value(self.vebus, '/Soc', 43.8)
		timer_manager.run(5000)
		# SOC is reached, idle
		self.assertEqual(self._monitor.get_value('com.victronenergy.hub4','/Overrides/MaxDischargePower'), 1.0)
		self.assertEqual(None, Dvcc.instance.internal_maxchargepower)

		# Back to charge if we go low enough
		self._monitor.set_value(self.vebus, '/Soc', 42.9)
		timer_manager.run(5000)
		self.assertEqual(self._monitor.get_value('com.victronenergy.hub4','/Overrides/MaxDischargePower'), -1.0)
		self.assertGreaterEqual(Dvcc.instance.internal_maxchargepower, 0.0)
