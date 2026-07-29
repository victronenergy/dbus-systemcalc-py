from gi.repository import GLib
from delegates.base import SystemCalcDelegate
from sc_utils import safeadd
from ve_utils import exit_on_error

class PvInverters(SystemCalcDelegate):
	def __init__(self):
		super(PvInverters, self).__init__()
		self.pvinverters = set()
		self._timer = None

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
				'/ProductId']),
			('com.victronenergy.acsystem', [
				'/Pv/L1/AcCoupledPower',
				'/Pv/L2/AcCoupledPower',
				'/Pv/L3/AcCoupledPower'])]

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
		elif service.startswith('com.victronenergy.acsystem.') and self._timer is None:
			# Only bother pushing AC-coupled PV to acsystem services (collections
			# of Multi-RS inverters) once one is present. The vast majority of
			# systems have no Multi-RS, so we avoid running the timer there. Once
			# started it is left running for the lifetime of the process.
			self._timer = GLib.timeout_add_seconds(5, exit_on_error, self._on_timer)

	def device_removed(self, service, instance):
		if service in self.pvinverters:
			self.pvinverters.discard(service)
			self._updatepvinverterspidlist()

	def _updatepvinverterspidlist(self):
		# Create list of connected pv inverters id's
		productids = set(self._dbusmonitor.get_value(p, '/ProductId') for p in self.pvinverters)
		productids.discard(None)
		self._dbusservice['/PvInvertersProductIds'] = list(productids)

	def map_position(self, p):
		""" Map the position of the PV-inverter to the AC-source on that
		    position. We're primarily concerned about Grid vs Genset. """
		if p == 1:
			return '/Ac/PvOnOutput'
		s = {
			0: self._dbusmonitor.get_value(
				'com.victronenergy.settings', '/Settings/SystemSetup/AcInput1'),
			2: self._dbusmonitor.get_value(
				'com.victronenergy.settings', '/Settings/SystemSetup/AcInput2')
			}.get(p)
		return {
			1: '/Ac/PvOnGrid',
			2: '/Ac/PvOnGenset',
			3: '/Ac/PvOnGrid'}.get(s)

	def get_totals(self):
		newvalues = {}
		for pvinverter in self.pvinverters:
			# Position will be None if PV inverter service has just been removed (after retrieving the
			# service list).
			pos = self._dbusmonitor.get_value(pvinverter, '/Position')
			if pos is not None and (position := self.map_position(pos)) is not None:
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

	def _on_timer(self):
		# Communicate the AC-coupled PV that is on the output across to any
		# acsystem services, so a collection of Multi-RS inverters can account
		# for it. When a phase has no AC-coupled PV the value is None; we write
		# nothing so the Multi-RS times out and assumes there is none.
		for service in self._dbusmonitor.get_service_list('com.victronenergy.acsystem'):
			for phase in range(1, 4):
				power = self._dbusservice[f'/Ac/PvOnOutput/L{phase}/Power']
				if power is None:
					continue
				path = f'/Pv/L{phase}/AcCoupledPower'
				if self._dbusmonitor.seen(service, path):
					self._dbusmonitor.set_value_async(service, path, power)
		return True
