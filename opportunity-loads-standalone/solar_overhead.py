import logging
from phaseawarefloat import PhaseAwareFloat
from helper import SystemTypeFlag
from globals import AC_DC_EFFICIENCY

logger = logging.getLogger("opportunity-loads")

class SolarOverhead():
	def __init__(self, l1:float, l2:float, l3:float, dcpv:float, reservation:float, battery_rate:float, 
			  inverterPowerL1:float, inverterPowerL2:float, inverterPowerL3:float, opportunity_loads):
		self.power:PhaseAwareFloat = PhaseAwareFloat(l1,l2,l3,dcpv)
		self.inverterPower:PhaseAwareFloat = PhaseAwareFloat(inverterPowerL1, inverterPowerL2, inverterPowerL3)
		self._prior_power:PhaseAwareFloat = None
		self.power_claim:PhaseAwareFloat = None
		self.power_request:PhaseAwareFloat = None
		self._opportunity_loads = opportunity_loads
		self.battery_rate = battery_rate
		self.battery_reservation = reservation
		self.transaction_running = False

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
	
	def claim_range(self, power_request_min:PhaseAwareFloat, power_request_max:PhaseAwareFloat, primary:bool, force:bool=False)->bool:
		"""
			claims the maximum power on variable power requests. Returns true on success, false on error. If the requirements of an RM are satisfied,
			call comit() which returns a PhaseAwareFloat representing the powerclaim of the transaction.
		"""
		if not self.transaction_running:
			raise Exception("No Solar Claim Transaction currently running. Need to call begin() before claiming power.")
		
		#this is the tricky part: At least one phase is requesting a value within a min/max range.
		#it may be as worse as all 3 phases requesting dynamic values. So, we have to find the MPP
		#among all possible combinations. Since claiming energy from either source will affect the
		#availability on other sources, we cannot probe each phase individually. We need to generate
		#a (reasonable) amount of permutations within a pre-selected range and check, which one fits best.
	
		#first, check the all-max case. if that works out, we are already done and found the highest possible range.
		max_fits = self.claim(power_request_max, primary, force)
		if max_fits:
			return True
		
		self.rollback()
		#didn't fit. So, let's start probing.

		return False

	def claim(self, power_request:PhaseAwareFloat, primary:bool, force:bool=False)->bool:
		"""
			Claims a bunch of power. Returns true on success, false on error. If the requirements of an RM are satisfied,
			call comit() which returns a PhaseAwareFloat representing the powerclaim of the transaction.
		"""
		if not self.transaction_running:
			raise Exception("No Solar Claim Transaction currently running. Need to call begin() before claiming power.")
		
		#First, start to determine the actual amount we want to claim. It needs to be between min and max, as close to max as possible.
		#Also check, if reservation needs to be applied for this claim. If there is enough "total", we can drive the consumer. 
		#The claim however may source from any available Power theren is.
		
		#now, deduct energy from the proper source. We start by allocating direct ACPV.
		claim_target = self._try_claim_ac(PhaseAwareFloat.from_phase_aware_float(power_request))
		
		if claim_target.total > 0:
			#Based on the system type we now proceed with DC or ACDCAC. If the system has a saldating measurement method,
			#We can claim ACDCAC lossless, so prefer that. Any other case preferably uses DC first.
		
			if SystemTypeFlag.Saldating in self._opportunity_loads.system_type_flags:
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
		#Deny primaries unless the resulting overhead total is greater than 50 Watts. (To avoid some extensive on/off flickering)
		if (not force and primary and not self.power.total > 50):
			logger.debug("-- Claiming {}W (primary) would violate Consumption reservation. Rejecting.".format(self.power_claim.total))
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
	#				logger.debug("-- Claiming {}W would violate continuous inverter power. Rejecting.".format(self.power_claim.total))
	#				return False
	#		else:
	#			for l in [1,2,3]:
	#				if ((self._delegate._dbusservice["/Ac/Consumption/L{}/Power".format(l)] or 0) + self.power_claim.by_phase[l] > 
	#					self._delegate.continuous_inverter_power_per_phase + (self._delegate._dbusservice["/Ac/PvOnGrid/L{}/Power".format(l)] or 0) + 
	#					(self._delegate._dbusservice["/Ac/PvOnOutput/L{}/Power".format(l)] or 0)):
	#					logger.debug("-- Claiming {}W on L{} would violate continuous inverter power. Rejecting.".format(self.power_claim.total, l))
	#					return False
					
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
