from datetime import datetime, timedelta, timezone
import random
from gi.repository import GLib # type: ignore
from delegates.base import SystemCalcDelegate
from delegates.batterysoc import BatterySoc
from delegates.schedule import ScheduledWindow
from delegates.dvcc import Dvcc
from delegates.batterylife import BatteryLife
from delegates.batterylife import State as BatteryLifeState
from enum import Enum, IntFlag
from time import time
from logging.handlers import TimedRotatingFileHandler
import json
import os
import logging
import platform
import dbus #type:ignore
import uuid
from typing import Dict, cast, Callable
from ve_utils import wrap_dbus_value, unwrap_dbus_value

from s2python.common import (
    ReceptionStatusValues,
    ReceptionStatus,
	ResourceManagerDetails,
    Handshake,
    EnergyManagementRole,
    HandshakeResponse,
    SelectControlType,
	ControlType,
	CommodityQuantity,
	PowerMeasurement,
	PowerValue,
	Timer
)

from s2python.ombc import (
    OMBCInstruction,
    OMBCOperationMode,
    OMBCStatus,
    OMBCSystemDescription,
    OMBCTimerStatus
)

from s2python.s2_parser import S2Parser
from s2python.version import S2_VERSION
from s2python.s2_control_type import S2ControlType, PEBCControlType, NoControlControlType
from s2python.validate_values_mixin import S2MessageComponent

#debug purpose.
log_dir = "/data/log"    
if not os.path.exists(log_dir):
	os.mkdir(log_dir)

class NoDebugInfoWarningPropagationLogger(logging.Logger):
    def callHandlers(self, record):
        # Handle with this logger's handlers
        c = self
        found = 0
        while c:
            for hdlr in c.handlers:
                if record.levelno >= hdlr.level:
                    hdlr.handle(record)
                    found = 1
            # Prevent DEBUG logs from propagating
            if record.levelno <= logging.WARNING:
                break
            if not c.propagate:
                break
            c = c.parent
        if not found:
            logging.lastResort.handle(record)

class LevelFilter(logging.Filter):
    def __init__(self, level):
        self.level = level
    def filter(self, record):
        return record.levelno >= self.level
	
