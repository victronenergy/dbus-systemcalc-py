#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Code uses several dummyprocesses to put data on the dbus. They are:
# dummyvebus:        10%, 11V, 12A, 11 * 12 = 132W
# dummybattery:      20%, 21V, 22A, 23W
# dummycharger:           31V, 32A, 31 * 32 = 992W
# dummysolarcharger:      41V, 42A, 41 * 42 = 1722W

# Python
import dbus
import logging
import unittest
from subprocess import Popen
import sys
from time import sleep
from subprocess import check_output
import atexit
import os

DEVNULL = open(os.devnull, 'wb')

def tryterminate(target):
	try:
		target.terminate()
	except OSError:
		pass #ignore the error.  The OSError doesn't seem to be documented(?)
			 #as such, it *might* be better to process.poll() and check for
			 #`None` (meaning the process is still running), but that
			 #introduces a race condition.  I'm not sure which is better,
			 #hopefully someone that knows more about this than I do can
			 #comment.

def startinbackground(args, wait=2):
	print('starting %s' % args)
	target = Popen(args, stdout=DEVNULL)
	# register a call to make sure to kill the started process on our exit
	atexit.register(tryterminate, target)

	sleep(wait)
	return target

class TestBatteryData(unittest.TestCase):
	# The actual code calling VeDbusItemExport is in fixture_vedbus.py, which is ran as a subprocess. That
	# code exports several values to the dbus. And then below test cases check if the exported values are
	# what the should be, by using the bare dbus import objects and functions.

	def setUp(self):
		self._localsettings = startinbackground(['../../localsettings/localsettings.py'], 1)
		self._dbussystemcalc = startinbackground(['../dbus_systemcalc.py'], 1)

		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.settings', '/Settings/SystemSetup/BatteryService', 'SetValue', 'default']))

	def tearDown(self):
		self._dbussystemcalc.kill()
		self._dbussystemcalc.wait()
		self._localsettings.kill()
		self._localsettings.wait()

		#print(check_output(['pkill', 'dummybattery.py']))
		#print(check_output(['pkill', 'dummyvebus.py']))

	def test_01_noservices(self):
		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))

		self.assertEqual("'No battery monitor found'\n", check_output(
			['dbus', 'com.victronenergy.system', '/AutoSelectedBatteryService', 'GetValue']))

	def test_02_disconnected_vebus_is_ignored_in_auto_mode(self):
		vebus = startinbackground(['./dummyvebus.py'])

		# SOC is ignored, since vebus /State == INVALID
		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))

		# But voltage is used when available
		self.assertEqual('11\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		vebus.kill()
		vebus.wait()

	def test_03_connected_vebus_is_auto_selected(self):
		vebus = startinbackground(['./dummyvebus.py'])

		# Make /State valid, it should now be used
		check_output(['dbus', 'com.victronenergy.vebus.ttyO1', '/State', 'SetValue', '%0'])

		sleep(2) # give systemcalc a few seconds to update

		self.assertEqual('10\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))
		self.assertEqual('11\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))
		self.assertEqual('12\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Current', 'GetValue']))
		self.assertEqual('132\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Power', 'GetValue']))
		self.assertEqual("'Multi 12/3000 on CCGX-VE.Bus port'\n", check_output(
			['dbus', 'com.victronenergy.system', '/AutoSelectedBatteryService', 'GetValue']))

		vebus.kill()
		vebus.wait()

	def test_04_onlybattery_defaultsetting(self):
		battery = startinbackground(['./dummybattery.py'])

		self.assertEqual('20\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))
		self.assertEqual('21\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))
		self.assertEqual('22\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Current', 'GetValue']))
		self.assertEqual('23\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Power', 'GetValue']))
		self.assertEqual("'BMV-700 on VE.Direct port 1'\n", check_output(
			['dbus', 'com.victronenergy.system', '/AutoSelectedBatteryService', 'GetValue']))

		battery.kill()
		battery.wait()

	def test_05_batteryandvebus_defaultsetting(self):
		vebus = startinbackground(['./dummyvebus.py'])

		# Make /State valid
		check_output(['dbus', 'com.victronenergy.vebus.ttyO1', '/State', 'SetValue', '%0'])

		sleep(2) # give systemcalc a few seconds to update

		self.assertEqual('10\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))

		battery = startinbackground(['./dummybattery.py'])

		self.assertEqual('20\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))
		self.assertEqual("'BMV-700 on VE.Direct port 1'\n", check_output(
			['dbus', 'com.victronenergy.system', '/AutoSelectedBatteryService', 'GetValue']))

		battery.kill()
		battery.wait()

		sleep(2)  # Give systemcalc a few seconds, since it only updates its values once a second

		self.assertEqual('10\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))
		self.assertEqual("'Multi 12/3000 on CCGX-VE.Bus port'\n", check_output(
			['dbus', 'com.victronenergy.system', '/AutoSelectedBatteryService', 'GetValue']))

		vebus.kill()
		vebus.wait()

	def test_06a_battery_voltage_vebus(self):
		vebus = startinbackground(['./dummyvebus.py'])

		self.assertEqual('11\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		vebus.kill()
		vebus.wait()

	def test_06b_battery_voltage_solarcharger(self):
		solarcharger = startinbackground(['./dummysolarcharger.py'])

		self.assertEqual('41\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		solarcharger.kill()
		solarcharger.wait()

	def test_06c_battery_voltage_charger(self):
		charger = startinbackground(['./dummycharger.py'])

		self.assertEqual('31\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		charger.kill()
		charger.wait()

	def test_07_battery_voltage_sequence(self):
		vebus = startinbackground(['./dummyvebus.py'])

		self.assertEqual('11\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		solarcharger = startinbackground(['./dummysolarcharger.py'])
		sleep(2)
		self.assertEqual('41\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		charger = startinbackground(['./dummycharger.py'])
		sleep(2)
		self.assertEqual('41\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		solarcharger.kill()
		solarcharger.wait()
		sleep(2)
		self.assertEqual('31\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		charger.kill()
		charger.wait()

		sleep(2)
		self.assertEqual('11\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		vebus.kill()
		vebus.wait()

	def test_08_do_not_autoselect_vebus_soc_when_charger_is_present(self):
		vebus = startinbackground(['./dummyvebus.py'])
		solarcharger = startinbackground(['./dummysolarcharger.py'])

		# Make /State valid
		check_output(['dbus', 'com.victronenergy.vebus.ttyO1', '/State', 'SetValue', '%0'])
		sleep(2)

		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))

		charger = startinbackground(['./dummycharger.py'])

		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))

		solarcharger.kill()
		solarcharger.wait()

		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))

		charger.kill()
		charger.wait()

		sleep(2)

		# All chargers are gone, the SOC should be autoselected again
		self.assertEqual('10\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Soc', 'GetValue']))

		vebus.kill()
		vebus.wait()

	def test_09_when_hasdcsystem_is_disabled_system_should_be_invalid(self):
		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.settings', '/Settings/SystemSetup/HasDcSystem', 'SetValue', '%0']))

		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/System/Power', 'GetValue']))

	def test_10_calculation_of_dc_system(self):
		battery = startinbackground(['./dummybattery.py'])
		vebus = startinbackground(['./dummyvebus.py'])
		solarcharger = startinbackground(['./dummysolarcharger.py'])
		charger = startinbackground(['./dummycharger.py'])

		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.settings', '/Settings/SystemSetup/HasDcSystem', 'SetValue', '%1']))

		sleep(2)

		other = (31 * 32) + (41 * 42) + (11 * 12) - 23

		self.assertEqual(str(other) + '\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/System/Power', 'GetValue']))

		battery.kill()
		battery.wait()

		sleep(2)

		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/System/Power', 'GetValue']))

		vebus.kill()
		vebus.wait()
		solarcharger.kill()
		solarcharger.wait()
		charger.kill()
		charger.wait()

	def test_10b_calculation_of_dc_system_no_battery_power(self):
		battery = startinbackground(['./dummybattery.py'])
		vebus = startinbackground(['./dummyvebus.py'])
		solarcharger = startinbackground(['./dummysolarcharger.py'])

		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.settings', '/Settings/SystemSetup/HasDcSystem', 'SetValue', '%1']))
		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.settings', '/Settings/SystemSetup/BatteryService', 'SetValue', 'com.victronenergy.battery/0']))
		# Could not find a way to make the dbus tool set an invalid value (empty array of integers), so using
		# dbus module instead.
		bus = dbus.SessionBus()
		proxy = bus.get_object('com.victronenergy.battery.ttyO1', '/Dc/0/Power')
		proxy.SetValue(dbus.Array([], signature=dbus.Signature('i'), variant_level=1))

		sleep(2)

		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/System/Power', 'GetValue']))

		vebus.kill()
		vebus.wait()
		battery.kill()
		battery.wait()
		solarcharger.kill()
		solarcharger.wait()

	def test_11_battery_state(self):
		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/State', 'GetValue']))

		battery = startinbackground(['./dummybattery.py'])

		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.battery.ttyO1', '/Dc/0/Power', 'SetValue', '%40']))

		sleep(2)

		# Charging
		self.assertEqual('1\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/State', 'GetValue']))

		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.battery.ttyO1', '/Dc/0/Power', 'SetValue', '%-40']))

		sleep(2)

		# Discharging
		self.assertEqual('2\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/State', 'GetValue']))

		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.battery.ttyO1', '/Dc/0/Power', 'SetValue', '%1']))

		sleep(2)

		# Idle
		self.assertEqual('0\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/State', 'GetValue']))

		battery.kill()
		battery.wait()

	def test_12_no_battery_service(self):
		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.settings', '/Settings/SystemSetup/BatteryService', 'SetValue', 'nobattery']))

		battery = startinbackground(['./dummybattery.py'])
		sleep(2)

		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Power', 'GetValue']))
		self.assertEqual('[]\n', check_output(
			['dbus', 'com.victronenergy.system', '/AutoSelectedBatteryService', 'GetValue']))

		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.settings', '/Settings/SystemSetup/BatteryService', 'SetValue', 'default']))
		sleep(2)

		self.assertEqual('23\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Power', 'GetValue']))
		self.assertEqual("'BMV-700 on VE.Direct port 1'\n", check_output(
			['dbus', 'com.victronenergy.system', '/AutoSelectedBatteryService', 'GetValue']))

		battery.kill()
		battery.wait()

	def test_13_derive_battery(self):
		vebus = startinbackground(['./dummyvebus.py'])
		solarcharger = startinbackground(['./dummysolarcharger.py'])
		charger = startinbackground(['./dummycharger.py'])

		assert('0\n' == check_output(
			['dbus', 'com.victronenergy.settings', '/Settings/SystemSetup/HasDcSystem', 'SetValue', '%0']))

		sleep(2)

		p = (31 * 32) + (41 * 42) + (11 * 12)
		i = p / 41
		self.assertEqual(str(p) + '\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Power', 'GetValue']))
		self.assertEqual(str(i) + '\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Current', 'GetValue']))
		self.assertEqual('41\n', check_output(
			['dbus', 'com.victronenergy.system', '/Dc/Battery/Voltage', 'GetValue']))

		vebus.kill()
		vebus.wait()
		solarcharger.kill()
		solarcharger.wait()
		charger.kill()
		charger.wait()

if __name__ == "__main__":
	logging.basicConfig(stream=sys.stderr)
	logging.getLogger('').setLevel(logging.WARNING)

	try:
		check_output(['dbus'])
	except OSError:
		logging.error('Cannot start testing since the dbus-cli (dbus) is not available in the path')
		sys.exit(1)

	unittest.main()
