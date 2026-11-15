import context
import patches
from base import TestSystemCalcBase

class PvProductIdTest(TestSystemCalcBase):
	def test_late_and_changed_product_ids_are_published(self):
		service = 'com.victronenergy.pvinverter.test'
		self._add_device(service, {'/ProductId': None, '/Position': 1})
		self._check_values({'/PvInvertersProductIds': []})
		for product_id in (123, 0, 456):
			self._monitor.set_value(service, '/ProductId', product_id)
			self._update_values()
			self._check_values({'/PvInvertersProductIds': [product_id]})
		self._remove_device(service)
		self._check_values({'/PvInvertersProductIds': []})
