from itertools import chain

# Victron packages
import sc_utils
from delegates.base import SystemCalcDelegate
from delegates.schedule import ScheduledCharging

class BL(object):
    Disabled = 0
    Restart = 1
    Default = 2
    Absorption = 3
    Float = 4
    Discharged = 5
    ForceCharge = 6
    Sustain = 7
    LowSocCharge = 8

class SOCG(object):
    KeepCharged = 9
    Default = 10
    Discharged = 11
    LowSocCharge = 12

class SystemState(SystemCalcDelegate):
	""" Calculates the system state. If ESS is installed, show that state,
		otherwise return the VEBus state. """

	# vebus states are passed right through, and range from 0x00 (Off) to 0x0b (psu). Let's start ESS
	# states at 0x20.
	UNKNOWN = 0x00
	EXTERNALCONTROL = 0xFC
	DISCHARGING = 0x100
	SUSTAIN = 0x101
	RECHARGE = 0x102
	SCHEDULEDCHARGE = 0x103

	def __init__(self, sc):
		super(SystemState, self).__init__()
		self.systemcalc = sc

	def get_input(self):
		return [
			('com.victronenergy.battery', [
				'/Info/MaxDischargeCurrent',
				'/Info/MaxChargeCurrent',
				'/Info/ChargeRequest']),
			('com.victronenergy.settings', [
				'/Settings/CGwacs/BatteryLife/State',
				'/Settings/SystemSetup/MaxChargeCurrent',
				'/Settings/CGwacs/MaxDischargePower']),
			('com.victronenergy.vebus', [
				'/Hub4/AssistantId',
				'/Hub4/Sustain',
				'/State',
				'/VebusMainState',
				'/BatteryOperationalLimits/MaxChargeVoltage',
				'/BatteryOperationalLimits/MaxChargeCurrent',
				'/BatteryOperationalLimits/MaxDischargeCurrent',
				'/Bms/AllowToDischarge',
				'/Bms/AllowToCharge']),
			('com.victronenergy.multi', [
				'/State'
			]),
			('com.victronenergy.inverter', [
				'/State',])]

	def get_output(self):
		return [
			('/SystemState/State', {'gettext': '%s'}),
			('/SystemState/LowSoc', {'gettext': '%s'}),
			('/SystemState/BatteryLife', {'gettext': '%s'}),
			('/SystemState/DischargeDisabled', {'gettext': '%s'}),
			('/SystemState/ChargeDisabled', {'gettext': '%s'}),
			('/SystemState/SlowCharge', {'gettext': '%s'}),
			('/SystemState/UserChargeLimited', {'gettext': '%s'}),
			('/SystemState/UserDischargeLimited', {'gettext': '%s'}),
		]

	def bms_state(self, vebus):
		""" Get the BMS state from the Multi. First check the /Bms/ paths. That
		    handles the case for a VE.Bus BMS or a 2-signal BMS.  then read the BOL
		    paths where the DVCC assistant copies the BMS values. """
		# Will return None if no vebus BMS
		may_discharge = self._dbusmonitor.get_value(vebus,
			'/Bms/AllowToDischarge')
		may_charge = self._dbusmonitor.get_value(vebus,
			'/Bms/AllowToCharge')

		if may_discharge is None or may_charge is None:
			# There is no vebus BMS in the system. Check if there
			# are operational limits set by another BMS. If these values
			# don't exist we will get None, which we interpret as
			# a signal that discharge is allowed. This is handled adequately
			# because None != 0.
			may_discharge = self._dbusmonitor.get_value(vebus,
				'/BatteryOperationalLimits/MaxDischargeCurrent') != 0
			may_charge = self._dbusmonitor.get_value(vebus,
				'/BatteryOperationalLimits/MaxChargeCurrent') != 0
		return (bool(may_charge), bool(may_discharge))

	def bms_state_2(self, battery):
		""" Alternative method of getting the BMS state. This is used
		    when there is no vebus. Then it gets the state from the
		    selected battery service. """
		may_discharge = self._dbusmonitor.get_value(battery,
			'/Info/MaxDischargeCurrent') != 0
		may_charge = self._dbusmonitor.get_value(battery,
			'/Info/MaxChargeCurrent') != 0
		return may_charge, may_discharge

	def bms_forcecharge(self, battery):
		""" Check if the battery is requesting a charge. Used to indicate
		    on the GUI that we're Recharging. """
		return self._dbusmonitor.get_value(battery,
			'/Info/ChargeRequest') == 1

	def state(self, newvalues):
		vebus = newvalues.get('/VebusService')
		flags = sc_utils.SmartDict(dict.fromkeys(['LowSoc', 'BatteryLife',
		'DischargeDisabled', 'ChargeDisabled', 'SlowCharge', 'UserChargeLimited', 'UserDischargeLimited'], 0))

		if vebus is None:
			ss = SystemState.UNKNOWN

			# Look for Multi RS, Inverter RS, or a VE.Direct inverter
			inverter = next(chain(
				self._dbusmonitor.get_service_list('com.victronenergy.multi').keys(),
				self._dbusmonitor.get_service_list('com.victronenergy.inverter').keys()), None)
			if inverter is not None:
				ss = self._dbusmonitor.get_value(inverter, '/State')

			# Check if we can get the bms state from the selected batteryservice
			if self.systemcalc.batteryservice is not None:
				flags.ChargeDisabled, flags.DischargeDisabled = map(
					lambda x: int(not x), self.bms_state_2(self.systemcalc.batteryservice))

			return (ss, flags)

		# VEBUS is available. If a managed battery is present, then the
		# system state is "External Control". Otherwise it is whatever
		# the Multi's charge state may be.
		if self._dbusmonitor.get_value(vebus,
				'/BatteryOperationalLimits/MaxChargeVoltage') is not None:
			ss = SystemState.EXTERNALCONTROL
		else:
			ss = self._dbusmonitor.get_value(vebus, '/State')

		assistant_id  = self._dbusmonitor.get_value(vebus, '/Hub4/AssistantId')
		if assistant_id is None:
			# ESS not installed. Return vebus state
			return (ss, flags)

		# VEBUS is available and ESS is installed
		mainstate = self._dbusmonitor.get_value(vebus, '/VebusMainState')

		# Charge or bypass mode.
		if mainstate in (8, 9):
			# BMS state
			flags.ChargeDisabled, flags.DischargeDisabled = map(
				lambda x: int(not x), self.bms_state(vebus))

			# BatteryLife state
			hubstate = self._dbusmonitor.get_value('com.victronenergy.settings',
				'/Settings/CGwacs/BatteryLife/State')

			# User limit
			user_discharge_limit = self._dbusmonitor.get_value(
				'com.victronenergy.settings',
				'/Settings/CGwacs/MaxDischargePower')
			user_charge_limit = self._dbusmonitor.get_value(
				'com.victronenergy.settings',
				'/Settings/SystemSetup/MaxChargeCurrent')
			flags.UserDischargeLimited = int(user_discharge_limit == 0 and hubstate != SOCG.KeepCharged)
			flags.UserChargeLimited = int(user_charge_limit == 0)

			# ESS state
			if (hubstate != SOCG.KeepCharged) and ScheduledCharging.instance.active:
				ss = SystemState.SCHEDULEDCHARGE
			elif hubstate in (BL.Default, BL.Absorption, BL.Float, SOCG.Default):
				if (newvalues.get('/Dc/Battery/Power') or 0) < -30:
					ss = SystemState.DISCHARGING
			elif hubstate in (BL.Discharged, SOCG.Discharged):
				flags.LowSoc = 1
				flags.BatteryLife = int(hubstate == BL.Discharged)
			elif hubstate in (BL.ForceCharge, BL.LowSocCharge, SOCG.LowSocCharge):
				ss = SystemState.RECHARGE
				if hubstate == BL.ForceCharge:
					flags.SlowCharge = 1

			if self._dbusmonitor.get_value(self.systemcalc.batteryservice,
					'/Info/ChargeRequest') == 1:
				# Battery requested a recharge. Sustain may also be active
				# but this one is more important
				ss = SystemState.RECHARGE
			elif self._dbusmonitor.get_value(vebus, '/Hub4/Sustain'):
				# Sustain flag
				ss = SystemState.SUSTAIN

		return (ss, flags)

	def update_values(self, newvalues):
		newvalues['/SystemState/State'], flags = self.state(newvalues)
		newvalues.update({'/SystemState/' + k: v for k, v in flags.items()})
