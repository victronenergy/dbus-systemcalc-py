from delegates.base import SystemCalcDelegate
from delegates.multi import Multi

class HubTypeSelect(SystemCalcDelegate):
	def get_input(self):
		return [
			('com.victronenergy.vebus', ['/Hub/ChargeVoltage', '/Hub4/AssistantId'])]

	def get_output(self):
		return [('/Hub', {'gettext': '%s'}), ('/SystemType', {'gettext': '%s'})]

	def get_multi(self):
		try:
			return Multi.instance.vebus_service.service
		except AttributeError:
			return None

	def update_values(self, newvalues):
		# The code below should be executed after PV inverter data has been updated, because we need the
		# PV inverter total power to update the consumption.
		hub = None
		system_type = None
		vebus_path = self.get_multi()
		if vebus_path is not None:
			hub4_assistant_id = self._dbusmonitor.get_value(vebus_path, '/Hub4/AssistantId')
			if hub4_assistant_id is not None:
				hub = 4
				system_type = 'ESS' if hub4_assistant_id == 5 else 'Hub-4'
			elif self._dbusmonitor.get_value(vebus_path, '/Hub/ChargeVoltage') is not None or \
				newvalues.get('/Dc/Pv/Power') is not None:
				hub = 1
				system_type = 'Hub-1'
			elif newvalues.get('/Ac/PvOnOutput/NumberOfPhases') is not None:
				hub = 2
				system_type = 'Hub-2'
			elif newvalues.get('/Ac/PvOnGrid/NumberOfPhases') is not None or \
				newvalues.get('/Ac/PvOnGenset/NumberOfPhases') is not None:
				hub = 3
				system_type = 'Hub-3'
		newvalues['/Hub'] = hub
		newvalues['/SystemType'] = system_type
