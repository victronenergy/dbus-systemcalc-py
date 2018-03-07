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

	def test_relay_state(self):
		with tempfile.NamedTemporaryFile(mode='wt') as relay_path_fd:
			gpio1_dir = tempfile.mkdtemp()
			gpio1_state = os.path.join(gpio1_dir, 'value')
			gpio2_dir = tempfile.mkdtemp()
			gpio2_state = os.path.join(gpio2_dir, 'value')
			try:
				with file(relay_path_fd.name, 'wt') as f:
					f.write('{} {}'.format(gpio1_dir, gpio2_dir))
				with file(gpio1_state, 'wt') as f:
					f.write('1')
				with file(gpio2_state, 'wt') as f:
					f.write('0')
				RelayState.RELAY_PATH = relay_path_fd.name

				rs = RelayState()
				rs.set_sources(self._monitor, self._system_calc._settings, self._service)

				self._update_values(5000)
				self.assertEqual(self._service['/Relay/0/State'], 1)

				self._service.set_value('/Relay/0/State', 0)
				self.assertEqual(file(gpio1_state, 'rt').read(), '0')
				self.assertEqual(self._service['/Relay/0/State'], 0)

				self._service.set_value('/Relay/0/State', 1)
				self.assertEqual(file(gpio1_state, 'rt').read(), '1')
				self.assertEqual(self._service['/Relay/0/State'], 1)
			finally:
				os.remove(gpio1_state)
				os.removedirs(gpio1_dir)
				os.remove(gpio2_state)
				os.removedirs(gpio2_dir)
