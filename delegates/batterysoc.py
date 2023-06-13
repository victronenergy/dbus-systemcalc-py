from delegates.base import SystemCalcDelegate

class BatterySoc(SystemCalcDelegate):
	def __init__(self, sc):
		super(BatterySoc, self).__init__()
		self.systemcalc = sc

	def get_output(self):
		return [('/Dc/Battery/Soc', {'gettext': '%.0F %%'})]

	@property
	def soc(self):
		if self.systemcalc.batteryservice is not None:
			return self._dbusmonitor.get_value(self.systemcalc.batteryservice, '/Soc')
		return None

	def update_values(self, newvalues):
		newvalues['/Dc/Battery/Soc'] = self.soc
