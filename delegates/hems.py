from datetime import datetime, timedelta, timezone
import random
from gi.repository import GLib # type: ignore
from delegates.base import SystemCalcDelegate
from delegates.batterysoc import BatterySoc
from delegates.schedule import ScheduledWindow
from delegates.dvcc import Dvcc
from delegates.batterylife import BatteryLife
from delegates.batterylife import State as BatteryLifeState
from enum import Enum
from time import time
from logging.handlers import TimedRotatingFileHandler
import json
import os
import logging
import platform
import dbus #type:ignore
import uuid
from typing import Dict, cast
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

logger = logging.getLogger("hems_logger")
logger.setLevel(logging.INFO)

#debug purpose.
log_dir = "/data/log/S2"    
if not os.path.exists(log_dir):
	os.mkdir(log_dir)

logging.basicConfig(format='%(asctime)s,%(msecs)d %(levelname)s %(message)s',
	datefmt='%Y-%m-%d %H:%M:%S',
	level=logging.DEBUG, 
	handlers=[
		TimedRotatingFileHandler(log_dir + "/" + os.path.basename(__file__) + ".log", when="midnight", interval=1, backupCount=2),
		logging.StreamHandler()
	])

HUB4_SERVICE = "com.victronenergy.hub4"
S2_IFACE = "com.victronenergy.S2"
KEEP_ALIVE_INTERVAL = 30
COUNTER_PERSIST_INTERVAL = 60 
CONNECTION_RETRY_INTERVAL = 35
AC_DC_EFFICIENCY = 0.90 #Experimental Value.
USE_FAKE_BMS = True

class Modes(int, Enum):
	Off = 0
	On = 1

class ConsumerType(int, Enum):
	Primary = 0
	Secondary = 1

