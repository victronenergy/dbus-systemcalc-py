
#!/usr/bin/env python

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

class TestMultipleVebusDevices(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def setUp(self):
		TestSystemCalcBase.setUp(self)

		self._add_device('com.victronenergy.settings',
			values={
				'/Settings/SystemSetup/AcInput1': 1,
				'/Settings/SystemSetup/AcInput2': 2,
			})

        def test_second_disconnected_vebus_system_is_ignored(self):
                self._add_device('com.victronenergy.vebus.ttyO1',
                        product_name='Multi',
			connected=True,
                        values={
                                '/Ac/ActiveIn/L1/P': 123,
                                '/Ac/ActiveIn/ActiveInput': 0,
                                '/Ac/ActiveIn/Connected': 1,
                                '/Ac/Out/L1/P': 100,
                                '/Dc/0/Voltage': 12.25,
                                '/Dc/0/Current': -8,
                                '/DeviceInstance': 0,
                                '/Dc/0/MaxChargeCurrent': None,
                                '/Soc': 53.2,
                                '/State': 3,
                        })

                self._update_values()
                self._check_values({
                        '/Ac/Grid/L1/Power': 123,
                        '/Ac/Consumption/L1/Power': 100
                })

                self._add_device('com.victronenergy.vebus.ttyO2',
                        product_name='Multi2',
                        connected=False,
                        values={
                                '/Ac/ActiveIn/L1/P': None,
                                '/Ac/ActiveIn/ActiveInput': None,
                                '/Ac/Out/L1/P': None,
                                '/Dc/0/Voltage': None,
                                '/Dc/0/Current': None,
                                '/DeviceInstance': None,
                                '/Soc': None,
                                '/State': 3
                        })

                self._update_values()
                self._check_values({
                        '/Ac/Grid/L1/Power': 123,
                        '/Ac/Consumption/L1/Power': 100
                })


        def test_system_auto_switches_to_second_vebus_system_after_disconnecting_the_first(self):
                self._add_device('com.victronenergy.vebus.ttyO1',
                        product_name='Multi',
                        values={
                                '/Ac/ActiveIn/L1/P': 123,
                                '/Ac/ActiveIn/ActiveInput': 0,
                                '/Ac/ActiveIn/Connected': 1,
                                '/Ac/Out/L1/P': 100,
                                '/Dc/0/Voltage': 12.25,
                                '/Dc/0/Current': -8,
                                '/DeviceInstance': 0,
                                '/Dc/0/MaxChargeCurrent': None,
                                '/Soc': 53.2,
                                '/State': 3,
                        })

                self._add_device('com.victronenergy.vebus.ttyO2',
                        product_name='Multi2',
                        connected=True,
                        values={
                                '/Ac/ActiveIn/L1/P': 127,
                                '/Ac/ActiveIn/ActiveInput': 0,
                                '/Ac/Out/L1/P': 87,
                                '/Dc/0/Voltage': 12.25,
                                '/Dc/0/Current': -8,
                                '/DeviceInstance': 1,
                                '/Soc': 53.2,
                                '/State': 3
                        })
                self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Connected', 0)
                self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L1/P', None)
                self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L2/P', None)
                self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/L3/P', None)
                self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L1/P', None)
                self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L2/P', None)
                self._monitor.add_value('com.victronenergy.vebus.ttyO1', '/Ac/Out/L3/P', None)
                self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Ac/ActiveIn/ActiveInput', None)
                self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Voltage', None)
                self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Dc/0/Current', None)
                self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/Soc', None)
                self._monitor.set_value('com.victronenergy.vebus.ttyO1', '/State', None)

                self._update_values()
                self._check_values({
                        '/Ac/Grid/L1/Power': 127,
                        '/Ac/Consumption/L1/Power': 87
                })

	def test_sort_order(self):
		self._update_values()
		self._check_values({
			'/Dc/Battery/Soc': None,
			'/ActiveBatteryService': None,
			'/AutoSelectedBatteryService': 'No battery monitor found'})

		self._add_device('com.victronenergy.vebus.ttyUSB1',
			product_name='MultiUSB',
			values={
				'/Ac/ActiveIn/L1/P': 123,
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/ActiveIn/Connected': 1,
				'/Ac/Out/L1/P': 100,
				'/Dc/0/Voltage': 11.00,
				'/Dc/0/Current': -8,
				'/DeviceInstance': 266,
				'/Dc/0/MaxChargeCurrent': None,
				'/Soc': 53.2,
				'/State': 3,
				'/BatteryOperationalLimits/MaxChargeVoltage': None,
				'/BatteryOperationalLimits/MaxChargeCurrent': None,
				'/BatteryOperationalLimits/MaxDischargeCurrent': None,
				'/BatteryOperationalLimits/BatteryLowVoltage': None,
			})

		self._update_values()
		self._check_values({
			'/Dc/Battery/Power':  -88.0,
			'/ActiveBatteryService': 'com.victronenergy.vebus/266',
			'/AutoSelectedBatteryService': 'MultiUSB on dummy'})


		self._add_device('com.victronenergy.vebus.ttyO1',
			product_name='MultiTTY',
			values={
				'/Ac/ActiveIn/L1/P': 123,
				'/Ac/ActiveIn/ActiveInput': 0,
				'/Ac/ActiveIn/Connected': 1,
				'/Ac/Out/L1/P': 100,
				'/Dc/0/Voltage': 12.0,
				'/Dc/0/Current': 5,
				'/DeviceInstance': 2,
				'/Dc/0/MaxChargeCurrent': None,
				'/Soc': 53.2,
				'/State': 3,
				'/BatteryOperationalLimits/MaxChargeVoltage': None,
				'/BatteryOperationalLimits/MaxChargeCurrent': None,
				'/BatteryOperationalLimits/MaxDischargeCurrent': None,
				'/BatteryOperationalLimits/BatteryLowVoltage': None,
			})

		self._update_values()
		self._check_values({
			'/Dc/Battery/Power':  60,
			'/ActiveBatteryService': 'com.victronenergy.vebus/2',
			'/AutoSelectedBatteryService': 'MultiTTY on dummy'})
