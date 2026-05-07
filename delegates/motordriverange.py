from delegates.base import SystemCalcDelegate
from delegates.batterysoc import BatterySoc

PREFIX = "/MotorDrive"


class MotorDriveRange(SystemCalcDelegate):
	def __init__(self):
		super(MotorDriveRange, self).__init__()

	def get_output(self):
		return [
			(PREFIX + "/Range", {"gettext": "%dkm"}),
		]

	def update_values(self, newvalues):
		consumption_ah = self._dbusservice["/MotorDrive/ConsumptionAhkm"]
		soc = BatterySoc.instance.soc
		discharge_floor = BatterySoc.instance.discharge_floor if BatterySoc.instance.discharge_floor is not None else 0
		capacity_ah = self._dbusservice["/Dc/Battery/Capacity"]

		if consumption_ah is not None and soc is not None and capacity_ah is not None:
			remaining_soc = max(soc - discharge_floor, 0)
			remaining_ah = capacity_ah * (remaining_soc / 100)
			if consumption_ah > 0:
				range_km = remaining_ah / consumption_ah
				newvalues["/MotorDrive/Range"] = range_km
		pass
