from delegates.base import SystemCalcDelegate
from sc_utils import safeadd

class PvInverters(SystemCalcDelegate):
	def __init__(self):
		super(PvInverters, self).__init__()
		self.pvinverters = set()

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(PvInverters, self).set_sources(dbusmonitor, settings, dbusservice)
		dbusservice.add_path('/PvInvertersProductIds', value=[])

	def get_input(self):
		return [('com.victronenergy.pvinverter', [
				'/Connected',
				'/ProductName',
				'/Mgmt/Connection',
				'/Ac/L1/Power',
				'/Ac/L2/Power',
				'/Ac/L3/Power',
				'/Ac/L1/Current',
				'/Ac/L2/Current',
				'/Ac/L3/Current',
				'/Position',
				'/ProductId'])]

	def get_output(self):
		return [('/Ac/PvOnOutput/L1/Power', {'gettext': '%.0F W'}),
			('/Ac/PvOnOutput/L2/Power', {'gettext': '%.0F W'}),
			('/Ac/PvOnOutput/L3/Power', {'gettext': '%.0F W'}),
			('/Ac/PvOnOutput/L1/Current', {'gettext': '%.1F A'}),
			('/Ac/PvOnOutput/L2/Current', {'gettext': '%.1F A'}),
			('/Ac/PvOnOutput/L3/Current', {'gettext': '%.1F A'}),
			('/Ac/PvOnOutput/NumberOfPhases', {'gettext': '%.0F W'}),
			('/Ac/PvOnGrid/L1/Power', {'gettext': '%.0F W'}),
			('/Ac/PvOnGrid/L2/Power', {'gettext': '%.0F W'}),
			('/Ac/PvOnGrid/L3/Power', {'gettext': '%.0F W'}),
			('/Ac/PvOnGrid/L1/Current', {'gettext': '%.1F A'}),
			('/Ac/PvOnGrid/L2/Current', {'gettext': '%.1F A'}),
			('/Ac/PvOnGrid/L3/Current', {'gettext': '%.1F A'}),
			('/Ac/PvOnGrid/NumberOfPhases', {'gettext': '%.0F W'}),
			('/Ac/PvOnGenset/L1/Power', {'gettext': '%.0F W'}),
			('/Ac/PvOnGenset/L2/Power', {'gettext': '%.0F W'}),
			('/Ac/PvOnGenset/L3/Power', {'gettext': '%.0F W'}),
			('/Ac/PvOnGenset/L1/Current', {'gettext': '%.1F A'}),
			('/Ac/PvOnGenset/L2/Current', {'gettext': '%.1F A'}),
			('/Ac/PvOnGenset/L3/Current', {'gettext': '%.1F A'}),
			('/Ac/PvOnGenset/NumberOfPhases', {'gettext': '%d'})]

	def device_added(self, service, instance, *args):
		if service.startswith('com.victronenergy.pvinverter.'):
			self.pvinverters.add(service)
			self._updatepvinverterspidlist()

	def device_removed(self, service, instance):
		if service in self.pvinverters:
			self.pvinverters.discard(service)
			self._updatepvinverterspidlist()

	def _updatepvinverterspidlist(self):
		# Create list of connected pv inverters id's
		productids = set(self._dbusmonitor.get_value(p, '/ProductId') for p in self.pvinverters)
		productids.discard(None)
		self._dbusservice['/PvInvertersProductIds'] = list(productids)

	def get_totals(self):
		pos = {0: '/Ac/PvOnGrid', 1: '/Ac/PvOnOutput', 2: '/Ac/PvOnGenset'}
		newvalues = {}
		for pvinverter in self.pvinverters:
			# Position will be None if PV inverter service has just been removed (after retrieving the
			# service list).
			position = pos.get(self._dbusmonitor.get_value(pvinverter, '/Position'))
			if position is not None:
				for phase in range(1, 4):
					power = self._dbusmonitor.get_value(pvinverter, '/Ac/L%s/Power' % phase)
					if power is not None:
						path = '%s/L%s/Power' % (position, phase)
						newvalues[path] = safeadd(newvalues.get(path), power)

					current = self._dbusmonitor.get_value(pvinverter, '/Ac/L%s/Current' % phase)
					if current is not None:
						path = '%s/L%s/Current' % (position, phase)
						newvalues[path] = safeadd(newvalues.get(path), current)

		return newvalues
