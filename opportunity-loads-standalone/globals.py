from helper import Configurable

HUB4_SERVICE = "com.victronenergy.hub4"
S2_IFACE = "com.victronenergy.S2"
KEEP_ALIVE_INTERVAL_S = 30 #seconds
COUNTER_PERSIST_INTERVAL_MS = 60000 #milli-seconds
CONNECTION_RETRY_INTERVAL_MS = 90000 #milli-seconds
INVERTER_LIMIT_MONITOR_INTERVAL_MS = 250 #milli-seconds
AC_DC_EFFICIENCY = 0.925 #Experimental Value.
USE_FAKE_BMS = True

CONFIGURABLES:list[Configurable] = []
C_MODE = Configurable('/Mode', '/Settings/OpportunityLoads/Mode', 'ems_mode', 0, 0, 1, CONFIGURABLES)
C_BALANCING_THRESHOLD = Configurable('/BalancingThreshold', '/Settings/OpportunityLoads/BalancingThreshold', 'ems_balancingthreshold', 98, 2, 98, CONFIGURABLES)
C_RESERVATION_BASE_POWER = Configurable('/ReservationBasePower', '/Settings/OpportunityLoads/ReservationBasePower', 'ems_battery_base', 10000.0, 0.0, 100000.0, CONFIGURABLES)
C_RESERVATION_DECREMENT = Configurable('/ReservationDecrement', '/Settings/OpportunityLoads/ReservationDecrement', 'ems_battery_decrement', 100.0, 0.0, 100000.0, CONFIGURABLES)
C_RESERVATION_EQUATION = Configurable(None, '/Settings/OpportunityLoads/BatteryReservationEquation', 'ems_batteryreservation', "RBP - SOC * RD","","", CONFIGURABLES)
C_CONTINIOUS_INVERTER_POWER = Configurable(None, '/Settings/OpportunityLoads/ContinuousInverterPower', 'ems_cip', 30000.0, 0.0, 300000.0, CONFIGURABLES)
C_CONTROL_LOOP_INTERVAL = Configurable(None, '/Settings/OpportunityLoads/ControlLoopInterval', 'ems_clinterval', 5, 5, 15, CONFIGURABLES)
C_PRIORITY_MAPPING = Configurable(None, '/Settings/OpportunityLoads/PriorityMapping', 'ems_pmap', "{\"battery\":0}", "", "", CONFIGURABLES, True)
