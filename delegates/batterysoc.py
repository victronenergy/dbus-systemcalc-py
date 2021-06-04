from delegates.base import SystemCalcDelegate

class BatterySoc(SystemCalcDelegate):
	def __init__(self, sc):
		super(BatterySoc, self).__init__()
		self.systemcalc = sc

	def get_output(self):
		return [('/Dc/Battery/Soc', {'gettext': '%.0F %%'})]

	def update_values(self, newvalues):
		soc = None
		if self.systemcalc.batteryservice is not None:
			soc = self._dbusmonitor.get_value(self.systemcalc.batteryservice, '/Soc')

		newvalues['/Dc/Battery/Soc'] = soc