class SystemType(int, Enum):
	Unknown = 0
	GridConnected1Phase = 1
	GridConnected2PhaseSaldating = 2
	GridConnected3PhaseSaldating = 3
	GridConnected2PhaseIndividual = 4
	GridConnected3PhaseIndividual = 5
	ZeroFeedin1Phase = 6
	ZeroFeedin2Phase = 7
	ZeroFeedin3Phase = 8
	OffGrid1Phase = 9
	OffGrid2Phase = 10
	OffGrid3Phase = 11

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
		- by commodity: objc.by_commodity[CommodityQuantity.ELECTRIC_POWER_L1], etc. (only for l1,l2,l3) 

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
		self.power_reserved:PhaseAwareFloat = PhaseAwareFloat()
		self._prior_power:PhaseAwareFloat = None
		self.power_claim:PhaseAwareFloat = None
		self.power_request:PhaseAwareFloat = None
		self._delegate:HEMS = delegate
		
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
		
		#When claiming energy, we always try to do it the most efficient way: First AC-PV, Second DC-PV, Third ACDCAC-PV.
		#Only if none (or combination of all) claims does not work out, we consider the claim failed. 

		#First, start to determine the actual amount we want to claim. It needs to be between min and max, as close to max as possible.
		#Also check, if reservation needs to be applied for this claim. If there is enough "total", we can drive the consumer. 
		#The claim however may source from any available Power thereis.
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
		
		#TODO: However, when getting energy from DC or any other phase, we need to respect inverter-capabilities for certain system types.  

		#calculate the claim_factor as OMBC needs it. Other control types don't need this, but doesn't hurt either. 
		power_factor = (maxv - minv) / claim_target_total if claim_target_total > 0 else 0

		#now, deduct energy from the proper source. We start by allocating direct ACPV.
		claim_target = self._try_claim_ac(claim_target)
		
		#Based on the system type we now proceed with DC or ACDCAC. If the system has a saldating measurement method,
		#We can claim ACDCAC lossless, so prefer that. Any other case preferably uses DC first.
		if claim_target.total > 0:
			if self._delegate.system_type in [SystemType.GridConnected2PhaseSaldating, SystemType.GridConnected3PhaseSaldating]:
				claim_target = self._try_claim_acdcac(claim_target, 1.0)
				if (claim_target.total > 0):
					claim_target = self._try_claim_dc(claim_target)	
			else:
				claim_target = self._try_claim_dc(claim_target)
				if (claim_target.total > 0):
					claim_target = self._try_claim_acdcac(claim_target, AC_DC_EFFICIENCY ** 2)
		
		#check, if the claim_target is fully satisfied.
		if claim_target.total > 0:
			logger.debug("- Missing Power: {}W".format(claim_target.total))
			if not force:
				#claim just failed
				return False
			else:
				#Forced claim, punish the battery for what is missing. 
				logger.debug("-- Force claiming remaining power from dc: {}W".format(claim_target.total))
				self.power.dc -= claim_target.total
				self.power_claim.dc += claim_target.total
		
		#final considerations:
		#check if battery reservation would be violated, then this can't be allowed.
		#Exception is the state is forced, or the consumer is primary. 
		logger.debug ("- Claim {}W vs reservation {}W on budget {}W (Primary:{}, force:{})".format(self.power_claim.total, self.battery_reservation, self.power.total, primary, force))
		if (self.power.total < self.battery_reservation) and not primary and not force:
			logger.debug("-- Claiming {}W would violate Battery reservation. Rejecting.".format(self.power_claim.total))
			return False

		#last but not least: Primary consumers are allowed to run despite reservation. However, consumption needs to be covered
		#before they can be enabled. Check, if that is true for a primary request. 
		#Deny primaries unless the resulting overheat total is greater than 50 Watts. (To avoid some extensive on/off flickering)
		if (not force and primary and not self.power.total > 50):
			logger.debug("-- Claiming {}W (primary) would violate Consumption reservation. Rejecting.".format(self.power_claim.total))
			return False

		#We either satisfied all needs or force-claimed power from dc.
		return True
	
	def _try_claim_ac(self, claim_target:PhaseAwareFloat):
		logger.debug("AC Claim begin. Claim {} and remaining: {}".format(self.power_claim, claim_target))

		#1) Direct AC Claim. 
		for l in [1,2,3]:
			if claim_target.by_phase[l] > 0:
				if claim_target.by_phase[l] <= self.power.by_phase[l]:
					#can be satisfied by ACPV.
					claimed = claim_target.by_phase[l]
					self.power_claim.by_phase[l] = claimed
					logger.debug("-- claimed {}W AC to be used on L{} (AC saturates)".format(claimed, l))
				else:
					#Not enough ACPV, claim what's available.
					claimed = max(self.power.by_phase[l], 0)
					self.power_claim.by_phase[l] = claimed
					logger.debug("-- claimed {}W AC to be used on L{} (not enough AC)".format(claimed, l))
				self.power.by_phase[l] -= claimed
				claim_target.by_phase[l] -= claimed
				logger.debug("---- AC L{} now {}W".format(l, self.power.by_phase[l]))

		logger.debug("AC done. Claim {} and remaining: {}".format(self.power_claim, claim_target))
		return claim_target

	def _try_claim_dc(self, claim_target:PhaseAwareFloat):
		logger.debug("DC Claim begin. DC is {}W".format(self.power.dc))
		for l in [1,2,3]:
			if claim_target.by_phase[l] > 0:
				if claim_target.by_phase[l] <= self.power.dc:
					#can be satisfied by DC.
					claimed = claim_target.by_phase[l]
					logger.debug("-- claimed {}W DC to be used on L{} (DC saturates)".format(claimed, l))
					self.power_claim.dc += claimed #incremental, every phase may source from DCPV
				else:
					#Not enough DC, claim what's available
					claimed = max(self.power.dc, 0)
					logger.debug("-- claimed {}W DC to be used on L{} (not enough DC)".format(claimed, l))
					self.power_claim.dc = claimed
				self.power.dc -= claimed
				logger.debug("---- DC now {}".format(self.power.dc))
				claim_target.by_phase[l] -= claimed
		
		logger.debug("DC done. Claim {} and remaining: {}".format(self.power_claim, claim_target))
		return claim_target

	def _try_claim_acdcac(self, claim_target:PhaseAwareFloat, efficiency_penalty:float):
		logger.debug("ACDCAC Claim begin. Overhead is {}".format(self.power))

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
							logger.debug("-- claimed {}W AC (Effective {}W) from L{} to be used on L{} (ACDCAC saturates)".format(total_claim, effective_claim, o, l))
						else:
							#there is not enough on o. eventually we have another o to try to get the remaining power.
							#take what this o has to offer.
							effective_claim = self.power.by_phase[o] * efficiency_penalty
							total_claim = self.power.by_phase[o]
							self.power_claim.by_phase[o] += total_claim
							self.power.by_phase[o] -= total_claim
							claim_target.by_phase[l] -= effective_claim #only amount after conversion hits the consumer. 
							logger.debug("-- claimed {}W AC (Effective {}W) from L{} to be used on L{} (not enough ACDCAC)".format(total_claim, effective_claim, o, l))
				
		logger.debug("ACDCAC done. Claim {} and remaining: {}".format(self.power_claim, claim_target))
		return claim_target
	
	def rollback(self):
		"""
			Rollback the current transaction, restoring prior values associated with the underlaying PhaseAwareFloat
			Object.
		"""
		if not self.transaction_running:
			raise Exception("No Solar Claim Transaction currently running. Need to call begin() before rolling back.")
		
		logger.debug("Rolling back overhead from {} to {}".format(self.power, self._prior_power))
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
	def __init__(self, monitor, service, instance, rmno, priority, consumer_type, hems):
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
		self._hems:HEMS=hems
		self._reported_as_blocked = False
		
		if USE_FAKE_BMS:
			self._hems.available_fake_bms = sorted(self._hems.available_fake_bms)
			self._fake_bms_no = self._hems.available_fake_bms.pop(0)
			logger.info("Assigned fakebms {} to {}".format(self._fake_bms_no,self.unique_identifier))

		#power tracking values
		self.power_claim:PhaseAwareFloat=PhaseAwareFloat()
		self.power_request:PhaseAwareFloat=PhaseAwareFloat()
		self.current_power:PhaseAwareFloat = PhaseAwareFloat()
		self._current_counter:PhaseAwareFloat = PhaseAwareFloat()
		self._current_timestamps:PhaseAwareFloat = PhaseAwareFloat()
		self._last_pop_powerstats:datetime = None

		#Generic Handler
		self._message_receiver=None
		self._disconnect_receiver=None
		self._keep_alive_timer=None
		
		#Generic value holder
		self.rm_details=None
		self.active_control_type=None
		
		#OMBC related stuff.
		self.ombc_system_description = None
		self.ombc_active_operation_mode = None
		self._ombc_next_operation_mode = None
		self.ombc_active_instruction = None

		#TODO: RM can change timer status by sending a OMBC.TimerStatus update. Need a handler for that.
		self.ombc_timers:dict[str, Timer] = {}
		self.ombc_timer_starts:dict[str, datetime] = {}

		#TODO: Register value change listener for Priority and ConsumerType to react to configuration changes.
		#self._dbusmonitor.track_value(self.service, self.s2path + "/Priority", self.priority)
		#self._dbusmonitor.track_value(self.service, self.s2path + "/ConsumerType", self.consumer_type)

	@property
	def unique_identifier(self):
		return "{}_RM{}".format(self.service, self.rmno)
	
	def begin(self):
		"""
			Initializes the RM, establishes connection, handshake, etc. 
		"""
		if self._s2_connect():
			#Set KeepAlive Timer. 
			self._keep_alive_timer = GLib.timeout_add(KEEP_ALIVE_INTERVAL * 1000, self._keep_alive_loop)

			#RM is now ready to be managed.
			self.initialized = True
		else:
			self.initialized = False #will be retried in retry loop. 
	
	def end(self):
		"""
			To be called when the RM leaves the dbus or an s2 timeout occurs. 
		"""
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
		logger.info("RMDelegate is now uninitialized: {}".format(self.unique_identifier))

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
			#logger.debug("Keepalive OK for {}".format(self.unique_identifier))
			return True
		else:
			logger.warning("Keepalive MISSED for {} ({})".format(self.unique_identifier, self._keep_alive_missed))
			self.end()
			return False

	def _s2_connect(self) -> bool:
		"""
			Establishes Connection to the RM via S2. 
		"""
		#start to monitor for Signals: Message and Disconnect. 
		self._message_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_message_handler,
			dbus_interface=S2_IFACE, signal_name='Message', path=self.s2path)

		self._disconnect_receiver = self._dbusmonitor.dbusConn.add_signal_receiver(self._s2_on_disconnect_handler,
			dbus_interface=S2_IFACE, signal_name='Disconnect', path=self.s2path)
		
		if self._dbusmonitor.dbusConn.call_blocking(self.service, self.s2path, S2_IFACE, method='Connect', signature='si', 
										   args=[wrap_dbus_value(self.unique_identifier), wrap_dbus_value(KEEP_ALIVE_INTERVAL)]):
			logger.info("S2-Connection to {} established with Keep-Alive {}".format(self.unique_identifier, KEEP_ALIVE_INTERVAL))
			return True
		else:
			logger.warning("S2-Connection to {} failed. Operation will be retried in {}s".format(self.unique_identifier, CONNECTION_RETRY_INTERVAL))
			self.end() #clean handlers and stuff.
			return False

	def _s2_on_message_handler(self, client_id, msg:str):
		if self.unique_identifier == client_id:
			#logger.info("Received Message from {}: {}".format(self.unique_identifier, msg))

			jmsg = json.loads(msg)

			#if jmsg["message_type"] != "ReceptionStatus":
			#	logger.debug("Received Message from {}: {}".format(self.unique_identifier, jmsg["message_type"]))

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
						#logger.debug("Received ReceptionStatus from {}: {} -> {} ({})".format(self.unique_identifier, jmsg["message_type"], p.status, p.diagnostic_label))
					else:
						#Not yet implemented! 
						logger.warning("Received an unknown Message: {} from {}".format(jmsg["message_type"], self.unique_identifier))
						self._s2_send_reception_message(ReceptionStatusValues.PERMANENT_ERROR, jmsg["message_id"], "MessageType not yet implemented in HEMS.")
				else:
					#Received another message than Handshake without beeing connected. Reject. 
					logger.warning("Received a Message: {} from {} while RM is not actively connected".format(jmsg["message_type"], self.unique_identifier))
					self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, jmsg["message_id"], "Connection not yet established.")
						
	def _s2_on_power_measurement(self, message:PowerMeasurement):
		#RM reported Powermeasurement. Track internally, until HEMS requests an update.
		#TODO: If the underlaying Service is reporting a building Ac/Energy/Forward, use that counter.
		def increase_counter(self:S2RMDelegate, commodity:CommodityQuantity, value:float, timestamp):
			if self._current_timestamps.by_commodity[commodity]==0:
				#initialize counter for the first time, no evaluation possible.
				self.current_power.by_commodity[commodity] = value
				self._current_timestamps.by_commodity[commodity] = timestamp
			else:
				duration = (timestamp - self._current_timestamps.by_commodity[commodity]).total_seconds()
				consumption = (self.current_power.by_commodity[commodity] * duration / 3600.0) / 1000.0
				self._current_counter.by_commodity[commodity] += consumption
				self.current_power.by_commodity[commodity] = value
				self._current_timestamps.by_commodity[commodity] = timestamp

		total = 0
		for pv in message.values:
			if pv.commodity_quantity == CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC:
				for c in [CommodityQuantity.ELECTRIC_POWER_L1, CommodityQuantity.ELECTRIC_POWER_L2, CommodityQuantity.ELECTRIC_POWER_L3]:
					increase_counter(self, c, pv.value / 3.0, message.measurement_timestamp)
					total += pv.value / 3.0
			else:
				increase_counter(self,pv.commodity_quantity,pv.value, message.measurement_timestamp)
				total += pv.value

		if USE_FAKE_BMS:
			self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/Dc/0/Power", total)		
		
		self._s2_send_reception_message(ReceptionStatusValues.OK, message)

	def _s2_on_ombc_system_description(self, message:OMBCSystemDescription):
		#sort opmodes based on their powerranges. most expensive topmost.
		def sum_key(i:OMBCOperationMode):
			sum = 0
			for r in i.power_ranges:
				sum += r.end_of_range
			return sum

		message.operation_modes.sort(key=sum_key, reverse=True)
		self.ombc_system_description = message
		self._s2_send_reception_message(ReceptionStatusValues.OK, message)
	
	def _s2_on_ombc_status(self, message:OMBCStatus):
		for opm in self.ombc_system_description.operation_modes:
			#FIXME: Theres an error with message.active_operation_mode_id in s2-pyhton. fix this, once it was fixed.
			#       Until then, compare root with id.
			if "{}".format(opm.id) == "{}".format(message.active_operation_mode_id.root):
				self.ombc_active_operation_mode = opm

				logger.info("{} reported operation mode: '{}'".format(self.unique_identifier, self.ombc_active_operation_mode.diagnostic_label))

				if USE_FAKE_BMS:
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] @ {}".format(
						self.priority, self.rm_details.name, self.ombc_active_operation_mode.diagnostic_label
					))

				self._s2_send_reception_message(ReceptionStatusValues.OK, message)
				return

		#Operationmode is not known. This may be a temporary error.
		self._s2_send_reception_message(ReceptionStatusValues.TEMPORARY_ERROR, message, "Unknown operationmode-id: {}".format(message.active_operation_mode_id))

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
			self._s2_send_message(
				SelectControlType(
					message_id=uuid.uuid4(),
					control_type=ControlType.NOT_CONTROLABLE
				)
			)
			
			#TODO: This should be set, if transmission of the above message is confirmed. Need Async Continuation + callback.
			self.active_control_type = ControlType.NOT_CONTROLABLE
			logger.warning("RM {} only offered NOCTRL, accepting.".format(self.unique_identifier))

			if USE_FAKE_BMS:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [NOCTRL] ".format(
					self.priority, self.rm_details.name
				))

		else:
			#Check if OMBC is available, that is our prefered mode as of now.
			if ControlType.OPERATION_MODE_BASED_CONTROL in message.available_control_types:
				self._s2_send_message(
					SelectControlType(
						message_id=uuid.uuid4(),
						control_type=ControlType.OPERATION_MODE_BASED_CONTROL
					)
				)
				
				#TODO: This should be set, if transmission of the above message is confirmed. Need Async Continuation + callback.
				self.active_control_type = ControlType.OPERATION_MODE_BASED_CONTROL
				logger.info("RM {} offered OMBC, accepting.".format(self.unique_identifier))

				if USE_FAKE_BMS:
					self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] ".format(
						self.priority, self.rm_details.name
					))
			else:
				#TODO: Implement other controltypes.
				#Any Other controltype is currenetly not implemented, we just can reject. 
				logger.error("RM {} offered no compatible ControlType. Rejecting request.".format(self.unique_identifier))
				self._s2_send_reception_message(ReceptionStatusValues.PERMANENT_ERROR, "No supported ControlType offered.")

	def _s2_on_handhsake_message(self, message:Handshake):
		#RM wants to handshake. Do that :) 
		logger.info("Received handshake from {}.".format(self.unique_identifier))
		if S2_VERSION in message.supported_protocol_versions:
			self._s2_send_reception_message(ReceptionStatusValues.OK, message)
			#Supported Version, Accept.
			resp = HandshakeResponse(
				message_id=uuid.uuid4(),
				selected_protocol_version=S2_VERSION
			)

			self._s2_send_message(resp)
		else:
			logger.warning("RM {} is using outdated version: {}; expected: {}".format(self.unique_identifier, message.supported_protocol_versions, S2_VERSION))
			#wrong version. Reject. 
			self._s2_send_reception_message(ReceptionStatusValues.INVALID_CONTENT, message)

	def _s2_on_disconnect_handler(self, client_id, reason):
		if self.unique_identifier == client_id:
			logger.info("Received Disconnect from {}: {}".format(self.unique_identifier, reason))
			  
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

	def _s2_send_message(self, message:S2MessageComponent):
		#logger.debug("Send Message to {}: {}".format(self.unique_identifier, message.to_dict()["message_type"]))
		try:
			#TODO: Eventually we want to do something with replies here? Handler?
			self._dbusmonitor.dbusConn.call_async(self.service, self.s2path, S2_IFACE, method='Message', signature='ss', 
					args=[wrap_dbus_value(self.unique_identifier), wrap_dbus_value(message.to_json())], 
					reply_handler=None, error_handler=None)
		except Exception as ex:
			logger.error("Error sending a S2 Message.", exc_info=ex)
	
	def self_assign_overhead(self, overhead:SolarOverhead) -> SolarOverhead:
		"""
			RM Delegate is claiming power that matches it's requirements.
			RMDelegate is waiting for comit() of HEMS, before sending new instructions to RM.
		"""
		try:
			self.power_claim = None
			#based on control type, this is different.
			if self.active_control_type == ControlType.OPERATION_MODE_BASED_CONTROL:
				return self._ombc_self_assign_overhead(overhead)
								
		except Exception as ex:
			logger.error("Exception during Power assignment. This may be temporary", exc_info=ex)
			overhead.rollback() #restore state before claiming power values. 

		return overhead
	
	def _ombc_self_assign_overhead(self, overhead:SolarOverhead)->SolarOverhead:
		#check all Operation modes, and if one fits. op modes have been sorted
		#when retrieved, so first one is most expensive and should be selected
		#if possible. 
		if self.ombc_system_description is None:
			logger.warning("No System Description available for {}".format(self.unique_identifier))	
			return overhead
		
		if self.ombc_active_operation_mode is None:
			logger.warning("No active operation mode known for {}".format(self.unique_identifier))	
			return overhead

		#Not every state may be reachable from within the current operation mode. 
		#So, what we will do here is: 
		# 1.) Get all States that are reachable or equal current state. 
		# 2.) They are sorted expensive to cheap, so for self-consumption-optimization, we start probing the most expensive sate. 
		# 3.) If we couldn't find any suitable state in 0 to n-2, we have to force state n-1 as that means: 
		#      - There isn't enough overhead to enter more expensive states. 
		#      - There isn't enough overhead to keep the current state. 
		#      - hence, the last state in the list - cheapest one - is the one we will choose. 
		eligible_operation_modes:list[OMBCOperationMode] = []
		for opm in self.ombc_system_description.operation_modes:
			if self._ombc_can_transition(self.ombc_active_operation_mode, opm):
				eligible_operation_modes.append(opm)

		logger.debug("Eligible States for consumer {}: {}".format(self.unique_identifier, 
															[mode.diagnostic_label for mode in eligible_operation_modes]))

		#this is our last resort.
		#FIXME: Once in a while this throws a index out of bound exception. Find Reason and catch it properly.
		forced_state = eligible_operation_modes[len(eligible_operation_modes) -1]
		logger.debug("Forced State for consumer {}: {}".format(self.unique_identifier, forced_state.diagnostic_label))

		for opm in eligible_operation_modes:
			overhead.begin()
			for pr in opm.power_ranges:
				claim_success = overhead.claim(pr.commodity_quantity, pr.start_of_range, pr.end_of_range, 
					self.consumer_type==ConsumerType.Primary, opm.id == forced_state.id)
				
				if not claim_success:
					#maximum assignment for this powerrange failed for at least one powerrange requested. This OperationMode is currently not eligible. 
					logger.debug("Operation Mode not eligible: '{}' on {} due to missing availability on commodity: {}".format(opm.diagnostic_label, self.unique_identifier, pr.commodity_quantity))
					overhead.rollback()
					break
			
			if overhead.transaction_running:
				#Managed to verify all power ranges and transaction still running? This mode is eligible! 
				logger.debug("Operation Mode selected: '{}' on {}".format(opm.diagnostic_label, self.unique_identifier))

				old_power_claim = self.power_claim
				new_power_claim = overhead.comit()

				logger.debug("Power-Claim: {}".format(new_power_claim))

				if (old_power_claim is not None and old_power_claim.total != new_power_claim.total):
					logger.debug("Power Claim changed from {}W to {}W. Overhead now is: {}W".format(old_power_claim.total, new_power_claim.total, overhead.power.total))

				#store this operation_mode as beeing the next one to be send. EMS will call comit() on the RM-Delegate, 
				#once it should inform the actual RM and send out a new instruction, if required. RM-Delegate has to 
				#track if a (re-)send is required. 
				self._ombc_next_operation_mode = opm

				# When a consumer is going from a high to a low claim, but stuck in transition time - the overhead will already be considered
				# unclaimed, other consumers will be enabled. This shouldn't happen, overhead needs to stay "blocked" until the consumer managed
				# to reduce it's power consumption. Therefore, if the transition is blocked AND the new claim is smaller, we keep the target mode
				# but revert the claim.  
				if self._ombc_check_timer_block() > 0:
					if (old_power_claim is not None and old_power_claim.total > new_power_claim.total):
						logger.warning("Consumer {} is stuck in transition-timer. Reverting powerclaim from {} to {} until transition is possible.".format(
							self.unique_identifier, new_power_claim.total, old_power_claim.total
						))
						self.power_claim = old_power_claim
						overhead.power += new_power_claim
						overhead.power -= old_power_claim
				else:
					#good to go, this will happen. keep the new claim. 
					self.power_claim = new_power_claim
					self.power_request = overhead.power_request
				break
		
		#FIXME: If we are here, and didn't find a proper operation mode with 0Watt - the client probably didn't report
		#      one. So, let's see, if we can switch to a NOCTRL mode, if not, drop the connection.
		
		return overhead

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
			if self._ombc_next_operation_mode is not None and self.ombc_active_operation_mode != self._ombc_next_operation_mode:
				#check, if transition is blocked. Else we will retry later, no problem. 
				seconds_blocked = self._ombc_check_timer_block()
				if seconds_blocked <= 0.0:
					#send out op mode selection, as operation mode changed. 
					self.ombc_active_instruction = OMBCInstruction(
						message_id = uuid.uuid4(),
						id = uuid.uuid4(),
						execution_time= datetime.now(timezone.utc),
						operation_mode_factor=1.0, #TODO: This needs to be adjusted, along with the factor determined by power allocation. 
						operation_mode_id= self._ombc_next_operation_mode.id,
						abnormal_condition=False
					)

					logger.info("Instruction send: OMBC = {} for {}".format(self._ombc_next_operation_mode.diagnostic_label, self.unique_identifier))
					logger.info("Power-Claim: {}".format(self.power_claim))

					#Check, if this transition starts any timer. Only required if we leave a well known operation mode. 
					if self.ombc_active_operation_mode is not None:
						for t in self.ombc_system_description.transitions:
							if t.from_ == self.ombc_active_operation_mode.id and t.to == self._ombc_next_operation_mode.id:
								#transition found, timer required?
								for tmr in t.start_timers:
									#find the timer we need to start and start it. 
									for tmr_cand in self.ombc_system_description.timers:
										if tmr_cand.id == tmr:
											logger.info("Transition from '{}' to '{}' on {} causes a timer: '{}'. Timer started.".format(
												self.ombc_active_operation_mode.diagnostic_label, self._ombc_next_operation_mode.diagnostic_label,
												self.unique_identifier, tmr_cand.diagnostic_label
											))
											self.ombc_timers[tmr] = tmr_cand
											self.ombc_timer_starts[tmr] = datetime.now(timezone.utc)
											break

					self._s2_send_message(self.ombc_active_instruction)
					self.ombc_active_operation_mode = self._ombc_next_operation_mode
					self._ombc_next_operation_mode = None

					if USE_FAKE_BMS:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] @ {}".format(
							self.priority, self.rm_details.name, self.ombc_active_operation_mode.diagnostic_label
						))
				else:
					if USE_FAKE_BMS:
						self._reported_as_blocked = True
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] @ {} ({}s)".format(
							self.priority, self.rm_details.name, self.ombc_active_operation_mode.diagnostic_label, seconds_blocked
						))
			
			else:
				#No transition required. Make sure our BMS does not outline a blocking-timer information. 
				if USE_FAKE_BMS:
					if self._reported_as_blocked:
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(self._fake_bms_no), "/CustomName", "{}: {} [OMBC] @ {}".format(
							self.priority, self.rm_details.name, self.ombc_active_operation_mode.diagnostic_label
						))
						self._reported_as_blocked = False



	def _ombc_check_timer_block(self) ->float:
		"""
			Checks if there is a blocking timer, if there is, returns the amount of seconds to go. 
		"""
		#if the current mode is unknown, we have no block. 
		#if the next operation mode is unknown, it's no block at all.
		if self.ombc_active_operation_mode is None or self._ombc_next_operation_mode is None:
			return 0
		
		#attempting to transist between 2 operation modes. See, if there is a defined transition
		timer_to_invalidate = []
		for t in self.ombc_system_description.transitions:
			if t.from_ == self.ombc_active_operation_mode.id and t.to == self._ombc_next_operation_mode.id:
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
										
										logger.warning("Timer '{}' is preventing {} to transition from '{}' to '{}' currently. ({}s)".format(
											running_timer.diagnostic_label, self.unique_identifier, self.ombc_active_operation_mode.diagnostic_label, 
											self._ombc_next_operation_mode.diagnostic_label, seconds_remaining
										))
										return seconds_remaining
		
		for id in timer_to_invalidate:
			del self.ombc_timers[id]
			del self.ombc_timer_starts[id]

		return 0

	def pop_powerstats(self, now:datetime) -> tuple[PhaseAwareFloat, PhaseAwareFloat]:
		"""
			Returns a tuple of PhaseAwareFloats, where the first tuple is the power values and the second
			is the counters. Counters are reset after retrievel through this method. If the consumer
			does NOT provide power-measurements counters are estimated based on the current allowance
			calculated for the consumer.
		"""
		if self.rm_details is not None:
			if len(self.rm_details.provides_power_measurement_types) == 0:
				#Consumer does not provide measurements. So, we calculate an estimated power consumption
				#since the last call of pop_powerstats(). We use the claim per phase as it has been approved
				if self._last_pop_powerstats is not None:
					duration = (now - self._last_pop_powerstats).total_seconds
					for l in [1,2,3]:
						consumption = (self.power_request.by_phase[l] * duration / 3600.0) / 1000.0
						self._current_counter.by_phase[l] = consumption

		result = (self.current_power, self._current_counter)
		self._current_counter = PhaseAwareFloat()
		self._last_pop_powerstats = now
		return result

