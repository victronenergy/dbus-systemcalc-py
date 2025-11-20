#S2 imports
from s2python.common import (
	CommodityQuantity,
	PowerRange
)

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

	@classmethod
	def from_power_ranges(clazz, power_ranges:list[PowerRange], use_start_of_range=False):
		'''
			Creates a PhaseAwareFloat out of the given list of PowerRanges.
		'''
		res = PhaseAwareFloat()
		for pr in power_ranges:
			if pr.commodity_quantity == CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC:
				for l in [1,2,3]:
					res.by_phase[l] += (pr.end_of_range if not use_start_of_range else pr.start_of_range) / 3.0
			else:
				res.by_commodity[pr.commodity_quantity] += (pr.end_of_range if not use_start_of_range else pr.start_of_range)
		return res

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
