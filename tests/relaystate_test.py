import os
import tempfile

# This adapts sys.path to include all relevant packages
import context

# our own packages
from base import TestSystemCalcBase
from delegates import RelayState

# Monkey patching for unit tests
import patches


class RelayStateTest(TestSystemCalcBase):
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

		self._add_device('com.victronenergy.settings', values={
			'/Settings/Relay/Function': 1, # Generator
		})

		self.gpio_dir = tempfile.mkdtemp()
		os.mkdir(os.path.join(self.gpio_dir, 'relay_1'))
		os.mkdir(os.path.join(self.gpio_dir, 'relay_2'))
		self.gpio1_state = os.path.join(self.gpio_dir, 'relay_1', 'value')
		self.gpio2_state = os.path.join(self.gpio_dir, 'relay_2', 'value')
		RelayState.RELAY_GLOB = os.path.join(self.gpio_dir, 'relay_*')

		# Relay 1 is on, relay 2 is off
		with file(self.gpio1_state, 'wt') as f:
			f.write('1')
		with file(self.gpio2_state, 'wt') as f:
			f.write('0')

	def tearDown(self):
		os.remove(self.gpio1_state)
		os.remove(self.gpio2_state)
		os.rmdir(os.path.join(self.gpio_dir, 'relay_1'))
		os.rmdir(os.path.join(self.gpio_dir, 'relay_2'))
		os.rmdir(self.gpio_dir)

	def test_relay_state(self):
		rs = RelayState()
		rs.set_sources(self._monitor, self._system_calc._settings, self._service)

		self._update_values(6000)
		self.assertEqual(self._service['/Relay/0/State'], 1)

		self._service.set_value('/Relay/0/State', 0)
		self.assertEqual(file(self.gpio1_state, 'rt').read(), '0')
		self.assertEqual(self._service['/Relay/0/State'], 0)

		self._service.set_value('/Relay/0/State', 1)
		self.assertEqual(file(self.gpio1_state, 'rt').read(), '1')
		self.assertEqual(self._service['/Relay/0/State'], 1)


	def test_stored_state(self):
		rs = RelayState()
		rs.set_sources(self._monitor, self._system_calc._settings, self._service)

		self._service.set_value('/Relay/0/State', 0)
		self._service.set_value('/Relay/1/State', 1)
		self._check_settings({
			'/Relay/0/State': 0,
			'/Relay/1/State': 1})

		self._service.set_value('/Relay/0/State', 1)
		self._service.set_value('/Relay/1/State', 0)
		self._check_settings({
			'/Relay/0/State': 1,
			'/Relay/1/State': 0})

	def test_relay_function(self):
		rs = RelayState()
		rs.set_sources(self._monitor, self._system_calc._settings, self._service)
		self.assertEqual(rs.relay_function, 1)

	def test_relay_init(self):
		rs = RelayState()
		rs.set_sources(self._monitor, self._system_calc._settings, self._service)

		self._monitor.set_value('com.victronenergy.settings',
			'/Settings/Relay/Function', 2) # Manual

		self._set_setting('/Settings/Relay/0/InitialState', 0)
		self._set_setting('/Settings/Relay/1/InitialState', 1)

		self._update_values(5000)
		self.assertEqual(self._service['/Relay/0/State'], 0)
		self.assertEqual(self._service['/Relay/1/State'], 1)
		self.assertEqual(file(self.gpio1_state, 'rt').read(), '0')
		self.assertEqual(file(self.gpio2_state, 'rt').read(), '1')

	def test_relay_init_no_manual(self):
		rs = RelayState()
		rs.set_sources(self._monitor, self._system_calc._settings, self._service)

		self._monitor.set_value('com.victronenergy.settings',
			'/Settings/Relay/Function', 0) # Alarms

		self._set_setting('/Settings/Relay/0/InitialState', 0)
		self._update_values(5000)
		self.assertEqual(self._service['/Relay/0/State'], 1) # Unaffected
		self.assertEqual(file(self.gpio1_state, 'rt').read(), '1') # Unaffected