class HEMS(SystemCalcDelegate):
	#TODO: Refactor dateTime usage to _get_time everywhere, as this required for unit testing to time travel.
	_get_time = datetime.now

	def __init__(self):
		super(HEMS, self).__init__()
		self.system_type = SystemType.Unknown
		self.managed_rms: Dict[str, S2RMDelegate] = {}

		#consumption counters and momentary power values
		self.power_primary:PhaseAwareFloat = PhaseAwareFloat()
		self.power_secondary:PhaseAwareFloat = PhaseAwareFloat()
		self.counter_primary:PhaseAwareFloat = PhaseAwareFloat()
		self.counter_secondary:PhaseAwareFloat = PhaseAwareFloat()

		if USE_FAKE_BMS:
			self.available_fake_bms = [1,2,3,4,5,6,7,8,9]

	def set_sources(self, dbusmonitor, settings, dbusservice):
		super(HEMS, self).set_sources(dbusmonitor, settings, dbusservice)

		#load stored counters.
		for l in [1,2,3]:
			self.counter_primary.by_phase[l] = settings["hems_primary_l{}_forward".format(l)]
			self.counter_secondary.by_phase[l] = settings["hems_secondary_l{}_forward".format(l)]

		self._dbusservice.add_path('/HEMS/Active', value=0, gettextcallback=lambda p, v: Modes(v))
		self._dbusservice.add_path('/HEMS/BatteryReservation', value=0)
		self._dbusservice.add_path('/HEMS/BatteryReservationState', value=None)
		self._dbusservice.add_path('/HEMS/SystemType', value=0, gettextcallback=lambda p, v: SystemType(v))

		for l in [1,2,3]:
			self._dbusservice.add_path('/HEMS/PrimaryConsumer/Ac/L{}/Power'.format(l), value=None)
			self._dbusservice.add_path('/HEMS/PrimaryConsumer/Ac/L{}/Energy/Forward'.format(l), value=self.counter_primary.by_phase[l])
			self._dbusservice.add_path('/HEMS/SecondaryConsumer/Ac/L{}/Power'.format(l), value=None)
			self._dbusservice.add_path('/HEMS/SecondaryConsumer/Ac/L{}/Energy/Forward'.format(l), value=self.counter_secondary.by_phase[l])
		
		self._dbusservice.add_path('/HEMS/PrimaryConsumer/Ac/Power', value=None)
		self._dbusservice.add_path('/HEMS/PrimaryConsumer/Ac/Energy/Forward', value=self.counter_primary.total)
		self._dbusservice.add_path('/HEMS/SecondaryConsumer/Ac/Power', value=None)
		self._dbusservice.add_path('/HEMS/SecondaryConsumer/Ac/Energy/Forward', value=self.counter_secondary.total)

		self.system_type = self._determine_system_type()

		if self.mode == 1:
			self._enable()
		else:
			self._disable()

	def get_settings(self):
		# Settings for HEMS
		path = '/Settings/HEMS'
		#EnergyCounters are stored in settings. Values will be written every 15 min, meanwhile run out of memory.
		settings = [
			("hems_mode", path + "/Mode", 0, 0, 1),
			("hems_clinterval", path + "/ControlLoopInterval", 5, 1, 60),
			("hems_balancingthreshold", path + '/BalancingThreshold', 98, 2, 98),
			("hems_batteryreservation", path + '/BatteryReservationEquation', "15000.0 * (100.0-SOC)/100.0", "", ""),
			("hems_primary_l1_forward", path + "/Energy/Primary/L1/Forward", 0.0, 0.0, 999999.9),
			("hems_primary_l2_forward", path + "/Energy/Primary/L2/Forward", 0.0, 0.0, 999999.9),
			("hems_primary_l3_forward", path + "/Energy/Primary/L3/Forward", 0.0, 0.0, 999999.9),
			("hems_secondary_l1_forward", path + "/Energy/Secondary/L1/Forward", 0.0, 0.0, 999999.9),
			("hems_secondary_l2_forward", path + "/Energy/Secondary/L2/Forward", 0.0, 0.0, 999999.9),
			("hems_secondary_l3_forward", path + "/Energy/Secondary/L3/Forward", 0.0, 0.0, 999999.9),
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
				'/Settings/HEMS/Energy/Primary/L1/Forward',
				'/Settings/HEMS/Energy/Primary/L2/Forward',
				'/Settings/HEMS/Energy/Primary/L3/Forward',
				'/Settings/HEMS/Energy/Secondary/L1/Forward',
				'/Settings/HEMS/Energy/Secondary/L2/Forward',
				'/Settings/HEMS/Energy/Secondary/L3/Forward'
			]),
			('com.victronenergy.s2Mock', topic_list),
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
		logger.debug("Device added: {}".format(service))
		i = 0
		while True:
			s2_rm_exists = self._check_s2_rm(service, "/Devices/{}/S2".format(i))

			if s2_rm_exists:
				priority = self._dbusmonitor.get_value(service, "/Devices/{}/S2/Priority".format(i)) or 50
				ct_raw = self._dbusmonitor.get_value(service, "/Devices/{}/S2/ConsumerType".format(i))
				consumer_type = ConsumerType(1 if ct_raw is None else ct_raw)
				logger.debug("priority and ct: {} {}:{}".format(priority, ct_raw, consumer_type))
				delegate = S2RMDelegate(self._dbusmonitor, service, instance, i, priority, consumer_type, self)
				self.managed_rms[delegate.unique_identifier] = delegate
				logger.info("Identified S2 RM {} on {}. Added to managed RMs as {}".format(i, service, delegate.unique_identifier))
				delegate.begin()
				i += 1 #probe next one.
			else:
				break

	def device_removed(self, service, instance):
		logger.debug("Device removed: {}".format(service))

		#check, if this service provided one or multiple rm, we have been controlling. 
		known_rms = list(self.managed_rms.keys()) 
		for key in known_rms:
			if key.startswith(service):
				logger.info("Removing RM {} from managed RMs.".format(key))
				self.managed_rms[key].end()

				if USE_FAKE_BMS:
					if self.managed_rms[key]._fake_bms_no not in self.available_fake_bms:
						no= self.managed_rms[key]._fake_bms_no
						self.available_fake_bms.append(no)

						#reset that fake BMS to defaults.
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(no), "/Dc/0/Power", 0.0)
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(no), "/Dc/0/Soc", 0)
						self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_{}".format(no), "/CustomName", "HEMS Fake BMS {}".format(no))

				del self.managed_rms[key]

	def settings_changed(self, setting, oldvalue, newvalue):
		if setting == 'hems_mode':
			if oldvalue == 0 and newvalue == 1:
				self._enable()
			if oldvalue == 1 and newvalue == 0:
				self._disable()

	@property
	def mode(self):
		return self._settings['hems_mode']

	@property
	def control_loop_interval(self):
		return self._settings['hems_clinterval']

	@property
	def soc(self) -> float:
		"""
			current soc 0 - 100
		"""
		return BatterySoc.instance.soc
	
	@property
	def current_battery_reservation(self) -> float:
		"""
			returns the current desired battery reservation based on the user equation in watts.
			0 if error in equation. /HEMS/BatteryReservationState will indicate if there is an error with the equation,
			or if the reservation is lowered by BMS capabilities.
		"""
		reservation = 0.0
		try:
			reservation = round(eval(self._settings['hems_batteryreservation'].replace("SOC", str(self.soc))))
			capability = self.get_charge_power_capability()
			dess_charge = self._dbusservice["/DynamicEss/ChargeRate"]
			logger.info("Dess chargerate is {}".format(dess_charge))
			reservation_hint = "OK"
			if capability != None:
				if capability < reservation:
					reservation = capability
					reservation_hint = "BMS"

			# for now, only handle the case when DESS is issuing a positive chargerate.
			# having a lower chargerate issued than the calculated reservation otherwise would cause unused feedin.
			# TODO: When DESS is keeping the rate lower, this has the intention to feedin. What should hems do? 
			#       Possible Options (per Consumer?)
			#       - Steal that overhead? (For now, we do that by simply lowering our reservation to what DESS wants to charge.)
			#       - Stay "off" and let feedin happen?
			if dess_charge is not None and dess_charge > 0:
				if dess_charge < reservation:
					reservation = dess_charge
					reservation_hint = "DESS"
				
			self._dbusservice["/HEMS/BatteryReservationState"] = reservation_hint

			if USE_FAKE_BMS:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/CustomName", "Battery Reservation: {}W ({})".format(reservation, reservation_hint))

		except Exception as ex:
			reservation = 0.0
			self._dbusservice["/HEMS/BatteryReservationState"] = "ERROR"
		
		self._dbusservice["/HEMS/BatteryReservation"] = reservation
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
		self._timer = GLib.timeout_add(self.control_loop_interval * 1000, self._on_timer)
		self._timer_track_power = GLib.timeout_add(1 * 1000, self._on_timer_track_power)
		self._timer_save_counters = GLib.timeout_add(COUNTER_PERSIST_INTERVAL * 1000, self._on_timer_save_counters) #save counters every 15 min. 
		self._timer_retry_connections = GLib.timeout_add(CONNECTION_RETRY_INTERVAL * 1000, self._on_timer_retry_connection) #save counters every 15 min. 
		self._dbusservice["/HEMS/Active"] = 1
		logger.info("HEMS activated with a control loop interval of {}s".format(self.control_loop_interval))

	def _disable(self):
		self._dbusservice["/HEMS/Active"] = 0
		logger.info("HEMS deactivated.")

	def _determine_system_type(self) -> SystemType:
		#TODO: Determine current system kind. Required to distribute energy properly based on the type of the system.
		system_type = SystemType.Unknown
		try:
			no_phases = self._dbusservice["/Ac/Grid/NumberOfPhases"]
			grid_parallel = self._dbusservice["/Ac/ActiveIn/GridParallel"]
			multiphase_mode = self._dbusmonitor.get_value('com.victronenergy.settings', '/Settings/CGwacs/Hub4Mode')

			if grid_parallel is not None and grid_parallel == 1:
				#grid connected
				if no_phases == 1:
					system_type = SystemType.GridConnected1Phase
				elif no_phases == 2:
					if multiphase_mode == 1:
						system_type = SystemType.GridConnected2PhaseSaldating
					else:
						system_type = SystemType.GridConnected2PhaseIndividual
				elif no_phases == 3:
					if multiphase_mode == 1:
						system_type = SystemType.GridConnected3PhaseSaldating
					else:
						system_type = SystemType.GridConnected3PhaseIndividual

			else:
				#offgrid
				#TODO: Determine number of phases Offgrid, can't use grid information here. 
				pass
		except Exception as ex:
			logger.warning("Unable to determine SystemType by now. Retrying later...")
			#may happen during startup, until all delegates have populated their initial values. 
			pass

		self._dbusservice["/HEMS/SystemType"] = system_type.value
		return system_type
	
	def _on_timer_save_counters(self):
		try:
			for l in [1,2,3]:
				self._dbusmonitor.set_value("com.victronenergy.settings", "/Settings/HEMS/Energy/Primary/L{}/Forward".format(l), self.counter_primary.by_phase[l])
				self._dbusmonitor.set_value("com.victronenergy.settings", "/Settings/HEMS/Energy/Secondary/L{}/Forward".format(l), self.counter_secondary.by_phase[l])

			logger.debug("Saved transient counters: P: {}/{}/{} | S: {}/{}/{}".format(
				self.counter_primary.l1, self.counter_primary.l2, self.counter_primary.l3,
				self.counter_secondary.l1, self.counter_secondary.l2, self.counter_secondary.l3
			))

		except Exception as ex:
			logger.error("Exception saving counters", exc_info=ex)

		return True

	def _on_timer_retry_connection(self):
		for unique_identifier, delegate in self.managed_rms.items():
			if not delegate.initialized:
				logger.info("Retrying connection to {}".format(unique_identifier))
				delegate.begin()
		
		return True

	def _on_timer_track_power(self):
		try:
			self.power_primary = PhaseAwareFloat()
			self.power_secondary = PhaseAwareFloat()

			for unique_identifier, delegate in self.managed_rms.items():
				if delegate.initialized:
					values = delegate.pop_powerstats(self._get_time(timezone.utc))

					if delegate.consumer_type == ConsumerType.Primary:
						self.power_primary += values[0]
						self.counter_primary += values[1]

					elif delegate.consumer_type == ConsumerType.Secondary:
						self.power_secondary += values[0]
						self.counter_secondary += values[1]
			
			#dump on dbus
			for l in [1,2,3]:
				self._dbusservice["/HEMS/PrimaryConsumer/Ac/L{}/Power".format(l)] = self.power_primary.by_phase[l]
				self._dbusservice["/HEMS/PrimaryConsumer/Ac/L{}/Energy/Forward".format(l)] = self.counter_primary.by_phase[l]	
				self._dbusservice["/HEMS/SecondaryConsumer/Ac/L{}/Power".format(l)] = self.power_secondary.by_phase[l]
				self._dbusservice["/HEMS/SecondaryConsumer/Ac/L{}/Energy/Forward".format(l)] = self.counter_secondary.by_phase[l]

			self._dbusservice["/HEMS/PrimaryConsumer/Ac/Power"] = self.power_primary.total
			self._dbusservice["/HEMS/PrimaryConsumer/Ac/Energy/Forward"] = self.counter_primary.total	
			self._dbusservice["/HEMS/SecondaryConsumer/Ac/Power"] = self.power_secondary.total
			self._dbusservice["/HEMS/SecondaryConsumer/Ac/Energy/Forward"] = self.counter_secondary.total

		except Exception as ex:
			logger.error("Exception while publishing power records", exc_info=ex)

		return True

	def _on_timer(self):
		try:
			logger.debug("v------------------- LOOP -------------------v")
			# TODO: Add temporary performance counters for loop method, so we can figure out, how intense HEMS is.
			# Control loop timer.
			now = self._get_time()
			self.system_type = self._determine_system_type()
			available_overhead = self._get_available_overhead()

			logger.debug("SOC={}%, RSRV={}/{}W ({}), L1o={}W, L2o={}W, L3o={}W, dcpvo={}W, totalo={}W".format(
					self.soc,
					available_overhead.battery_rate,
					self.current_battery_reservation,
					self._dbusservice["/HEMS/BatteryReservationState"],
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

			#Iterate over all known RMs, check their requirement and assign them a suitable Budget. 
			#The RMDelegate is responsible to communicate with it's rm upon .comit() beeing called. 
			#(Will be called after finishing all power assignments to avoid instructions beeing send out immediately)
			#sort RMs by priority before iterating.
			for unique_identifier, delegate in sorted(self.managed_rms.items(), key=lambda i: i[1].priority):
				logger.debug("=============================================================================================================")  
				if delegate.initialized and delegate.rm_details is not None:
					if delegate.active_control_type is not None and delegate.active_control_type != ControlType.NOT_CONTROLABLE:
						logger.debug("===== RM {} ({}) is controllable: {} =====".format(unique_identifier, delegate.rm_details.name, delegate.active_control_type))	
						available_overhead = delegate.self_assign_overhead(available_overhead)
						logger.debug("==> Remaining overhead: {}".format(available_overhead))
					else:
						logger.debug("===== RM {} ({}) is uncontrollable: {} =====".format(unique_identifier, delegate.rm_details.name, delegate.active_control_type))	
				else:
					logger.debug("===== RM {} is not yet initialized. =====".format(unique_identifier))

			logger.debug("All assignments done. Comiting states.")
			for unique_identifier, delegate in self.managed_rms.items():
				if delegate.initialized:
					delegate.comit()
					
			logger.debug("SOC={}%, RSRV={}/{}W ({}), L1o={}W, L2o={}W, L3o={}W, dcpvo={}W, totalo={}W".format(
					self.soc,
					available_overhead.battery_rate,
					self.current_battery_reservation,
					self._dbusservice["/HEMS/BatteryReservationState"],
					available_overhead.power.l1,
					available_overhead.power.l2,
					available_overhead.power.l3,
					available_overhead.power.dc,
					available_overhead.power.total,
				)
			)

			if USE_FAKE_BMS:
				self._dbusmonitor.set_value("com.victronenergy.battery.hems_fake_0", "/Dc/0/Current", available_overhead.power.total)

			now2 = self._get_time()
			duration = (now2 - now).total_seconds() * 1000
			
			logger.info("Loop took {}ms".format(duration))
			logger.debug("^------------------- LOOP -------------------^")

			if (self.mode == 1):
				return True	#keep timer up as long as mode is enabled.
		except Exception as ex:
			logger.fatal("Exception during control loop", exc_info=ex)

		#terminate timer
		return False
	
	def _get_available_overhead(self)-> SolarOverhead:
		"""
			Calculates the available solar overhead. Returns SolarOverhead Object to be allocated based on system type. 
			If the systems state doesn't allow to determine a numeric overhead, None is returned, then the alternative algorithm has to be
			choosen, balancing on a soc-point.
		"""
		#TODO: On Offgrid / ZeroFeedIn Systems, we need to mimic increased DC overhead, when approaching the battery-soc limit to ensure solar is not throttled. 
		batrate = (self._dbusservice["/Dc/Battery/Power"] or 0)
		l1 = (self._dbusservice["/Ac/PvOnGrid/L1/Power"] or 0) + (self._dbusservice["/Ac/PvOnOutput/L1/Power"] or 0) - (self._dbusservice["/Ac/Consumption/L1/Power"] or 0)
		l2 = (self._dbusservice["/Ac/PvOnGrid/L2/Power"] or 0) + (self._dbusservice["/Ac/PvOnOutput/L2/Power"] or 0) - (self._dbusservice["/Ac/Consumption/L2/Power"] or 0)
		l3 = (self._dbusservice["/Ac/PvOnGrid/L3/Power"] or 0) + (self._dbusservice["/Ac/PvOnOutput/L3/Power"] or 0) - (self._dbusservice["/Ac/Consumption/L3/Power"] or 0)

		#now, we need to ADD power that is already beeing consumed by S2 Devices, because it will also be deducted in the Consumption-Values.
		#if the device is sourcing from DCPV it can be ignored, that will also be "ac-consumption" beeing reported, which we need to cancel out. 
		#current_power will still report AC consumption based on phase-allocation of the consumer. 
		for unique_identifier, delegate in self.managed_rms.items():
			if delegate.initialized:
				#FIXME: If a consumer is running manually, it's power should not be considered available overhread.
				#       Also, HEMS consumption counters should not count. -> implement delegate.is_hems_controlled()
				if delegate.current_power is not None:
					l1 += delegate.current_power.l1
					l2 += delegate.current_power.l2
					l3 += delegate.current_power.l3
		
		#DCPV Overhead is: Actual DC PV Power - every ac consumption that is not baked by ACPV.
		#finally, if there is no solar at all, dcpv overhead should be negative and equal the
		#battery discharge rate.
		dcpv = (self._dbusservice["/Dc/Pv/Power"] or 0) * AC_DC_EFFICIENCY #dcpv has a penalty when beeing turned into AC Consumption.

		if l1 < 0:
			dcpv -= abs(l1)
			l1=0
		
		if l2 < 0:
			dcpv -= abs(l2)
			l2=0

		if l3 < 0:
			dcpv -= abs(l3)
			l3=0
		
		#Init of SolarOverhead will take care to apply reservation constraints
		return SolarOverhead(
			round(l1, 1), 
			round(l2, 1), 
			round(l3, 1), 
			round(dcpv, 1),
			self.current_battery_reservation,
			batrate,
			4000, #TODO: Nominal Inverter Power(s). this needs to be queried "somehow".
			4000,
			4000,
			self
		)