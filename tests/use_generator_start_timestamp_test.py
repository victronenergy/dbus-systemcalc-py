import context
import patches
from base import TestSystemCalcBase

class GeneratorStartTimeTest(TestSystemCalcBase):
	def test_running_generator_preserves_its_reported_start_time(self):
		service = 'com.victronenergy.generator.test'
		self._add_device(service, {'/RunningByConditionCode': 1,
			'/Runtime': 120, '/LastStartTime': 1700000000})
		self._update_values()
		self._check_values({'/Ac/Genset/LastStartTime': 1700000000})
		self._monitor.set_value(service, '/LastStartTime', 1700000100)
		self._update_values()
		self._check_values({'/Ac/Genset/LastStartTime': 1700000100})
