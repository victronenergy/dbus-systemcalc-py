from datetime import datetime, date, time, timedelta
import sys

sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/s2')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')

#sys.path.insert(1, 'D:\GIT\dbus-systemcalc-py')
#sys.path.insert(1, 'D:\GIT\dbus-systemcalc-py/ext/s2')
#sys.path.insert(1, 'D:\GIT\dbus-systemcalc-py/ext/velib_python')

# our own packages
from delegates.ems import SolarOverhead, PhaseAwareFloat, SystemTypeFlag, AC_DC_EFFICIENCY
from s2python.common import CommodityQuantity
import logging

class LightDelegateMock():
	def __init__(self, system_type_flags:SystemTypeFlag):
		self.system_type_flags = system_type_flags

class TestHEMS():

	def __init__(self):
		#TestSystemCalcBase.__init__(self, methodName)
		pass

	def setUp(self):
		logging.getLogger().setLevel(logging.DEBUG)
		#TestSystemCalcBase.setUp(self)

	def tearDown(self):
		pass

	def run(self):
		logging.getLogger().info("Testing range probing.")
		self.test_claim_all_dynamic_max_fits()
		return

		logging.getLogger().info("Testing several claim-situations.")
		self.test_claim_fail_secondary_consumer()
		self.test_claim_fail_primary_consumer()
		self.test_claim_fail_reservation()
		self.test_claim_success_same_phase()
		self.test_claim_with_dc()
		self.test_claim_with_dc_and_xfer_saldating()
		self.test_claim_with_dc_and_xfer_individual()
		self.test_claim_with_dc_and_xfer2()
		self.test_claim_fail_symetric()
		self.test_claim_success_symetric()
		self.test_claim_success_symetric_dc()
		self.test_claim_success_symetric_dc_for_each()
		self.test_force_claim_over_budget()

	def assertEqual(self, left, right):
		if left==right:
			return True
		
		raise Exception("AssertionError, Objects not equal: '{}' vs '{}'".format(left,right))

	def test_claim_all_dynamic_max_fits(self):
		logging.getLogger().info("---")

		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)

		solar_overhead = SolarOverhead(
			l1 = 3000,
			l2 = 3000,
			l3 = 3000,
			dcpv= 0,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 0-1000 Watt on each phase, expecting success and 1000W on each phase.")

		solar_overhead.begin()
		power_claim_min = PhaseAwareFloat(0,0,0)
		power_claim_max = PhaseAwareFloat(1000,1000,1000)
		claim_success = solar_overhead.claim_range(power_claim_min, power_claim_max, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		self.assertEqual(solar_overhead.power_claim.l1, 1000)
		self.assertEqual(solar_overhead.power_claim.l2, 1000)
		self.assertEqual(solar_overhead.power_claim.l3, 1000)
		self.assertEqual(solar_overhead.power_claim.total, 3000)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))

	def test_claim_fail_secondary_consumer(self):
		logging.getLogger().info("---")

		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)

		solar_overhead = SolarOverhead(
			l1 = 200,
			l2 = 200,
			l3 = 200,
			dcpv= 200,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 1000 Watt on L1, expecting failure.")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, False)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
	
	def test_claim_fail_reservation(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)
		solar_overhead = SolarOverhead(
			l1 = 1200,
			l2 = 0,
			l3 = 0,
			dcpv= 0,
			reservation=500,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 1000 Watt on L1, expecting failure due to 500 Watt active reservation. ")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, False)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
	
	def test_claim_fail_primary_consumer(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)
		solar_overhead = SolarOverhead(
			l1 = 200,
			l2 = 200,
			l3 = 200,
			dcpv= 200,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 1000 Watt on L1, expecting failure.")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000)
		claim_success = solar_overhead.claim(powerclaim, True, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, False)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))

	def test_claim_success_same_phase(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)
		solar_overhead = SolarOverhead(
			l1 = 1000,
			l2 = 200,
			l3 = 200,
			dcpv= 200,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 1000 Watt on L1, expecting success.")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		self.assertEqual(solar_overhead.power_claim.total, 1000)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
	
	def test_claim_with_dc(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Individual
		)		
		solar_overhead = SolarOverhead(
			l1 = 800,
			l2 = 200,
			l3 = 200,
			dcpv= 200,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 1000 Watt on L1, expecting AC+DC claim to be enough (Individual Phasemode).")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		self.assertEqual(solar_overhead.power_claim.total, 1000)
		self.assertEqual(solar_overhead.power_claim.l1, 800)
		self.assertEqual(solar_overhead.power_claim.dc, 200)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
	
	def test_claim_with_dc_and_xfer_saldating(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)		
		solar_overhead = SolarOverhead(
			l1 = 800,
			l2 = 150,
			l3 = 0,
			dcpv= 100,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 1000 Watt on L1, expecting AC+DC+ACDCAC. Total claim now higher than 1000W. Saldating.")

		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		self.assertEqual(solar_overhead.power_claim.total, 950 + 50)
		self.assertEqual(solar_overhead.power_claim.l1, 800)
		self.assertEqual(solar_overhead.power_claim.dc, 50) #claim from dc is already normalized, no penalty.
		self.assertEqual(solar_overhead.power_claim.l2, 150)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))

	def test_claim_with_dc_and_xfer_individual(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Individual
		)		
		solar_overhead = SolarOverhead(
			l1 = 800,
			l2 = 200,
			l3 = 200,
			dcpv= 100,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 1000 Watt on L1, expecting AC+DC+ACDCAC. Total claim now higher than 1000W. Individual")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		
		self.assertEqual(solar_overhead.power_claim.l1, 800)
		self.assertEqual(solar_overhead.power_claim.dc, 100)
		self.assertEqual(solar_overhead.power_claim.l2, 100/AC_DC_EFFICIENCY**2)
		self.assertEqual(solar_overhead.power_claim.total, 900 + 100/AC_DC_EFFICIENCY**2) #100 Watt from ACDCAC will be penalized.
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
	
	def test_claim_with_dc_and_xfer2(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Individual
		)		
		solar_overhead = SolarOverhead(
			l1 = 800,
			l2 = 50,
			l3 = 200,
			dcpv= 100,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 1000 Watt on L1, expecting AC+DC+ACDCAC. Total claim now higher than 1000W. Claiming from 2 diff. phases. Individual")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		self.assertEqual(solar_overhead.power_claim.total, 900 + 100/AC_DC_EFFICIENCY**2) #100 Watt from ACDCAC will be penalized.
		self.assertEqual(solar_overhead.power_claim.l1, 800) #no penalty, direct claim.
		self.assertEqual(solar_overhead.power_claim.dc, 100) #no penalty, direct claim, dc is normalized.
		self.assertEqual(solar_overhead.power_claim.l2, 50) #we can claim 50 watt of l2. 
		#However, that will only deduct 50 / penalty from our needs.
		# so, the remaining claim to be done on phase 3 is :
		rem_claim = 1000 - 800 - 100 - (50*(AC_DC_EFFICIENCY**2))
		self.assertEqual(solar_overhead.power_claim.l3, rem_claim/AC_DC_EFFICIENCY**2)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))

	def test_claim_fail_symetric(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)
		solar_overhead = SolarOverhead(
			l1 = 1000,
			l2 = 1000,
			l3 = 900,
			dcpv= 0,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 3000 Watt symmetric, expecting failure.")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000,1000,1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, False)
		self.assertEqual(solar_overhead.power_claim.total, 2900)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
	
	def test_claim_success_symetric(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)
		solar_overhead = SolarOverhead(
			l1 = 1000,
			l2 = 1000,
			l3 = 1000,
			dcpv= 0,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 3000 Watt symmetric, expecting success.")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000,1000,1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		self.assertEqual(solar_overhead.power_claim.total, 3000)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
	
	def test_claim_success_symetric_dc(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)
		solar_overhead = SolarOverhead(
			l1 = 1000,
			l2 = 1000,
			l3 = 100,
			dcpv= 900,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 3000 Watt symmetric, expecting success with 900 dc to 1 phase.")

		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000,1000,1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		self.assertEqual(solar_overhead.power_claim.total, 3000)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
	
	def test_claim_success_symetric_dc_for_each(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)
		solar_overhead = SolarOverhead(
			l1 = 800,
			l2 = 700,
			l3 = 600,
			dcpv= 900,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 3000 Watt symmetric, expecting success with 900 dc to 3 phases.")

		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000,1000,1000)
		claim_success = solar_overhead.claim(powerclaim, False, False)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		self.assertEqual(solar_overhead.power_claim.total, 3000)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))

	def test_force_claim_over_budget(self):
		logging.getLogger().info("---")
		light_delegate_mock = LightDelegateMock(
			SystemTypeFlag.ThreePhase | SystemTypeFlag.GridConnected | SystemTypeFlag.Saldating
		)
		solar_overhead = SolarOverhead(
			l1 = 200,
			l2 = 0,
			l3 = 0,
			dcpv= 200,
			reservation=0,
			battery_rate=0,
			inverterPowerL1=5000,
			inverterPowerL2=5000,
			inverterPowerL3=5000,
			delegate=light_delegate_mock
		)

		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))
		logging.getLogger().info("Claiming 1000 Watt on L1, forcing, expecting increased dc claim.")

		
		solar_overhead.begin()
		powerclaim = PhaseAwareFloat(1000)
		claim_success = solar_overhead.claim(powerclaim, False, True)

		logging.getLogger().info("Claim Success? {}".format(claim_success))
		self.assertEqual(solar_overhead.transaction_running, True)
		self.assertEqual(claim_success, True)
		self.assertEqual(solar_overhead.power_claim.total, 1000)
		self.assertEqual(solar_overhead.power_claim.l1, 200)
		self.assertEqual(solar_overhead.power_claim.dc, 800)
		logging.getLogger().info("Claim-Result: {}".format(solar_overhead.power_claim))
		logging.getLogger().info("SolarOverhead: {}".format(solar_overhead))

if __name__ == '__main__':
	logging.basicConfig(format='%(asctime)s,%(msecs)d %(levelname)s %(message)s',
		datefmt='%Y-%m-%d %H:%M:%S',
		level=logging.DEBUG,
		handlers=[
			logging.StreamHandler()
		])
	
	#Set HEMS to debug logging, so we get more sophisticated log output.
	logging.getLogger("hems_logger").setLevel(logging.DEBUG)
	
	test = TestHEMS()
	test.run()