log_format = logging.Formatter(
    fmt='%(asctime)s,%(msecs)d %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

debug_handler = TimedRotatingFileHandler(log_dir + "/ems_debug.log", when="midnight", interval=1, backupCount=2)
info_handler = TimedRotatingFileHandler(log_dir + "/ems_info.log", when="midnight", interval=1, backupCount=2)

debug_handler.setLevel(logging.DEBUG)
debug_handler.setFormatter(log_format)
debug_handler.addFilter(LevelFilter(logging.DEBUG))

info_handler.setLevel(logging.INFO)
info_handler.setFormatter(log_format)
info_handler.addFilter(LevelFilter(logging.INFO))

logging.setLoggerClass(NoDebugInfoWarningPropagationLogger)
logger = logging.getLogger("ems")
logger.addHandler(debug_handler)
logger.addHandler(info_handler)
logger.setLevel(logging.DEBUG)
logger.propagate = True
#end debug purpose

HUB4_SERVICE = "com.victronenergy.hub4"
S2_IFACE = "com.victronenergy.S2"
KEEP_ALIVE_INTERVAL_S = 30 #seconds
COUNTER_PERSIST_INTERVAL_MS = 60000 #milli-seconds
CONNECTION_RETRY_INTERVAL_MS = 35000 #milli-seconds
INVERTER_LIMIT_MONITOR_INTERVAL_MS = 250 #milli-seconds
AC_DC_EFFICIENCY = 0.925 #Experimental Value.
USE_FAKE_BMS = True

def logger_debug_proxy(msg:str):
	pass

def logger_debug_proxy_pass(msg:str):
	pass

class Modes(int, Enum):
	Off = 0
	On = 1

class ConsumerType(int, Enum):
	Primary = 0
	Secondary = 1

class SystemTypeFlag(IntFlag):
	None_ = 0
	SinglePhase = 1
	DualPhase = 2
	ThreePhase = 4
	GridConnected = 8
	OffGrid = 16
	Saldating = 32
	Individual = 64
	FeedinAllowed = 128
	ZeroFeedin = 256

	def to_str(value: int) -> str:
		members = [flag.name for flag in SystemTypeFlag if flag & value]
		return "{}:".format(value) + ("|".join(members) if members else str(value))

class ClaimType(int, Enum):
	Total = 0
	AC = 1
	DC = 2
	ACDCAC = 3

class PropertyAccessPhase:
	def __init__(self, obj, props):
		self._obj = obj
		self._props = props
	
	def __getitem__(self, index):
		name = self._props[index]
		return getattr(self._obj, name)
	
	def __setitem__(self, index, value):
		name = self._props[index]
		setattr(self._obj, name, value)
	
class PropertyAccessCommodity:
	def __init__(self, obj, props):
		self._obj = obj
		self._props = props
	
	def __getitem__(self, key):
		return getattr(self._obj, self._props[key])
	
	def __setitem__(self, key, value):
		setattr(self._obj, self._props[key], value)

class PhaseAwareFloat():
	"""
		The PhaseAwareFloat offers access to values on different phases. Each value can be accessed
		in three ways, depending on the needs and available information to avoid continious if/else checks. 
		- Direct: obj.total, obj.l1, obj.l2, obj.l3, obj.dc 
		- via index: obj.by_phase[0], obj.by_phase[1], obj.by_phase[2], obj.by_phase[3], obj.by_phase[4]
		- by commodity: obj.by_commodity[CommodityQuantity.ELECTRIC_POWER_L1], etc. (only for l1,l2,l3) 

		PhaseAwareFloats support "+", "-", "+=" and "-=" operators.
	"""	
	def __init__(self, l1:float=0.0, l2:float=0.0, l3:float=0.0, dc:float=0.0):
		self._l1 = l1
		self._l2 = l2
		self._l3 = l3
		self._dc = dc

		#carrier for debug information. Not to be used for production purpose, can be anything.
		#just something to be dumped in logs, when != None
		self._diagnostic_label = None

		self.by_phase = PropertyAccessPhase(self, ["total","l1", "l2", "l3", "dc"])
		self.by_commodity = PropertyAccessCommodity(self, {
			CommodityQuantity.ELECTRIC_POWER_L1: "l1",
			CommodityQuantity.ELECTRIC_POWER_L2: "l2",
			CommodityQuantity.ELECTRIC_POWER_L3: "l3"
		})

	@classmethod
	def from_phase_aware_float(clazz, other):
		return clazz(
			other.l1,
			other.l2,
			other.l3,
			other.dc
		)

	def __iadd__(self, other):
		if not isinstance(other, PhaseAwareFloat):
			raise TypeError("Only PhaseAwareFloats can be added.")
		
		self._l1 += other._l1
		self._l2 += other._l2
		self._l3 += other._l3
		self._dc += other._dc

		return self		
	
	def __add__(self, other):
		if not isinstance(other, PhaseAwareFloat):
			raise TypeError("Only PhaseAwareFloats can be added.")
		
		return PhaseAwareFloat(
			self._l1 + other._l1,
			self._l2 + other._l2,
			self._l3 + other._l3,
			self._dc + other._dc,
		)

	def __isub__(self, other):
		if not isinstance(other, PhaseAwareFloat):
			raise TypeError("Only PhaseAwareFloats can be sub'd.")
		
		self._l1 -= other._l1
		self._l2 -= other._l2
		self._l3 -= other._l3
		self._dc -= other._dc

		return self	
		
	def __sub__(self, other):
		if not isinstance(other, PhaseAwareFloat):
			raise TypeError("Only PhaseAwareFloats can be sub'd.")
		
		return PhaseAwareFloat(
			self._l1 - other._l1,
			self._l2 - other._l2,
			self._l3 - other._l3,
			self._dc - other._dc,
		)
	
	@property
	def l1(self)->float:
		return self._l1

	@l1.setter
	def l1(self, value):
		self._l1 = value

	@property
	def l2(self)->float:
		return self._l2
	
	@l2.setter
	def l2(self, value):
		self._l2 = value

	@property
	def l3(self)->float:
		return self._l3
	
	@l3.setter
	def l3(self, value):
		self._l3 = value

	@property
	def dc(self)->float:
		return self._dc
	
	@dc.setter
	def dc(self, value):
		self._dc = value

	@property
	def total(self)->float:
		return self._l1 + self._l2 + self._l3 + self._dc
	
	def __repr__(self):
		return "PhaseAwareFloat[{}, {}, {}, {}, {}]".format(
			self.total, self._l1, self._l2, self._l3, self._dc
		)

class SolarOverhead():
	def __init__(self, l1:float, l2:float, l3:float, dcpv:float, reservation:float, battery_rate:float, 
			  inverterPowerL1:float, inverterPowerL2:float, inverterPowerL3:float, delegate):
		self.power:PhaseAwareFloat = PhaseAwareFloat(l1,l2,l3,dcpv)
		self.inverterPower:PhaseAwareFloat = PhaseAwareFloat(inverterPowerL1, inverterPowerL2, inverterPowerL3)
		self.power_reserved:PhaseAwareFloat = PhaseAwareFloat() #TODO: Power_reserved is not used any longer? Remove it?
		self._prior_power:PhaseAwareFloat = None
		self.power_claim:PhaseAwareFloat = None
		self.power_request:PhaseAwareFloat = None
		self._delegate:EMS = delegate
		self.battery_rate = battery_rate
		self.battery_reservation = reservation
		self.transaction_running = False

		if reservation > 0:
			#first use DCPV to cover the reservation. That is technically what happens anyway, when 
			#enabling AC Consumers anyway.
			if reservation <= self.power.dc:
				#whole reservation can be covered by DCPV.
				self.power_reserved.dc = reservation
			else:
				#need dcpv completly + some of ACPV.
				self.power_reserved.dc = self.power.dc
				reservation -= self.power.dc

				for l in [3,2,1]:
					if reservation > 0:
						if reservation <= self.power.by_phase[l] * AC_DC_EFFICIENCY:
							self.power_reserved.by_phase[l] = reservation / AC_DC_EFFICIENCY #need part of this phase
							reservation = 0
						else:
							reservation -= self.power.by_phase[l] * AC_DC_EFFICIENCY
							self.power_reserved.by_phase[l] = self.power.by_phase[l] #need all of this phase.

	def __repr__(self):
		return "SolarOverhead[power={}, res={}, tr={}]".format(
			self.power, self.battery_reservation, self.transaction_running
		)

	def begin(self):
		"""
			Creates a checkpoint for claiming power. If all claims required for a certain usage
			return true, call comit() afterwards. If at least one claim fails, call rollback() before
			trying another set of power variables. 
		"""
		if self.transaction_running:
			raise Exception("Solar Claim Transaction currently running, need to call comit() or rollback() before starting another one.")
		
		self._prior_power = PhaseAwareFloat.from_phase_aware_float(self.power)
		self.power_claim = PhaseAwareFloat()
		self.transaction_running = True
	
	def claim(self, commodity_quantity:CommodityQuantity, minv:float, maxv:float, primary:bool, force:bool=False)->bool:
		"""
			Claims a bunch of power. Returns true on success, false on error. If the requirements of an RM are satisfied,
			call comit() which returns a PhaseAwareFloat representing the powerclaim of the transaction.
		"""
		if not self.transaction_running:
			raise Exception("No Solar Claim Transaction currently running. Need to call begin() before claiming power.")
		
		#First, start to determine the actual amount we want to claim. It needs to be between min and max, as close to max as possible.
		#Also check, if reservation needs to be applied for this claim. If there is enough "total", we can drive the consumer. 
		#The claim however may source from any available Power theren is.
		claim_target_total = maxv
		
		#Build the PhaseAwareFloat representing the claim split onto individual phases.
		claim_target = PhaseAwareFloat()
		if commodity_quantity == CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC:
			for l in [1,2,3]:
				claim_target.by_phase[l] = claim_target_total / 3.0
		else:
			claim_target.by_commodity[commodity_quantity] = claim_target_total

		#if the consumer is not providing Powermeasurements, we store this as power_request
		#to be able to calculate consumption later.
		self.power_request = PhaseAwareFloat(claim_target.l1, claim_target.l2, claim_target.l3)
		
		#TODO: calculate the claim_factor as OMBC needs it. Other control types don't need this, but doesn't hurt either. 
		power_factor = (maxv - minv) / claim_target_total if claim_target_total > 0 else 0

		#now, deduct energy from the proper source. We start by allocating direct ACPV.
		claim_target = self._try_claim_ac(claim_target)
		
		if claim_target.total > 0:
			#Based on the system type we now proceed with DC or ACDCAC. If the system has a saldating measurement method,
			#We can claim ACDCAC lossless, so prefer that. Any other case preferably uses DC first.
		
			if SystemTypeFlag.Saldating in self._delegate.system_type_flags:
				claim_target = self._try_claim_acdcac(claim_target, 1.0)
				if (claim_target.total > 0):
					claim_target = self._try_claim_dc(claim_target)	
			else:
				claim_target = self._try_claim_dc(claim_target)
				if (claim_target.total > 0):
					claim_target = self._try_claim_acdcac(claim_target, AC_DC_EFFICIENCY ** 2)
		
		#check, if the claim_target is fully satisfied.
		if claim_target.total > 0:
			logger_debug_proxy("- Missing Power: {}W".format(claim_target.total))
			if not force:
				#claim just failed
				return False
			else:
				#Forced claim, punish the battery for what is missing. 
				logger_debug_proxy("-- Force claiming remaining power from dc: {}W".format(claim_target.total))
				self.power.dc -= claim_target.total
				self.power_claim.dc += claim_target.total
		
		#final considerations:
		#check if battery reservation would be violated, then this can't be allowed.
		#Exception is the state is forced, or the consumer is primary. 
		logger_debug_proxy ("- Claim {}W vs reservation {}W on budget {}W (Primary:{}, force:{})".format(self.power_claim.total, self.battery_reservation, self.power.total, primary, force))
		if (self.power.total < self.battery_reservation) and not primary and not force:
			logger_debug_proxy("-- Claiming {}W would violate Battery reservation. Rejecting.".format(self.power_claim.total))
			return False

		#last but not least: Primary consumers are allowed to run despite reservation. However, consumption needs to be covered
		#before they can be enabled. Check, if that is true for a primary request. 
		#Deny primaries unless the resulting overhead total is greater than 50 Watts. (To avoid some extensive on/off flickering)
		if (not force and primary and not self.power.total > 50):
			logger_debug_proxy("-- Claiming {}W (primary) would violate Consumption reservation. Rejecting.".format(self.power_claim.total))
			return False

		#And finally: We should not exceed the desired continuous inverter power. At least for consumption. 
		#If the system will exceed the limit to feedin, that is fine. 
		#For saldating system types, we consider this on a total-basis to allow multiphase regulation to do it's job. 
		#For Non-Saldating and offgrid systems, we have to do this per phase. 
		#if not force:
	#		if SystemTypeFlag.Saldating in self._delegate.system_type_flags:
	#			total_consumption = (self._delegate._dbusservice["/Ac/Consumption/L1/Power"] or 0) +(self._delegate._dbusservice["/Ac/Consumption/L2/Power"] or 0) +(self._delegate._dbusservice["/Ac/Consumption/L3/Power"] or 0) 
	#			total_ac_pv = ((self._delegate._dbusservice["/Ac/PvOnGrid/L1/Power"] or 0) + (self._delegate._dbusservice["/Ac/PvOnOutput/L1/Power"] or 0) +
	#						   (self._delegate._dbusservice["/Ac/PvOnGrid/L2/Power"] or 0) + (self._delegate._dbusservice["/Ac/PvOnOutput/L2/Power"] or 0) + 
	#						   (self._delegate._dbusservice["/Ac/PvOnGrid/L3/Power"] or 0) + (self._delegate._dbusservice["/Ac/PvOnOutput/L3/Power"] or 0))
	#			if total_consumption + self.power_claim.total > self._delegate.continuous_inverter_power + total_ac_pv:
	#				logger_debug_proxy("-- Claiming {}W would violate continuous inverter power. Rejecting.".format(self.power_claim.total))
	#				return False
	#		else:
	#			for l in [1,2,3]:
	#				if ((self._delegate._dbusservice["/Ac/Consumption/L{}/Power".format(l)] or 0) + self.power_claim.by_phase[l] > 
	#					self._delegate.continuous_inverter_power_per_phase + (self._delegate._dbusservice["/Ac/PvOnGrid/L{}/Power".format(l)] or 0) + 
	#					(self._delegate._dbusservice["/Ac/PvOnOutput/L{}/Power".format(l)] or 0)):
	#					logger_debug_proxy("-- Claiming {}W on L{} would violate continuous inverter power. Rejecting.".format(self.power_claim.total, l))
	#					return False
					
		#We either satisfied all needs or force-claimed power from dc.
		return True
	
	def _try_claim_ac(self, claim_target:PhaseAwareFloat):
		logger_debug_proxy("AC Claim begin. Claim {} and remaining: {}".format(self.power_claim, claim_target))

		#1) Direct AC Claim. 
		for l in [1,2,3]:
			if claim_target.by_phase[l] > 0:
				if claim_target.by_phase[l] <= self.power.by_phase[l]:
					#can be satisfied by ACPV.
					claimed = claim_target.by_phase[l]
					self.power_claim.by_phase[l] = claimed
					logger_debug_proxy("-- claimed {}W AC to be used on L{} (AC saturates)".format(claimed, l))
				else:
					#Not enough ACPV, claim what's available.
					claimed = max(self.power.by_phase[l], 0)
					self.power_claim.by_phase[l] = claimed
					logger_debug_proxy("-- claimed {}W AC to be used on L{} (not enough AC)".format(claimed, l))
				self.power.by_phase[l] -= claimed
				claim_target.by_phase[l] -= claimed
				logger_debug_proxy("---- AC L{} now {}W".format(l, self.power.by_phase[l]))

		logger_debug_proxy("AC done. Claim {} and remaining: {}".format(self.power_claim, claim_target))
		return claim_target

	def _try_claim_dc(self, claim_target:PhaseAwareFloat):
		logger_debug_proxy("DC Claim begin. DC is {}W".format(self.power.dc))
		for l in [1,2,3]:
			if claim_target.by_phase[l] > 0:
				if claim_target.by_phase[l] <= self.power.dc:
					#can be satisfied by DC.
					claimed = claim_target.by_phase[l]
					logger_debug_proxy("-- claimed {}W DC to be used on L{} (DC saturates)".format(claimed, l))
					self.power_claim.dc += claimed #incremental, every phase may source from DCPV
				else:
					#Not enough DC, claim what's available
					claimed = max(self.power.dc, 0)
					logger_debug_proxy("-- claimed {}W DC to be used on L{} (not enough DC)".format(claimed, l))
					self.power_claim.dc = claimed
				self.power.dc -= claimed
				logger_debug_proxy("---- DC now {}".format(self.power.dc))
				claim_target.by_phase[l] -= claimed
		
		logger_debug_proxy("DC done. Claim {} and remaining: {}".format(self.power_claim, claim_target))
		return claim_target

	def _try_claim_acdcac(self, claim_target:PhaseAwareFloat, efficiency_penalty:float):
		logger_debug_proxy("ACDCAC Claim begin. Overhead is {}".format(self.power))

		#3) Check, if we need to source more fron ACDCAC. That will be deducted with an efficiency penalty of 2 times conversion losses AC_DC_EFFICIENCY ** 2
		#   From the respective phase we are sourcing from. At this point, we have to validate claimings, what was initially calculated as "matching"
		#   against the total may now exceed the available budget due to conversion losses. 
		for l in [1,2,3]:
			if claim_target.by_phase[l] > 0:
				#claiming ACDCAC means, we can claim from any other phase that is NOT the current phase. 
				for o in [1,2,3]:
					if l != o:
						if self.power.by_phase[o] >= claim_target.by_phase[l]/efficiency_penalty:
							#can be totally satisfied by ACDCAC from o.
							effective_claim = claim_target.by_phase[l]
							total_claim = claim_target.by_phase[l]/efficiency_penalty
							self.power_claim.by_phase[o] += total_claim
							self.power.by_phase[o] -= total_claim
							claim_target.by_phase[l] -= effective_claim #satisfied.
							logger_debug_proxy("-- claimed {}W AC (Effective {}W) from L{} to be used on L{} (ACDCAC saturates)".format(total_claim, effective_claim, o, l))
						else:
							#there is not enough on o. eventually we have another o to try to get the remaining power.
							#take what this o has to offer.
							effective_claim = self.power.by_phase[o] * efficiency_penalty
							total_claim = self.power.by_phase[o]
							self.power_claim.by_phase[o] += total_claim
							self.power.by_phase[o] -= total_claim
							claim_target.by_phase[l] -= effective_claim #only amount after conversion hits the consumer. 
							logger_debug_proxy("-- claimed {}W AC (Effective {}W) from L{} to be used on L{} (not enough ACDCAC)".format(total_claim, effective_claim, o, l))
				
		logger_debug_proxy("ACDCAC done. Claim {} and remaining: {}".format(self.power_claim, claim_target))
		return claim_target
	
	def rollback(self):
		"""
			Rollback the current transaction, restoring prior values associated with the underlaying PhaseAwareFloat
			Object.
		"""
		if not self.transaction_running:
			raise Exception("No Solar Claim Transaction currently running. Need to call begin() before rolling back.")
		
		logger_debug_proxy("Rolling back overhead from {} to {}".format(self.power, self._prior_power))
		self.power = PhaseAwareFloat.from_phase_aware_float(self._prior_power)
		self._prior_power = None
		self.transaction_running = False
		self.power_claim=None

	def comit(self)->PhaseAwareFloat:
		"""
			Comits the ongoing transaction, returns a PhaseAwareFloat representing the claim on each Phase.
		"""
		if not self.transaction_running:
			raise Exception("No Solar Claim Transaction currently running. Need to call begin() before comit().")

		power_claim = self.power_claim

		self._prior_power = None
		self.power_claim=None
		self.transaction_running = False

		return power_claim

class S2RMDelegate():
	def __init__(self, monitor, service, instance, rmno, priority, consumer_type, ems):
		#General
		self.initialized = False
		self.service = service
		self.instance = instance
		self.rmno = rmno
		self.s2path = "/Devices/{}/S2".format(rmno)
		self._dbusmonitor = monitor
		self._keep_alive_missed = 0
		self.s2_parser = S2Parser()
		self.priority = priority
		self.consumer_type = consumer_type
		self._commit_count = 0 #to ensure responsibility if consumers don't react.
		self._ems:EMS=ems
		self.current_state_confirmed=True #will be reset, when new instructions are send. 
		self._reported_as_blocked = False
		self.ombc_transition_info = None
		
		if USE_FAKE_BMS:
			self._ems.available_fake_bms = sorted(self._ems.available_fake_bms)
			self._fake_bms_no = self._ems.available_fake_bms.pop(0)
			logger.info("{} | Assigned fakebms {} ".format(self.unique_identifier, self._fake_bms_no))

		#power tracking values
		self.power_claim:PhaseAwareFloat=PhaseAwareFloat()
		self.power_request:PhaseAwareFloat=PhaseAwareFloat()
		self.current_power:PhaseAwareFloat = None
		self._current_counter:PhaseAwareFloat = PhaseAwareFloat()
		self._current_timestamps:PhaseAwareFloat = PhaseAwareFloat()
		self._last_pop_powerstats:datetime = None

		#Generic Handler
		self._message_receiver=None
		self._disconnect_receiver=None
		self._keep_alive_timer=None
		self._reply_handler_dict:Dict[uuid.UUID, Callable[[ReceptionStatus], None]]={} #TODO Needs handling, when replies are never received?
		
		#Generic value holder
		self.rm_details=None
		self.active_control_type:ControlType=None
		
		#OMBC related stuff.
		self.ombc_system_description = None
		self.ombc_active_instruction = None
		self.ombc_active_operation_mode = None
		self._ombc_next_operation_mode = None

		#TODO: RM can change timer status by sending a OMBC.TimerStatus update. Need a handler for that?
		self.ombc_timers:dict[str, Timer] = {}
		self.ombc_timer_starts:dict[str, datetime] = {}

	@property
	def unique_identifier(self):
		return "{}_RM{}".format(self.service, self.rmno)
	
	def publish_fake_bms_values(self):
		"""
			Updates the Fake BMS display option with current values. 
		"""
		try:
			if not self.initialized:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Dc/0/Power", 0)
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Soc", 0)
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "Uninitialized: {}".format(
						self.unique_identifier.replace("com.victronenergy", "")
					))
				return
			
			if self.current_power is not None:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Dc/0/Power", self.current_power.total)
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Soc", 0)
			else:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Dc/0/Power", 0)
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Soc", 0)

			if self.rm_details is not None:
				# Setting info based on Control Type. 
				if self.active_control_type == ControlType.OPERATION_MODE_BASED_CONTROL:
					if self.ombc_transition_info is None:
						if self.ombc_active_operation_mode is not None:
							self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] @ {}".format(
								self.priority, self.rm_details.name, self.ombc_active_operation_mode.diagnostic_label
							))
					else:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] @ {}".format(
							self.priority, self.rm_details.name, self.ombc_transition_info
						))
				elif self.active_control_type == ControlType.NOT_CONTROLABLE:
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [NOCTRL]".format(
						self.priority, self.rm_details.name
					))
		except Exception as ex: 
			logger.error("Exception during fake bms publish. This may be temporary", exc_info=ex)


	def begin(self):
		"""
			Initializes the RM, establishes connection, handshake, etc. 
		"""
		self._s2_connect_async()
	
	def end(self):
		"""
			To be called when the RM leaves the dbus or an s2 timeout occurs. 
		"""
		self._s2_send_disconnect()

		if self._message_receiver is not None:
			self._dbusmonitor.dbusConn.remove_signal_receiver(self._s2_on_message_handler, path=self.s2path, signal_name="Message", dbus_interface=S2_IFACE)
			self._message_receiver = None

		if self._disconnect_receiver is not None:
			self._dbusmonitor.dbusConn.remove_signal_receiver(self._s2_on_disconnect_handler, path=self.s2path, signal_name="Disconnect", dbus_interface=S2_IFACE)
			self._disconnect_receiver = None

		if self._keep_alive_timer is not None:
			GLib.source_remove(self._keep_alive_timer)
			self._keep_alive_timer = None

		self.initialized=False
		logger.info("{} | RMDelegate is now uninitialized.".format(self.unique_identifier))

	def _keep_alive_loop(self):
		"""
			Sends the keepalive and monitors for success.
		"""
		def reply_handler(result): 
			if result:
				self._keep_alive_missed = 0
			else:
				self._keep_alive_missed = self._keep_alive_missed + 1	
		
		def error_handler(result):
			self._keep_alive_missed = self._keep_alive_missed + 1

		self._dbusmonitor.dbusConn.call_async(self.service, self.s2path, S2_IFACE, method='KeepAlive', signature='s',
										args=[wrap_dbus_value(self.unique_identifier)],
										reply_handler=reply_handler, error_handler=error_handler)
		
		if self._keep_alive_missed < 2: 
			return True
		else:
			logger.warning("{} | Keepalive MISSED ({})".format(self.unique_identifier, self._keep_alive_missed))
			self.end()
			return False

	def _s2_connect_async(self):
		"""
			Establishes Connection to the RM via S2. 
		"""
		#start to monitor for Signals: Message and Disconnect. Yes, we need to do this, before connection 
		#is successfull, else we have a race-condition on catching the first reply, if any. 
		self._message_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_message_handler,
			dbus_interface=S2_IFACE, signal_name='Message', path=self.s2path)

		self._disconnect_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_disconnect_handler,
			dbus_interface=S2_IFACE, signal_name='Disconnect', path=self.s2path)
		
		self._dbusmonitor.dbusConn.call_async(self.service, self.s2path, S2_IFACE, method='Connect', signature='si', 
			args=[wrap_dbus_value(self.unique_identifier), wrap_dbus_value(KEEP_ALIVE_INTERVAL_S)],
			reply_handler=self._s2_connect_callback_ok, error_handler=self._s2_connect_callback_error)

	def _s2_connect_callback_ok(self, result):
		logger.info("{} | S2-Connection established with Keep-Alive {}".format(self.unique_identifier, KEEP_ALIVE_INTERVAL_S))
		
		#Set KeepAlive Timer. 
		self._keep_alive_timer = GLib.timeout_add(KEEP_ALIVE_INTERVAL_S * 1000, self._keep_alive_loop)

		#RM is now ready to be managed.
		self.initialized = True

	def _s2_connect_callback_error(self, result):
		logger.warning("{} | S2-Connection failed. Operation will be retried in {}s".format(self.unique_identifier, CONNECTION_RETRY_INTERVAL_MS))
		self.end() #clean handlers and stuff.

	def _s2_on_message_handler(self, client_id, msg:str):
		if self.unique_identifier == client_id:
			#logger.info("Received Message from {}: {}".format(self.unique_identifier, msg))

			jmsg = json.loads(msg)

			#if jmsg["message_type"] != "ReceptionStatus":
			#	logger_debug_proxy("Received Message from {}: {}".format(self.unique_identifier, jmsg["message_type"]))

			if "message_type" in jmsg:
				#if client is not initialized, deny all messages, except Handshake.
				if jmsg["message_type"] == "Handshake" or self.initialized:
					if jmsg["message_type"] == "Handshake":
						self._s2_on_handhsake_message(self.s2_parser.parse_as_message(msg, Handshake))
					elif jmsg["message_type"] == "ResourceManagerDetails":
						self._s2_on_rm_details(self.s2_parser.parse_as_message(msg, ResourceManagerDetails))
					elif jmsg["message_type"] == "OMBC.SystemDescription":
						self._s2_on_ombc_system_description(self.s2_parser.parse_as_message(msg, OMBCSystemDescription))
					elif jmsg["message_type"] == "OMBC.Status":
						self._s2_on_ombc_status(self.s2_parser.parse_as_message(msg, OMBCStatus))
					elif jmsg["message_type"] == "PowerMeasurement":
						self._s2_on_power_measurement(self.s2_parser.parse_as_message(msg, PowerMeasurement))
					elif jmsg["message_type"] == "ReceptionStatus":
						p = self.s2_parser.parse_as_message(msg, ReceptionStatus)
						if p.subject_message_id in self._reply_handler_dict:
							self._reply_handler_dict[p.subject_message_id](p)
							del self._reply_handler_dict[p.subject_message_id]
					else:
						#Not yet implemented! 
						logger.warning("{} | Received an unknown Message: {} ".format(self.unique_identifier, jmsg["message_type"]))
						self._s2_send_reception_message(ReceptionStatusValues.PERMANENT_ERROR, jmsg["message_id"], "MessageType not yet implemented in EMS.")
				else:
					#Received another message than Handshake without beeing connected. Reject. 
					logger.warning("{} | Received a Message: {} while RM is not actively connected".format(self.unique_identifier, jmsg["message_type"]))
					self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, jmsg["message_id"], "Connection not yet established.")
						
	def _s2_on_power_measurement(self, message:PowerMeasurement):
		#RM reported Powermeasurement. Track internally, until EMS requests an update.
		first_report = False
		if self.current_power == None:
			first_report = True

		self.current_power = PhaseAwareFloat()
		for pv in message.values:
			if pv.commodity_quantity == CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC:
				for c in [CommodityQuantity.ELECTRIC_POWER_L1, CommodityQuantity.ELECTRIC_POWER_L2, CommodityQuantity.ELECTRIC_POWER_L3]:
					self.current_power.by_commodity[c] = pv.value / 3.0
			else:
				self.current_power.by_commodity[pv.commodity_quantity] = pv.value
		
		# if total is 0, remove the current_power and power_request. 
		if self.current_power.total == 0:
			self.current_power = None
		else:
			if first_report:
				logger.info("{} | First power report received: {}".format(self.unique_identifier, self.current_power))

		self._s2_send_reception_message(ReceptionStatusValues.OK, message)

	def _s2_on_ombc_system_description(self, message:OMBCSystemDescription):
		#sort opmodes based on their powerranges. most expensive topmost.
		logger.info("{} | New system description received. Reseting state tracking.".format(self.unique_identifier))
		def sum_key(i:OMBCOperationMode):
			sum = 0
			for r in i.power_ranges:
				sum += r.end_of_range
			return sum

		message.operation_modes.sort(key=sum_key, reverse=True)
		self.ombc_system_description = message
		#reset active state, so transitioning doesn't cause issues. There might be no transition between different system descriptions. 
		self.ombc_active_instruction = None
		self.ombc_active_operation_mode = None
		self._ombc_next_operation_mode = None
		self._s2_send_reception_message(ReceptionStatusValues.OK, message)
	
	def _s2_on_ombc_status(self, message:OMBCStatus):
		try:
			for opm in self.ombc_system_description.operation_modes:
				#FIXME: Theres an error with message.active_operation_mode_id in s2-pyhton. fix this, once it was fixed.
				#       Until then, compare root with id.
				if "{}".format(opm.id) == "{}".format(message.active_operation_mode_id.root):
					
					#Confirm, if we have the state confirmed and received a first power report about it. 
					prior_operation_mode = self.ombc_active_operation_mode
					if self._ombc_next_operation_mode is not None and opm.id == self._ombc_next_operation_mode.id:
						self.ombc_active_operation_mode = opm
						logger.info("{} | Confirmed next operation mode: '{}'".format(self.unique_identifier, self.ombc_active_operation_mode.diagnostic_label))	
						self.current_state_confirmed=True
						self._ombc_next_operation_mode = None
						self._commit_count = 0 #reset, we got response. 
					elif self._ombc_next_operation_mode is None:
						# status reported without change-request, accept.
						self.ombc_active_operation_mode = opm
						self.current_state_confirmed=True
						self._commit_count = 0 #reset, we got a RM triggered state change.
						logger.info("{} | Reported operation mode: '{}'".format(self.unique_identifier, self.ombc_active_operation_mode.diagnostic_label))

					#Check, if this transition starts any timer. Only required if we leave a well known operation mode. 
					if prior_operation_mode is not None:
						for t in self.ombc_system_description.transitions:
							if t.from_ == prior_operation_mode.id and t.to == self.ombc_active_operation_mode.id:
								#transition found, timer required?
								for tmr in t.start_timers:
									#find the timer we need to start and start it. 
									for tmr_cand in self.ombc_system_description.timers:
										if tmr_cand.id == tmr:
											logger_debug_proxy("{} | Transition from '{}' to '{}' causes a timer: '{}'. Timer started.".format(
												self.unique_identifier, prior_operation_mode.diagnostic_label, self.ombc_active_operation_mode.diagnostic_label,
												tmr_cand.diagnostic_label
											))

											self.ombc_timers[tmr] = tmr_cand
											self.ombc_timer_starts[tmr] = datetime.now(timezone.utc)
											break

					self._s2_send_reception_message(ReceptionStatusValues.OK, message)
					return

			#Operationmode is not known. This may be a temporary error.
			logger.error("Unknown operationmode-id reported: {}, expecting any of: {}".format(
				message.active_operation_mode_id,
				["{}=>{}".format(mode.id, mode.diagnostic_label) for mode in self.ombc_system_description.operation_modes]
			))
			self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, message, "Unknown operationmode-id: {}".format(message.active_operation_mode_id))
		except Exception as ex:
			logger.error("Exception during status reception. This may be temporary", exc_info=ex)

	def _s2_on_rm_details(self, message:ResourceManagerDetails):
		# Detail update. Store to keep information present.
		self.rm_details = message
		if len(message.available_control_types) == 0:
			self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, message,"No ControlType provided.")
			return

		self._s2_send_reception_message(ReceptionStatusValues.OK, message)

		# TODO: Control-Mode-Selection will later depend on the actual System Type. While some ControlTypes offer greater
		#       User convinience, for a offgrid-situation they are not really feasible (long term scheduling)
		# if there is only 1 mode (and that is NOCTRL) we can select that right away. RM doesn't want to be controlled currently. 
		if len(message.available_control_types) == 1 and ControlType.NOT_CONTROLABLE in message.available_control_types:
			def noctrl_reply_handler(reply:ReceptionStatus):
				if reply.status == ReceptionStatusValues.OK:
					self.active_control_type = ControlType.NOT_CONTROLABLE

			logger.warning("{} | Only offered NOCTRL, accepting.".format(self.unique_identifier))
			
			self._s2_send_message(
				SelectControlType(
					message_id=uuid.uuid4(),
					control_type=ControlType.NOT_CONTROLABLE
				),noctrl_reply_handler
			)

		else:
			#Check if OMBC is available, that is our prefered mode as of now.
			def ombc_reply_handler(reply:ReceptionStatus):
				if reply.status == ReceptionStatusValues.OK:
					self.active_control_type = ControlType.OPERATION_MODE_BASED_CONTROL

					if USE_FAKE_BMS:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] ".format(
							self.priority, self.rm_details.name
						))

			logger.info("{} | Offered OMBC, accepting.".format(self.unique_identifier))

			if ControlType.OPERATION_MODE_BASED_CONTROL in message.available_control_types:
				self._s2_send_message(
					SelectControlType(
						message_id=uuid.uuid4(),
						control_type=ControlType.OPERATION_MODE_BASED_CONTROL
					), ombc_reply_handler
				)
				
			else:
				#TODO: Implement other controltypes.
				#Any Other controltype is currenetly not implemented, we just can reject. 
				logger.error("{} | Offered no compatible ControlType. Rejecting request.".format(self.unique_identifier))
				self._s2_send_reception_message(ReceptionStatusValues.PERMANENT_ERROR, "No supported ControlType offered.")

	def _s2_on_handhsake_message(self, message:Handshake):
		#RM wants to handshake. Do that :) 
		logger.info("{} | Received handshake.".format(self.unique_identifier))
		if S2_VERSION in message.supported_protocol_versions:
			self._s2_send_reception_message(ReceptionStatusValues.OK, message)
			#Supported Version, Accept.
			resp = HandshakeResponse(
				message_id=uuid.uuid4(),
				selected_protocol_version=S2_VERSION
			)

			self._s2_send_message(resp)
		else:
			logger.warning("{} | Outdated version: {}; expected: {}".format(self.unique_identifier, message.supported_protocol_versions, S2_VERSION))
			#wrong version. Reject. 
			self._s2_send_reception_message(ReceptionStatusValues.INVALID_CONTENT, message)

	def _s2_on_disconnect_handler(self, client_id, reason):
		if self.unique_identifier == client_id:
			logger.info("{} | Received Disconnect: {}".format(self.unique_identifier, reason))
			self.end()
			  
	def _s2_send_reception_message(self, rsv:ReceptionStatusValues, src:S2MessageComponent, info:str=None):
		if isinstance(src, S2MessageComponent):
			message_id = str(src.to_dict()["message_id"])
		else:
			message_id = src

		resp = ReceptionStatus(
			status=rsv,
			subject_message_id = message_id,
			diagnostic_label=info
		)
		self._s2_send_message(resp)

	def _s2_send_message(self, message:S2MessageComponent, reply_handler: Callable[[ReceptionStatus], None] = None):
		'''
			Sends a s2 message. If a reply_handler is passed, this method will track for the response arriving
			and invoke the handler with the ReceptionStatus object as parameter.
		'''
		if reply_handler is not None:
			self._reply_handler_dict[message.model_dump()["message_id"]] = reply_handler

		try:
			self._dbusmonitor.dbusConn.call_async(self.service, self.s2path, S2_IFACE, method='Message', signature='ss', 
					args=[wrap_dbus_value(self.unique_identifier), wrap_dbus_value(message.to_json())], 
					reply_handler=None, error_handler=None)
		except Exception as ex:
			logger.error("Error sending a S2 Message.", exc_info=ex)
			logger.error("Mesesage was: {}".format(message.model_dump()))
			del self._reply_handler_dict[message.model_dump()["message_id"]]
	
	def _s2_send_disconnect(self):
		"""
			Sends a disconnect message to the RM. Will use fire and forget, as we don't
			care about if the message is receiving the rm, nor what he has to say about it. 
		"""
		try:
			logger.warning("{} | Sending disconnect.".format(self.unique_identifier))
			self._dbusmonitor.dbusConn.call_async(self.service, self.s2path, S2_IFACE, method='Disconnect', signature='s', 
					args=[wrap_dbus_value(self.unique_identifier)], 
					reply_handler=None, error_handler=None)
		except Exception as ex:
			logger.error("Error sending a S2 Message.", exc_info=ex)

	def self_assign_overhead(self, overhead:SolarOverhead) -> tuple[SolarOverhead, bool]:
		"""
			RM Delegate is claiming power that matches it's requirements.
			RMDelegate is waiting for comit() of EMS, before sending new instructions to RM.
		"""
		try:
			self.power_claim = None
			#based on control type, this is different.
			if self.active_control_type == ControlType.OPERATION_MODE_BASED_CONTROL:
				return self._ombc_self_assign_overhead(overhead)
								
		except Exception as ex:
			logger.error("Exception during Power assignment. This may be temporary", exc_info=ex)
			overhead.rollback() #restore state before claiming power values. 

		return overhead, False
	
	def _ombc_self_assign_overhead(self, overhead:SolarOverhead) -> tuple[SolarOverhead, bool]:
		#check all Operation modes, and if one fits. op modes have been sorted
		#when retrieved, so first one is most expensive and should be selected
		#if possible. 
		if self.ombc_system_description is None:
			logger.warning("{} | No System Description available".format(self.unique_identifier))	
			return overhead, False
		
		if self.ombc_active_operation_mode is None:
			logger.warning("{} | No active operation mode known".format(self.unique_identifier))	
			return overhead, False

		#Not every state may be reachable from within the current operation mode. 
		#So, what we will do here is: 
		# 1.) Get all States that are reachable or equal current state. 
		# 2.) They are sorted expensive to cheap, so for self-consumption-optimization, we start probing the most expensive sate. 
		# 3.) If we couldn't find any suitable state in 0 to n-2, we have to force state n-1 as that means: 
		#      - There isn't enough overhead to enter more expensive states. 
		#      - There isn't enough overhead to keep the current state. 
		#      - hence, the last state in the list - cheapest one - is the one we will choose. 
		eligible_operation_modes:list[OMBCOperationMode] = []
		was_change = False #Needs to be set to true, when assignment changes. 
		for opm in self.ombc_system_description.operation_modes:
			if self._ombc_can_transition(self.ombc_active_operation_mode, opm):
				eligible_operation_modes.append(opm)

		logger_debug_proxy("Eligible States: {}".format(self.unique_identifier, 
															[mode.diagnostic_label for mode in eligible_operation_modes]))

		if len(eligible_operation_modes) == 0:
			logger.error("{} | No valid operationmodes to choose from. Active is: {} / Selection is: {}".format(
				self.unique_identifier, 
				"{}=>{}".format(self.ombc_active_operation_mode.diagnostic_label, self.ombc_active_operation_mode.id) if self.ombc_active_operation_mode is not None else "None",
				["{}=>{}".format(mode.diagnostic_label, mode.id) for mode in self.ombc_system_description.operation_modes])
			)
			return overhead, was_change
		
		#this is our last resort.
		forced_state = eligible_operation_modes[len(eligible_operation_modes) -1]
		logger_debug_proxy("Forced State: {}".format(self.unique_identifier, forced_state.diagnostic_label))

		for opm in eligible_operation_modes:
			for pr in opm.power_ranges:
				#First check: If the power_claim is exceeding available total - it won't fit after considering efficiency losses. 
				#thus, for these states, we can directly omit to validate them througly and simple skip them. We basically start
				#above the state that may eventually fit.
				if (pr.start_of_range > overhead.power.total):
					logger_debug_proxy("Skipping detailed check on '{}'. {}W vs {}W raw available won't fit for sure.".format(
						self.unique_identifier, opm.diagnostic_label, pr.start_of_range, overhead.power.total
					))
					continue

				overhead.begin()	

				#TODO: Verify why there are multiple ranges?
				claim_success = overhead.claim(pr.commodity_quantity, pr.start_of_range, pr.end_of_range, 
					self.consumer_type==ConsumerType.Primary, opm.id == forced_state.id)
				
				if not claim_success:
					#maximum assignment for this powerrange failed for at least one powerrange requested. This OperationMode is currently not eligible. 
					logger_debug_proxy("{} | Operation Mode not eligible: '{}' due to missing availability on commodity: {}".format(self.unique_identifier, opm.diagnostic_label, pr.commodity_quantity))
					overhead.rollback()
					break
			
			if overhead.transaction_running:
				#Managed to verify all power ranges and transaction still running? This mode is eligible! 
				#Probe, if we are trapped in a transition timer, then we cannot do it anyway. 
				if self._ombc_check_timer_block(opm) == 0:
					#all good, commit. Deduct from budget, what we claim. 
					new_power_claim = overhead.comit()
					self.power_claim = new_power_claim
					self.power_request = overhead.power_request
					
					logger_debug_proxy("{} | Operation Mode selected: '{}'. (Power-Claim: {})".format(self.unique_identifier, opm.diagnostic_label, new_power_claim))

					#store this operation_mode as beeing the next one to be send. EMS will call comit() on the RM-Delegate, 
					#once it should inform the actual RM and send out a new instruction, if required. RM-Delegate has to 
					#track if a (re-)send is required. 
					self._ombc_next_operation_mode = opm
	
				else:
					# cannot change, trapped in timer. Thus, we need to revert the overhead
					# and lower it by the consumers active claim (if any)
					overhead.rollback()

					if (self.power_claim is not None):
						overhead.power -= self.power_claim

				break
		
		if self._ombc_next_operation_mode is not None and self._ombc_next_operation_mode.id != self.ombc_active_operation_mode.id:
			was_change = True

		return overhead, was_change

	def _ombc_can_transition(self, active_operation_mode:OMBCOperationMode, candidate:OMBCOperationMode)->bool:
		"""
			Checks, if the transition from active_operation_mode to candidate is teoretically possible,
			i.e. if a transition exists. Then this mode can be selected. Before transitioning however,
			blocking timers need to be validated using _ombc_check_timer_block.

			Transitioning from state X to state X is considered always allowed - that means, keep current operation mode. 
		"""
		if active_operation_mode.id == candidate.id:
			return True
			
		for t in self.ombc_system_description.transitions:
			if t.from_ == active_operation_mode.id and t.to == candidate.id:
				return True
		
		return False

	def comit(self):
		"""
			To be called, when all consumers have claimed their power share. If no new instruction is required 
			for the rm, there will be none. 
		"""
		if self.active_control_type == ControlType.OPERATION_MODE_BASED_CONTROL:
			#Transitioning may be based on timers. So, check if our transition is suspect to be delayed currently. 
			if self._ombc_next_operation_mode is not None and self._ombc_next_operation_mode.id != self.ombc_active_operation_mode.id:
				#send out op mode selection, as operation mode changed. 
				self.current_state_confirmed=False
				self.ombc_active_instruction = OMBCInstruction(
					message_id = uuid.uuid4(),
					id = uuid.uuid4(),
					execution_time= datetime.now(timezone.utc),
					operation_mode_factor=1.0, #TODO: This needs to be adjusted, along with the factor determined by power allocation. 
					operation_mode_id= self._ombc_next_operation_mode.id,
					abnormal_condition=False
				)

				logger.info("{} | Instruction send: OMBC = {} (Power-Claim: {})".format(self.unique_identifier, self._ombc_next_operation_mode.diagnostic_label, self.power_claim))

				self._s2_send_message(self.ombc_active_instruction)
				self._commit_count += 1
				#This has to be confirmed by the resource-manager, not assume it "worked".
				#self.ombc_active_operation_mode = self._ombc_next_operation_mode

				if self._commit_count >= 7:
					#Consumer is not reacting. That is odd. Only escape we have is to drop off and reconnect. 
					logger.warning("{} | RM didn't respond after 6 commits. Assuming stale, disconnecting.".format(self.unique_identifier))
					self.end()

	def _ombc_check_timer_block(self, target_operation_mode:OMBCOperationMode) -> float:
		"""
			Checks if there is a blocking timer, if there is, returns the amount of seconds to go. 
		"""
		#if the current mode is unknown, we have no block. 
		#if the next operation mode is unknown, it's no block at all.
		if self.ombc_active_operation_mode is None or target_operation_mode is None:
			self.ombc_transition_info = None
			return 0
		
		#attempting to transist between 2 operation modes. See, if there is a defined transition
		timer_to_invalidate = []
		for t in self.ombc_system_description.transitions:
			if t.from_ == self.ombc_active_operation_mode.id and t.to == target_operation_mode.id:
				#transition found, timer required?
				if len(t.blocking_timers) > 0:
					#yes, at least one blocking timer. Do we have timers running at all?
					if len(self.ombc_timers) > 0:
						#at least one timer is running. Check the blocking timers against the running timers and if they may have expired already.
						for blocking_timer_id in t.blocking_timers:
							for running_timer_id, running_timer in self.ombc_timers.items():
								if blocking_timer_id == running_timer_id:
									#this one is potentially blocking. Check, if it is still active. 
									if self.ombc_timer_starts[blocking_timer_id] + running_timer.duration.to_timedelta() <  datetime.now(timezone.utc):
										#timer is expired. Schedule for removel, it's non blocking anymore.
										timer_to_invalidate.append(blocking_timer_id)
									else:
										seconds_remaining = round(((self.ombc_timer_starts[blocking_timer_id] + running_timer.duration.to_timedelta()) 
									 		- datetime.now(timezone.utc)).total_seconds(),0)
										
										self.ombc_transition_info = "{} -> {} ({}s)".format(
											self.ombc_active_operation_mode.diagnostic_label, 
											target_operation_mode.diagnostic_label, seconds_remaining
										)
										logger_debug_proxy("{} | Timer '{}' is preventing to transition from '{}' to '{}' currently. ({}s)".format(
											self.unique_identifier, running_timer.diagnostic_label, self.ombc_active_operation_mode.diagnostic_label, 
											target_operation_mode.diagnostic_label, seconds_remaining
										))
										return seconds_remaining
		
		for id in timer_to_invalidate:
			del self.ombc_timers[id]
			del self.ombc_timer_starts[id]

		#no timer, reset transition info.
		self.ombc_transition_info = None
		return 0

	def pop_powerstats(self, now:datetime) -> PhaseAwareFloat:
		"""
			Returns a PhaseAwareFloat, representing momentary consumption.
		"""
		if self.current_power is not None:
			result = self.current_power
			self._last_pop_powerstats = now
			return result
		
		return None

