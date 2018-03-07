import os
import tempfile

# This adapts sys.path to include all relevant packages
import context

# our own packages
import delegates
from base import TestSystemCalcBase

# Monkey patching for unit tests
import patches

class TestBuzzer(TestSystemCalcBase):
	def __init__(self, methodName='runTest'):
		TestSystemCalcBase.__init__(self, methodName)

	def test_gpio_buzzer(self):
		with tempfile.NamedTemporaryFile(mode='wt') as gpio_buzzer_ref_fd:
			gpio_dir = tempfile.mkdtemp()
			gpio_state = os.path.join(gpio_dir, 'value')
			with file(gpio_state, 'wt') as f:
				f.write('0')
			try:
				gpio_buzzer_ref_fd.write(gpio_dir)
				gpio_buzzer_ref_fd.flush()
				delegates.BuzzerControl.GPIO_BUZZER_PATH = gpio_buzzer_ref_fd.name
				bc = delegates.BuzzerControl()
				bc.set_sources(self._monitor, self._system_calc._settings, self._service)
				self.assertEqual(bc._pwm_frequency, None)
				self.assertEqual(bc._gpio_path, gpio_state)
				self._service.set_value('/Buzzer/State', 'aa')  # Invalid value, should be ignored
				self.assertEqual(self._service['/Buzzer/State'], 0)
				self.assertEqual(file(gpio_state, 'rt').read(), '0')
				self._service.set_value('/Buzzer/State', '1')
				self.assertEqual(self._service['/Buzzer/State'], 1)
				self.assertEqual(file(gpio_state, 'rt').read(), '1')
				self._update_values(interval=505)
				self.assertEqual(file(gpio_state, 'rt').read(), '0')
				self._update_values(interval=505)
				self.assertEqual(file(gpio_state, 'rt').read(), '1')
				self._service.set_value('/Buzzer/State', 0)
				self.assertEqual(file(gpio_state, 'rt').read(), '0')
			finally:
				os.remove(gpio_state)
				os.removedirs(gpio_dir)

	def test_pwm_buzzer(self):
		# This test will log an exception to the standard output, because the BuzzerControl tries to do
		# a ioctl on a regular file (a temp file created for this test), which is not allowed. We use
		# a regular file here because we do not want to enable the buzzer on the machine running this
		# unit test.
		with tempfile.NamedTemporaryFile(mode='wt') as pwm_buzzer_fd, \
				tempfile.NamedTemporaryFile(mode='wt') as tty_path_fd:
			pwm_buzzer_fd.write('400')
			pwm_buzzer_fd.flush()
			delegates.BuzzerControl.PWM_BUZZER_PATH = pwm_buzzer_fd.name
			delegates.BuzzerControl.TTY_PATH = tty_path_fd.name
			bc = delegates.BuzzerControl()
			bc.set_sources(self._monitor, self._system_calc._settings, self._service)
			self.assertEqual(bc._pwm_frequency, 400)
			self.assertEqual(bc._gpio_path, None)

	def test_pwm_buzzer_invalid_etc_file(self):
		with tempfile.NamedTemporaryFile(mode='wt') as pwm_buzzer_fd:
			pwm_buzzer_fd.write('xx')
			pwm_buzzer_fd.flush()
			delegates.BuzzerControl.PWM_BUZZER_PATH = pwm_buzzer_fd.name
			bc = delegates.BuzzerControl()
			bc.set_sources(self._monitor, self._system_calc._settings, self._service)
			self.assertEqual(bc._pwm_frequency, None)
			self.assertEqual(bc._gpio_path, None)