class EMS(SystemCalcDelegate):
	#TODO: Refactor dateTime usage to _get_time everywhere, as this required for unit testing to time travel.
	_get_time = datetime.now

	def __init__(self):
		super(EMS, self).__init__()
		self.system_type_flags = SystemTypeFlag.None_
		self.managed_rms: Dict[str, S2RMDelegate] = {}
		self.rms_to_drop: list[str] = []
		self.continuous_inverter_power_per_phase = None #calculated after determining the systemtype.

		#consumption counters and momentary power values
		self.power_primary:PhaseAwareFloat = PhaseAwareFloat()
		self.power_secondary:PhaseAwareFloat = PhaseAwareFloat()
		self.counter_primary:PhaseAwareFloat = PhaseAwareFloat()
		self.counter_secondary:PhaseAwareFloat = PhaseAwareFloat()
		self.dcpv_balancing_offset:float = 0

		if USE_FAKE_BMS:
			self.available_fake_bms = [1,2,3,4,5,6,7,8,9]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(EMS, self).set_sources(dbusmonitor, settings, dbusservice)

		self._dbusservice.add_path('/Ems/Active', value=0, gettextcallback=lambda p, v: Modes(v))
		self._dbusservice.add_path('/Ems/Debug/LoopTime', value=0)
		self._dbusservice.add_path('/Ems/BatteryReservation', value=0)
		self._dbusservice.add_path('/Ems/BatteryReservationState', value=None)
		self._dbusservice.add_path('/Ems/SystemTypeFlags', value=0)

		for l in [1,2,3]:
			self._dbusservice.add_path('/Ems/PrimaryConsumer/Ac/L{}/Power'.format(l), value=None)
			self._dbusservice.add_path('/Ems/SecondaryConsumer/Ac/L{}/Power'.format(l), value=None)
		
		self._dbusservice.add_path('/Ems/PrimaryConsumer/Ac/Power', value=None)
		self._dbusservice.add_path('/Ems/SecondaryConsumer/Ac/Power', value=None)

		self.system_type_flags = self._determine_system_type_flags()

		#enable, if setting indicates enabled. 
		if self.mode == 1:
			self._enable()
		else:
			self._disable()

		#configure logging as requested. 
		if self.write_debug_logs:
			global logger_debug_proxy
			logger.info("Enabled debug logging for EMS.")
			logger_debug_proxy = logger.debug

	def get_settings(self):
		# Settings for EMS
		path = '/Settings/Ems'
		#EnergyCounters are stored in settings.

		settings = [
			("ems_mode", path + "/Mode", 0, 0, 1),
			("ems_debug", path + "/Debug/WriteDebugLogs", 0, 0, 1),
			("ems_clinterval", path + "/ControlLoopInterval", 5, 1, 60),
			("ems_balancingthreshold", path + '/BalancingThreshold', 98, 2, 98),
			("ems_batteryreservation", path + '/BatteryReservationEquation', "10000", "", ""),
			("ems_cip", path + "/ContinuousInverterPower", 4000.0, 0, 150000.0)
		]

		return settings

	def get_input(self):
		#Subscribe to 10 possible devices per service for now
		topic_list = []
		for i in range(0, 9):
			topic_list.append('/Devices/{}/S2/Priority'.format(i))
			topic_list.append('/Devices/{}/S2/ConsumerType'.format(i))

		return [
			('com.victronenergy.settings', [
				'/Settings/CGwacs/Hub4Mode',
				'/Settings/CGwacs/OvervoltageFeedIn'
			]),
			('com.victronenergy.switch', topic_list),
			('com.victronenergy.acload', topic_list),
			('com.victronenergy.battery', [
				'/CustomName'
			])
		]

	def get_output(self):
		return []

	def _check_s2_rm(self, serviceName, objectPath)->bool:
		"""
			Checks if the provided service and the provided path are of type S2_IFACE.
		"""
		try:
			self._dbusmonitor.dbusConn.call_blocking(serviceName, objectPath, S2_IFACE, 'GetValue', '', [])
			return True
		except dbus.exceptions.DBusException as e:
			return False
		
	def device_added(self, service, instance, *args):
		logger_debug_proxy("Device added: {}".format(service))
		i = 0
		while True:
			s2_rm_exists = self._check_s2_rm(service, "/Devices/{}/S2".format(i))

			if s2_rm_exists:
				priority = self._dbusmonitor.get_value(service, "/Devices/{}/S2/Priority".format(i)) or 50
				ct_raw = self._dbusmonitor.get_value(service, "/Devices/{}/S2/ConsumerType".format(i))
				consumer_type = ConsumerType(1 if ct_raw is None else ct_raw)
				delegate = S2RMDelegate(self._dbusmonitor, service, instance, i, priority, consumer_type, self)
				self.managed_rms[delegate.unique_identifier] = delegate
				logger.info("{} | Identified S2 RM {} on {}. Added to managed RMs".format(delegate.unique_identifier, i, service))
				delegate.begin()
			
			i += 1 #probe next one.
			
			#if we don't find anything within 10 rms, stop scanning. 
			if (i >= 10):
				break

	def device_removed(self, service, instance):
		logger_debug_proxy("Device removed: {}".format(service))

		#check, if this service provided one or multiple rm, we have been controlling. 
		known_rms = list(self.managed_rms.keys()) 
		for key in known_rms:
			if key.startswith(service):
				if USE_FAKE_BMS:
					if self.managed_rms[key]._fake_bms_no not in self.available_fake_bms:
						no= self.managed_rms[key]._fake_bms_no
						self.available_fake_bms.append(no)
				
				self.managed_rms[key].end()

				#if device is gone, remove it as managed rm. 
				del self.managed_rms [key]

	def settings_changed(self, setting, oldvalue, newvalue):
		if setting == 'ems_mode':
			if oldvalue == 0 and newvalue == 1:
				self._enable()
			if oldvalue == 1 and newvalue == 0:
				self._disable()
		
		#Check, if debug logging has been enabled, then setup our debug proxy.
		#Else set it to the pass-proxy.
		if setting == 'ems_debug':
			global logger_debug_proxy
			if newvalue == 1:
				logger.info("Enabled debug logging for EMS.")
				logger_debug_proxy = logger.debug
			else:
				logger.info("Disabled debug logging for EMS.")
				logger_debug_proxy = logger_debug_proxy_pass


	@property
	def mode(self):
		return self._settings['ems_mode']
	
	@property
	def write_debug_logs(self):
		return self._settings['ems_debug']
	
	@property
	def continuous_inverter_power(self):
		return self._settings['ems_cip']

	@property
	def control_loop_interval(self):
		return self._settings['ems_clinterval']
	
	@property
	def balancing_threshold(self):
		return self._settings['ems_balancingthreshold']

	@property
	def soc(self) -> float:
		"""
			current soc 0 - 100
		"""
		return BatterySoc.instance.soc
		
	def calculate_soc_res_map(self, equation:str) -> Dict[int,float]:
		"""
			Calculates the soc map 0 - 100 for the given equation.
		"""
		res = {}
		for i in range(0,101):
			try:
				reservation = max(0, round(eval(equation.replace("SOC", i))))
				res[i] = reservation
			except:
				return None
		return res

	@property
	def current_battery_reservation(self) -> float:
		"""
			returns the current desired battery reservation based on the user equation in watts.
			0 if error in equation. /Ems/BatteryReservationState will indicate if there is an error with the equation,
			or if the reservation is lowered by BMS capabilities.
		"""
		reservation = 0.0
		try:
			reservation = round(eval(self._settings['ems_batteryreservation'].replace("SOC", str(self.soc))))
			capability = self.get_charge_power_capability()
			dess_charge = self._dbusservice["/DynamicEss/ChargeRate"]
			dess_rs = self._dbusservice["/DynamicEss/ReactiveStrategy"]
			reservation_hint = "OK"

			#When we are at BalancingSoc + 1, Reservation can become 0. (ZeroFeedin and Offgrid) to Keep PV Alive 
			if self.system_type_flags & (SystemTypeFlag.OffGrid | SystemTypeFlag.ZeroFeedin):
				if self.soc is not None and self.soc >= self.balancing_threshold + 1:
					reservation = 0
					reservation_hint = "PVKA"

			if capability != None:
				if capability < reservation:
					reservation = capability
					reservation_hint = "BMS"

			# for now, only handle the case when DESS is issuing a positive chargerate.
			# having a lower chargerate issued than the calculated reservation otherwise would cause unused feedin.
			# TODO: When DESS is trying to charge from grid, Consumers can consume available solar and grid-pull is increased to match battery rate. 
			#       This should be avoided by setting the limitation to the desired chargerate, if the desired chargerate is > reservation.
			if dess_charge is not None and dess_charge > 0:
				if dess_charge != reservation:
					reservation = dess_charge
					reservation_hint = "DESS"
			
			#dess idle? TODO: eventually replace with a delegate.dynamicess.instance.isIdle() call 
			if dess_rs is not None and dess_rs in [5,8,9,15]:
				reservation = 0
				reservation_hint = "DESS"
				
			self._dbusservice["/Ems/BatteryReservationState"] = reservation_hint

			if USE_FAKE_BMS:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/CustomName", "Battery Reservation: {}W ({})".format(reservation, reservation_hint))

		except Exception as ex:
			reservation = 0.0
			self._dbusservice["/Ems/BatteryReservationState"] = "ERROR"
		
		self._dbusservice["/Ems/BatteryReservation"] = reservation
		return reservation
	
	def get_charge_power_capability(self) -> float:
		'''
		  Determines the systems maximum battery charge capability in Watts.
		  If the ccl and cvl fails to be determined, then None is returned.
		  None is to be distinguished from 0 (which means no charging allowed by the bms)
		'''
		battery = self._dbusservice["/ActiveBmsService"]

		# first, try to obtain values from the bms service.
		if battery is not None and battery != "":
			ccl = self._dbusmonitor.get_value(battery, '/Info/MaxChargeCurrent')
			cvl = self._dbusmonitor.get_value(battery, '/Info/MaxChargeVoltage')

			#TODO: Should take the smaller of CVL and actual chargevoltage here.
			#      System will not use the maximum allowed CVL for certain battery types.

			if (ccl is not None and cvl is not None):
				return ccl * cvl

		return None

	def _enable(self):
		'''
			Enables EMS.
		'''
		self._timer = GLib.timeout_add(self.control_loop_interval * 1000, self._on_timer) #regular control loop according to configuration.
		self._limit_timer = GLib.timeout_add(INVERTER_LIMIT_MONITOR_INTERVAL_MS, self._on_timer_check_inverter_limits) #quick monitoring of desired inverter limitations
		self._timer_track_power = GLib.timeout_add(1000, self._on_timer_track_power)
		self._timer_retry_connections = GLib.timeout_add(CONNECTION_RETRY_INTERVAL_MS, self._on_timer_retry_connection) #retry connection to devices periodically.
		self._dbusservice["/Ems/Active"] = 1
		logger.info("EMS activated with a control loop interval of {}s".format(self.control_loop_interval))

	def _disable(self):
		'''
			Disables EMS.
		'''
		self._dbusservice["/Ems/Active"] = 0
		logger.info("EMS deactivated.")

	def _determine_system_type_flags(self) -> SystemTypeFlag:
		'''
			Determines relevant system type flags required for operation. 
		'''
		system_type_flags = SystemTypeFlag.None_
		try:
			no_phases_grid = self._dbusservice["/Ac/Grid/NumberOfPhases"]
			no_phases_output = self._dbusservice["/Ac/ConsumptionOnOutput/NumberOfPhases"]
			grid_parallel = self._dbusservice["/Ac/ActiveIn/GridParallel"]
			multiphase_mode = self._dbusmonitor.get_value('com.victronenergy.settings', '/Settings/CGwacs/Hub4Mode')
			overvoltage_feedin = self._dbusmonitor.get_value('com.victronenergy.settings', '/Settings/CGwacs/OvervoltageFeedIn')

			# Determine Flags for this system. 
			if grid_parallel is not None and grid_parallel == 1:
				self.continuous_inverter_power_per_phase = self.continuous_inverter_power / no_phases_grid
				system_type_flags |= SystemTypeFlag.GridConnected
				if no_phases_grid == 1: system_type_flags |= SystemTypeFlag.SinglePhase
				elif no_phases_grid == 2: system_type_flags |= SystemTypeFlag.DualPhase
				elif no_phases_grid == 3: system_type_flags |= SystemTypeFlag.ThreePhase
				
				if not overvoltage_feedin: system_type_flags |= SystemTypeFlag.ZeroFeedin
				elif overvoltage_feedin: system_type_flags |= SystemTypeFlag.FeedinAllowed
				
				if multiphase_mode == 0: system_type_flags |= SystemTypeFlag.Individual
				elif multiphase_mode == 1: system_type_flags |= SystemTypeFlag.Saldating
			else:
				self.continuous_inverter_power_per_phase = self.continuous_inverter_power / no_phases_output
				system_type_flags |= SystemTypeFlag.OffGrid
				if no_phases_output == 1: system_type_flags |= SystemTypeFlag.SinglePhase
				elif no_phases_output == 2: system_type_flags |= SystemTypeFlag.DualPhase
				elif no_phases_output == 3: system_type_flags |= SystemTypeFlag.ThreePhase

		except Exception as ex:
			logger.warning("Unable to determine SystemTypeFlags by now. Retrying later...")
			logger.error("Exception was: ", exc_info=ex)
			#may happen during startup, until all delegates have populated their initial values. 
			pass

		self._dbusservice["/Ems/SystemTypeFlags"] = system_type_flags
		return system_type_flags
	
	def _on_timer_retry_connection(self):
		'''
			Retries connection to RMs that are currently in an unitialized state. 
		'''
		for unique_identifier, delegate in self.managed_rms.items():
			if not delegate.initialized:
				logger.info("{} | Retrying connection".format(unique_identifier))
				delegate.begin()
		
		return True

	def _on_timer_track_power(self):
		try:
			self.power_primary = PhaseAwareFloat()
			self.power_secondary = PhaseAwareFloat()

			for unique_identifier, delegate in self.managed_rms.items():
				if delegate.initialized:
					value = delegate.pop_powerstats(self._get_time(timezone.utc))

					if value is not None:
						if delegate.consumer_type == ConsumerType.Primary:
							self.power_primary += value

						elif delegate.consumer_type == ConsumerType.Secondary:
							self.power_secondary += value
			
			#dump on dbus
			for l in [1,2,3]:
				self._dbusservice["/Ems/PrimaryConsumer/Ac/L{}/Power".format(l)] = self.power_primary.by_phase[l]
				self._dbusservice["/Ems/SecondaryConsumer/Ac/L{}/Power".format(l)] = self.power_secondary.by_phase[l]

			self._dbusservice["/Ems/PrimaryConsumer/Ac/Power"] = self.power_primary.total
			self._dbusservice["/Ems/SecondaryConsumer/Ac/Power"] = self.power_secondary.total

		except Exception as ex:
			logger.error("Exception while publishing power records", exc_info=ex)

		return True

	def _on_timer_check_inverter_limits(self):
		'''
			The regular control loop is taking care to distribute power in a way, that each inverter operates
			at the desired continious power at maximum. Dropping solar production (ACPV) or raising consumption may cause
			this limit to be exceeded anyway. Thus, this loop is rapidly observing the desired limit and ensures
			loads are shedded, when the limit is exceeded. 
		'''
		# Monitor the total Inverting Power of each phase. This shall not exceed the continious inverter power. 
		# If it does, trigger an immediate re-calculation in order to drop some loads. Also monitor for overload alerts, 
		# if that is the case, pro-actively drop loads, ignoring the off_hysteresis to ensure system-stability.
		# TODO: Implement.
		pass

	def _on_timer(self):
		try:
			logger_debug_proxy("v------------------- LOOP -------------------v")
			# Control loop timer.
			now = self._get_time()
			self.system_type_flags = self._determine_system_type_flags()
			logger_debug_proxy("System Type Flags are: {}".format(SystemTypeFlag.to_str(self.system_type_flags)))

			if SystemTypeFlag.None_ == self.system_type_flags:
				logger.info("Unknown SystemTypeFlags. Doing nothing.")
				#TODO: We may come into Unknown-System-Type from another type for whatever reason.
				#      Thus, we have to make sure to disable all consumers eventually running here.
				return True

			available_overhead = self._get_available_overhead()

			logger_debug_proxy("SOC={}%, RSRV={}/{}W ({}), L1o={}W, L2o={}W, L3o={}W, dcpvo={}W, totalo={}W".format(
					self.soc,
					available_overhead.battery_rate,
					self.current_battery_reservation,
					self._dbusservice["/Ems/BatteryReservationState"],
					available_overhead.power.l1,
					available_overhead.power.l2,
					available_overhead.power.l3,
					available_overhead.power.dc,
					available_overhead.power.total,
				)
			)

			if USE_FAKE_BMS:
				try:
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Dc/0/Voltage", available_overhead.power.total)

					if available_overhead.battery_rate > -1 and available_overhead.battery_rate < 1:
						#0 will make the power value be calculated. avoid that.
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Dc/0/Power", 1)
					else:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Dc/0/Power", available_overhead.battery_rate)
									
					if available_overhead.battery_reservation > 0:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Soc", available_overhead.battery_rate / available_overhead.battery_reservation * 100.0)
					else:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Soc", 0)
				except:
					pass

			#only iterate when we have solar-overhead, OR EMS-caused consumption (then we may need to turn a consumer off.)
			if (available_overhead.power.total > 0 or 
	   			(self._dbusservice["/Ems/PrimaryConsumer/Ac/Power"] or 0) > 0 or 
				(self._dbusservice["/Ems/SecondaryConsumer/Ac/Power"] or 0) > 0):
				
				#Check, if there is a pending change on one RM. If so, we don't do anything until it's confirmed. 
				#Ask the RM to kindly resend.
				state_change_pending = False
				for unique_identifier, delegate in self.managed_rms.items():
					if not delegate.current_state_confirmed and delegate.initialized and delegate._ombc_next_operation_mode is not None :
						#TODO: Does successfull state change require a power report to be present as well? There may be huge delays until first report. Currently observing. 
						delegate.comit()
						logger.warning("{} | State change to '{}' pending. Skipping calculation round.".format(
							unique_identifier, 
							delegate._ombc_next_operation_mode.diagnostic_label if delegate._ombc_next_operation_mode is not None else "UNKNOWN"
						))
						state_change_pending = True

				if not state_change_pending:
					#Iterate over all known RMs, check their requirement and assign them a suitable Budget. 
					#The RMDelegate is responsible to communicate with it's rm upon .comit() beeing called. 
					#(Will be called after finishing all power assignments to avoid instructions beeing send out immediately)
					#sort RMs by priority before iterating.
					for unique_identifier, delegate in sorted(self.managed_rms.items(), key=lambda i: i[1].priority):
						logger_debug_proxy("=============================================================================================================")  
						if delegate.initialized and delegate.rm_details is not None:
							if delegate.active_control_type is not None and delegate.active_control_type != ControlType.NOT_CONTROLABLE:
								logger_debug_proxy("===== RM {} ({}) is controllable: {} =====".format(unique_identifier, delegate.rm_details.name, delegate.active_control_type))	
								available_overhead, was_change = delegate.self_assign_overhead(available_overhead)
								logger_debug_proxy("==> Remaining overhead: {}; Was Assignment? {}".format(available_overhead, was_change))
								if was_change:
									#There was a change in at least 1 consumer. So, we break, commit and await the change to happen, before we do more changes.
									#Else a system made out of many consumers will become to unstable due to reaction latencies all over the place. 
									logger.info("{} | Comiting change of assignment".format(delegate.unique_identifier))
									delegate.comit()
									break
							else:
								logger_debug_proxy("===== RM {} ({}) is uncontrollable: {} =====".format(unique_identifier, delegate.rm_details.name, delegate.active_control_type))	
						else:
							logger_debug_proxy("===== RM {} is not yet initialized. =====".format(unique_identifier))

				logger_debug_proxy("SOC={}%, RSRV={}/{}W ({}), L1o={}W, L2o={}W, L3o={}W, dcpvo={}W, totalo={}W".format(
						self.soc,
						available_overhead.battery_rate,
						self.current_battery_reservation,
						self._dbusservice["/Ems/BatteryReservationState"],
						available_overhead.power.l1,
						available_overhead.power.l2,
						available_overhead.power.l3,
						available_overhead.power.dc,
						available_overhead.power.total,
					)
				)
			else:
				logger_debug_proxy("ZzZzZzz...")

			if USE_FAKE_BMS:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Dc/0/Current", available_overhead.power.total)
				
				for unique_identifier, delegate in self.managed_rms.items():
					delegate.publish_fake_bms_values()

			#drop any RM, if we have to.
			for rm_to_drop in self.rms_to_drop:
				if rm_to_drop in self.managed_rms.keys():
					logger.warning("{} | Dropping RM from managed RMs.".format(rm_to_drop))
					
					if USE_FAKE_BMS:
						if self.managed_rms[rm_to_drop]._fake_bms_no not in self.available_fake_bms:
							no = self.managed_rms[rm_to_drop]._fake_bms_no
							self.available_fake_bms.append(no)
			
			#clean drop list, gone by now.
			self.rms_to_drop = []
				
			#reset unused fake BMS any time (for now, debug only)
			if USE_FAKE_BMS:
				for no in self.available_fake_bms:
					#reset that fake BMS to defaults.
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(no), "/Dc/0/Power", 0.0)
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(no), "/Dc/0/Soc", 0)
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(no), "/CustomName", "EMS Fake BMS {}".format(no))

			now2 = self._get_time()
			duration = (now2 - now).total_seconds() * 1000
			
			#logger_debug_proxy("Loop took {}ms".format(duration))
			#logger.info("Loop took {}ms".format(duration)) #double log that for now.
			self._dbusservice["/Ems/Debug/LoopTime"] = duration
			logger_debug_proxy("^------------------- LOOP -------------------^")

			if (self.mode == 1):
				return True	#keep timer up as long as mode is enabled.
		except Exception as ex:
			logger.fatal("Exception during control loop", exc_info=ex)

		#terminate timer
		return False
	
	def _get_available_overhead(self)-> SolarOverhead:
		"""
			Calculates the available solar overhead.
		"""
		batrate = (self._dbusservice["/Dc/Battery/Power"] or 0)
		l1 = (self._dbusservice["/Ac/PvOnGrid/L1/Power"] or 0) + (self._dbusservice["/Ac/PvOnOutput/L1/Power"] or 0) - (self._dbusservice["/Ac/Consumption/L1/Power"] or 0)
		l2 = (self._dbusservice["/Ac/PvOnGrid/L2/Power"] or 0) + (self._dbusservice["/Ac/PvOnOutput/L2/Power"] or 0) - (self._dbusservice["/Ac/Consumption/L2/Power"] or 0)
		l3 = (self._dbusservice["/Ac/PvOnGrid/L3/Power"] or 0) + (self._dbusservice["/Ac/PvOnOutput/L3/Power"] or 0) - (self._dbusservice["/Ac/Consumption/L3/Power"] or 0)

		#DCPV Overhead is: Actual DC PV Power - every ac consumption that is not baked by ACPV.
		#finally, if there is no solar at all, dcpv overhead should be negative and equal the
		#battery discharge rate.
		dcpv = (self._dbusservice["/Dc/Pv/Power"] or 0) * AC_DC_EFFICIENCY #dcpv has a penalty when beeing turned into AC Consumption.

		# now, we need to ADD power that is already beeing consumed by S2 Devices, because it will also be deducted in the Consumption-Values.
		for unique_identifier, delegate in self.managed_rms.items():
			if delegate.current_power is not None and delegate.initialized:
				#current power needs only be added to ac. It is real consumption and needs to be deducted where the unmanaged consumption is causing a deduction.
				l1 += delegate.current_power.l1
				l2 += delegate.current_power.l2
				l3 += delegate.current_power.l3
		
		if l1 < 0:
			dcpv -= abs(l1)
			l1=0
		
		if l2 < 0:
			dcpv -= abs(l2)
			l2=0

		if l3 < 0:
			dcpv -= abs(l3)
			l3=0
		
		#ZeroFeedin and Offgrid-Systems are suspect to PV beeing throttled when the batteries CCL is going down. 
		#This is undesired, throttled solar could be used for self-consumption-optimization instead.
		#Hence, when we are at balancingSoc + 1, we going to pretend more DCPV than there is, to increase HEMS consumption
		#and ensure solar is remaining unthrottled. When reaching balancingSoc - 1, we restore normal operation mode. 
		if self.system_type_flags & (SystemTypeFlag.ZeroFeedin | SystemTypeFlag.OffGrid):
			if self.soc is not None and self.soc >= self.balancing_threshold + 1:
				if (batrate > 0 or self.soc == 100) and self.dcpv_balancing_offset < self.continuous_inverter_power:
					self.dcpv_balancing_offset += 100 #increse 100 Watts per iteration until we reach a negative charge rate
					logger_debug_proxy("Increasing dcpv balancing offset to {}W".format(self.dcpv_balancing_offset))
			
			#reset if applicable.
			if self.soc is None or self.soc <= self.balancing_threshold - 1:
				self.dcpv_balancing_offset = 0
		else:
			#System Type is something else, we don't need an offset. (Leave this here, type can change)
			self.dcpv_balancing_offset = 0

		return SolarOverhead(
			round(l1, 1), 
			round(l2, 1), 
			round(l3, 1), 
			round(dcpv + self.dcpv_balancing_offset, 1),
			self.current_battery_reservation,
			batrate,
			self.continuous_inverter_power_per_phase,
			self.continuous_inverter_power_per_phase,
			self.continuous_inverter_power_per_phase,
			self
		)