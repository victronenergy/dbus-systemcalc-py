#!/usr/bin/env python
 
# imports
import sys
import os
import logging
import os
import platform
import uuid
import requests #type:ignore
import asyncio
from datetime import datetime, timedelta, timezone
import json
from builtins import Exception, int, str
from concurrent.futures import ThreadPoolExecutor
from typing import Dict
import signal

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler(sys.stdout))

sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/s2')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/aiovelib')

if sys.version_info.major == 2:
    import gobject # type: ignore
else:
    from gi.repository import GLib as gobject # type: ignore

from dbusmonitor import DbusMonitor # type: ignore
from dbus.mainloop.glib import DBusGMainLoop # type: ignore
from aiovelib.service import IntegerItem, TextItem, DoubleItem
from aiovelib.service import Service
import asyncio_glib #type:ignore
asyncio.set_event_loop_policy(asyncio_glib.GLibEventLoopPolicy())

#s2 related stuff
from s2 import S2ResourceManagerItem
from s2python.s2_connection import AssetDetails

from s2python.s2_control_type import (
    S2ControlType, 
    OMBCControlType, 
    NoControlControlType
)

from s2python.common import (
    CommodityQuantity,
    Role,
    RoleType,
    Commodity,
    Duration,
    ReceptionStatusValues,
    ResourceManagerDetails,
    NumberRange,
    PowerRange,
    Transition,
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

DBusGMainLoop(set_as_default=True)
EVCS_SERVICE = "com.victronenergy.evcharger"
PHASE_MODE_CONFIG = 3

class OMBCT(OMBCControlType):
    def __init__(self, rm_item:S2ResourceManagerItem):
        self.rm_item = rm_item
        self.active_operation_mode = None

        super().__init__()

    def activate(self, conn):
        logger.info("OMBC activated.")
        #nothing todo here, loop will handle.
    
    def deactivate(self, conn):
        #reset, so loop is resending information, when OMBC is enabled.
        #that is implemented in the loop, because the car state may change
        #during OMBC beeing active as well.
        self.rm_item.car_connected = None 
        self.rm_item.evcs_status = None
        logger.info("OMBC deactivated.")
    
    async def handle_instruction(self, conn, msg, send_okay):
        try:
            # Generally the EMS is supposed to send a instruction ONCE, then the RM has to report back with the proper state
            # so the EMS is becoming aware, that the state change was successfull. For the EVCS Mock, there is eventually an issue
            # with values not beeing forwarded from dbus to modbus every time. While this isn't critical for amp-switches (they will
            # appear often, loosing one just has a tiny precision impact) for on/off toggling it is critical to be identified correctly. 
            # Thus, for these states, our only option is the following: 
            # - When a instruction comes in, we reset the "confirm_state_change" value and try to confirm it through power values (will fail first time). 
            # - The same instruction will come in again, we do the same actions again and try to confirm it again. Should eventually succeed, else repeat.
            # - Once it finally is confirmed, EMS will stop to send the same instruction again.
            confirm_state_change = False
            confirmed_state = None
            prior_id = "{}".format(self.active_operation_mode.id) if self.active_operation_mode is not None else None
            for op_mode in self.rm_item.system_description.operation_modes:
                if op_mode.id == msg.operation_mode_id:
                    confirmed_state = op_mode
                    logger.info("Instruction received: {}".format(op_mode.diagnostic_label))
                    break

            #Here we actually do, what we are supposed to do. Reporting Power is handled by loop.
            if msg.operation_mode_id == self.rm_item.stand_by_id or msg.operation_mode_id == self.rm_item.no_car_id:
                #if charging, stop.
                if (self.rm_item.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L1/Power") or 0) > 0:
                    logger.info("Sending Stop (P>0W on L1 detected)!")
                    self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/StartStop", False)
                else:
                    #0 power, we can confirm the state now.
                    confirm_state_change = True
            else:
                #check, if a chargemode is selected - then verify the EVCS is running and select the proper amps.
                amps = self.rm_item.charge_mode_map[msg.operation_mode_id]
                if amps is not None:
                    logger.info("Setting amps to {}".format(amps))
                    self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/SetCurrent", amps)

                    #verify we are charging, else send Start.
                    if (self.rm_item.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L1/Power") or 0) == 0:
                        logger.info("Sending Start (0W on L1 detected)!")
                        self.rm_item.dbus_monitor.set_value(EVCS_SERVICE, "/StartStop", True)
                    else:
                        #As soon as we have ANY power, we can confirm charging. Whether the very detailed amp setting was 
                        #transferred correctly, we can't tell easily, ignore that.
                        confirm_state_change = True
            
            #first confirm reception and processing.
            await send_okay

            #then, confirm state change, if confirmed through power readings. 
            logger.info("State confirm is: {}".format(confirm_state_change))
            if confirm_state_change:
                self.active_operation_mode = confirmed_state
                await self.rm_item.send_msg_and_await_reception_status(
                    OMBCStatus(
                        message_id=uuid.uuid4(),
                        active_operation_mode_id="{}".format(self.active_operation_mode.id),
                        previous_operation_mode_id=prior_id,
                        transition_timestamp=datetime.now(timezone.utc),
                        operation_mode_factor=msg.operation_mode_factor
                    )
                )

        except Exception as ex:
            logger.error("Error in handle_instructions", exc_info=ex)

class CTNOCTRL(NoControlControlType):
    def __init__(self, rm_item:S2ResourceManagerItem):
        self.rm_item = rm_item
        super().__init__()

    def activate(self, conn):
        logger.info("NOCTRL activated.")
        self.system_description=None
        self.on_id=None
        self.off_id=None
        return super().activate(conn)
    
    def deactivate(self, conn):
        logger.info("NOCTRL deactivated.")
        return super().deactivate(conn)

class RM0(S2ResourceManagerItem):        
    def __init__(self, path:str, asset_details:AssetDetails, service:Service):
        self.ct_ombc = OMBCT(self)    
        self.ct_noctrl = CTNOCTRL(self)
        self.s2_path = path
        self.asset_details = asset_details
        self.service = service
        self.car_connected = None
        self.charge_mode_map = {}
        self.evcs_status = None
        self.stand_by_id = uuid.uuid4()
        self.no_car_id = uuid.uuid4()
        self.charged_id = uuid.uuid4()
        self.on_off_timer_id = uuid.uuid4()
        self.on_off_timer_always_id = uuid.uuid4()
        self.amp_switch_timer_id_10 = uuid.uuid4()
        self.amp_switch_timer_id_30 = uuid.uuid4()

        #we don't need a change handler, we read when required.
        dummy = {'code': None, 'whenToLog': 'configChange', 'accessLevel': None}
        self.dbus_monitor = DbusMonitor({
            "com.victronenergy.evcharger" : {
                "/Mode" : dummy,
                "/Status" : dummy,
                "/StartStop" : dummy,
                "/SetCurrent" : dummy,
                "/Ac/L1/Power" : dummy,
                "/Ac/L2/Power" : dummy,
                "/Ac/L3/Power" : dummy
            }
        })

        super().__init__(self.s2_path, [self.ct_noctrl, self.ct_ombc], self.asset_details)

        #find the ev charger, we have: 
        for (sn, instance) in self.dbus_monitor.get_service_list().items():
            if (sn.startswith("com.victronenergy.evcharger")):
                logger.info("Found EVCS: {}".format(sn))
                global EVCS_SERVICE
                EVCS_SERVICE = sn

    def _dbusValueChanged(self, dbusServiceName, dbusPath, dict, changes, deviceInstance):
        try:
            logger.info(self, "Change on dbus for {0} (new value: {1})".format(dbusServiceName, changes['Value'])) 
        except Exception as ex:
            logger.error("Exception", exc_info=ex)

    async def loop(self):
        try:
            if self.is_connected:
                # check if the EVCS status has changed, based on that, we may need to offer different OMBC states available. 
                evcs_status = self.dbus_monitor.get_value(EVCS_SERVICE, "/Status")
                
                if (evcs_status != self.evcs_status):
                    logger.info("EVCS status has changed to: {}".format(evcs_status))
                    old_evcs_status = self.evcs_status
                    self.evcs_status = evcs_status
                    
                    #has changed, determine a suitable model to be send. 
                    if (self.evcs_status == 3):
                        await self.enter_charged_state()

                    elif self.evcs_status == 0 or self.evcs_status is None:
                        await self.enter_nocar_state()

                    elif (old_evcs_status is None or old_evcs_status==0 or old_evcs_status==3):
                        await self.enter_operational_state()

                #report power while in OMBC mode.
                if self._current_control_type == self.ct_ombc:
                    l1_power = self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L1/Power") or 0.0
                    l2_power = self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L2/Power") or 0.0
                    l3_power = self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L3/Power") or 0.0
                    set_current = self.dbus_monitor.get_value(EVCS_SERVICE, "/SetCurrent") or 0

                    logger.info("Reporting power: {}/{}/{} (Current: {})".format(l1_power, l2_power, l3_power, set_current))

                    await self.send_msg_and_await_reception_status(
                        PowerMeasurement(
                            message_id=uuid.uuid4(),
                            measurement_timestamp=datetime.now(timezone.utc),
                            values = [
                                PowerValue(
                                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1,
                                    value=l1_power
                                ),
                                PowerValue(
                                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L2,
                                    value=l2_power
                                ),
                                PowerValue(
                                    commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L3,
                                    value=l3_power
                                )
                            ]
                        )
                    )
            else:
                logger.warning("No S2 connection. ZzzZzz")

                #reset some props to make sure, we requery, once connection is up again. 
                self.evcs_status = None
                self.car_connected = None

                #If S2 Control is lost, also stop charging. 
                l1_power = self.dbus_monitor.get_value(EVCS_SERVICE, "/Ac/L1/Power") or 0.0
                if l1_power > 0:
                    logger.warning("Sending Connection-Loss-Stop to EV (P>0W on L1 detected)!")
                    self.dbus_monitor.set_value(EVCS_SERVICE, "/StartStop", False)
                
        except Exception as ex:
            logger.error("Exception in loop: ", exc_info=ex)

    async def enter_operational_state(self):
        #Car state is anything but disconnected / fully charged. Offer Chargemodes.
        #This should only be send, when comming from 0 or 3 state,  
       
        logger.info("Car connected. Offering Standby and a State per Amp")

        operation_modes_temp=[
            OMBCOperationMode(
                id=self.stand_by_id,
                diagnostic_label="Standby",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    )
                ]
            )
        ]

        #EVCS would get this from settings. (Charging with 6-32A)
        self.charge_mode_map.clear()
        com_q = CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC if PHASE_MODE_CONFIG == 3 else CommodityQuantity.ELECTRIC_POWER_L1
        for a in range(6,17):
            op_mode_id = uuid.uuid4()
            self.charge_mode_map[op_mode_id] = a
            operation_modes_temp.append(
                OMBCOperationMode(
                    id=op_mode_id,
                    diagnostic_label="{} A".format(a),
                    abnormal_condition_only=False,
                    power_ranges=[
                        PowerRange(
                            start_of_range = a * 240 * PHASE_MODE_CONFIG,
                            end_of_range = a * 240 * PHASE_MODE_CONFIG,
                            commodity_quantity = com_q
                        )
                    ]
                )   
            )
        
        timers_temp = [
            Timer(
                id = self.on_off_timer_id,
                diagnostic_label="On/Off Hysteresis 300s",
                duration=300*1000
            ),
            Timer(
                id = self.on_off_timer_always_id,
                diagnostic_label="On/Off Hysteresis 60s",
                duration=60*1000
            ),
            Timer(
                id = self.amp_switch_timer_id_10,
                diagnostic_label="Amp Switch Delay 10s",
                duration=10*1000
            ),
            Timer(
                id = self.amp_switch_timer_id_30,
                diagnostic_label="Amp Switch Delay 30s",
                duration=30*1000
            )
        ]

        #Now, we need the transitions. We want a 15 second delay for adjusting the ChargeCurrent. 
        #And we want a 5 Minute histerysis between Standby and any other state. 
        #Transitions should onl be possible: Standby <-> 6 <-> 7 <-> ... <-> 15 <-> 16
        #(So the EVCS can't jump from 6 to 16 or vice versa during operation.)
        transitions_temp = []
        for left in operation_modes_temp:
            for right in operation_modes_temp:
                if left.id != right.id: #transitions to self, we don't need.
                    if left.id == self.stand_by_id:
                        #transition to from standby, use 5 min hysteresis.
                        #only legit to the 6A State.
                        if self.charge_mode_map[right.id] == 6:
                            logger.info("Creating transition from '{}' to '{}' with 5min Hysteresis".format(left.diagnostic_label, right.diagnostic_label))
                            transitions_temp.append(
                                Transition(id=uuid.uuid4(),
                                    from_=left.id,
                                    to=right.id,
                                    start_timers=[self.on_off_timer_id],
                                    blocking_timers=[self.on_off_timer_id],
                                    transition_duration=10000,
                                    abnormal_condition_only=False,
                                    transition_costs=None
                                ) 
                            )
                    elif right.id == self.stand_by_id:
                        #transition to standby, use 5 min hysteresis.
                        #also blocked by the 60s On/Off Hysteresis. Note: The on_off_timer_always_id is started, when ANY State transitions into 6A
                        #to achieve a final "resting" in 6A for 60s before finally switching to standby (off)
                        #only legit to the 6A State.
                        if self.charge_mode_map[left.id] == 6:
                            logger.info("Creating transition from '{}' to '{}' with 5min Hysteresis".format(left.diagnostic_label, right.diagnostic_label))
                            transitions_temp.append(
                                Transition(
                                    id=uuid.uuid4(),
                                    from_=left.id,
                                    to=right.id,
                                    start_timers=[self.on_off_timer_id],
                                    blocking_timers=[self.on_off_timer_id, self.on_off_timer_always_id],
                                    transition_duration=10000,
                                    abnormal_condition_only=False,
                                    transition_costs=None
                                ) 
                            )
                    else:
                        #transition between ampstates, use 30 or 10 seconds hysteris.
                        left_amp = self.charge_mode_map[left.id]
                        right_amp = self.charge_mode_map[right.id]

                        start_timers = []
                        blocking_timers = []
                        
                        #1A step? 
                        if (abs(left_amp - right_amp) == 1):
                            start_timers.append(self.amp_switch_timer_id_10)
                            blocking_timers.append(self.amp_switch_timer_id_10)
                        else:
                            start_timers.append(self.amp_switch_timer_id_30)
                            blocking_timers.append(self.amp_switch_timer_id_30)

                        #if the target state is 6A, we additionally have to start the on_off_timer_always_id timer.
                        if (right_amp == 6):
                            start_timers.append(self.on_off_timer_always_id)

                        logger.info("Creating transition from '{}' to '{}' with {} start-timers".format(left.diagnostic_label, right.diagnostic_label, len(start_timers)))
                        transitions_temp.append(
                            Transition(
                                id=uuid.uuid4(),
                                from_=left.id,
                                to=right.id,
                                start_timers=start_timers,
                                blocking_timers=blocking_timers,
                                transition_duration=2000,
                                abnormal_condition_only=False,
                                transition_costs=None
                            ) 
                        )

        self.system_description = OMBCSystemDescription(
            message_id=uuid.uuid4(),
            valid_from=datetime.now(timezone.utc),
            operation_modes=operation_modes_temp,
            transitions=transitions_temp,
            timers=timers_temp
        )

        await self.send_msg_and_await_reception_status(self.system_description)

        await self.send_msg_and_await_reception_status(
            OMBCStatus(
                message_id=uuid.uuid4(),
                active_operation_mode_id="{}".format(self.stand_by_id),
                operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
            )
        )

    async def enter_nocar_state(self):
        #Car has just disconnected, only offer no car. 
        operation_modes_temp=[
            OMBCOperationMode(
                id=self.no_car_id,
                diagnostic_label="No Car",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    )
                ]
            )
        ]

        #Control Type has been selected by CEM. Advertise OperationModes.
        self.system_description = OMBCSystemDescription(
            message_id=uuid.uuid4(),
            valid_from=datetime.now(timezone.utc),
            operation_modes=operation_modes_temp,
            transitions=[],
            timers=[]
        )

        logger.info("Only offering 'NoCar' Mode.")
        await self.send_msg_and_await_reception_status(
            self.system_description
        )

        await self.send_msg_and_await_reception_status(
            OMBCStatus(
                message_id=uuid.uuid4(),
                active_operation_mode_id="{}".format(self.no_car_id),
                operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
            )
        )

        for opm in self.system_description.operation_modes:
            if opm.id == self.no_car_id:
                self.active_operation_mode = opm

    async def enter_charged_state(self):
        operation_modes_temp=[
            OMBCOperationMode(
                id=self.charged_id,
                diagnostic_label="Charged",
                abnormal_condition_only=False,
                power_ranges=[
                    PowerRange(
                        start_of_range=0,
                        end_of_range=0,
                        commodity_quantity=CommodityQuantity.ELECTRIC_POWER_L1
                    )
                ]
            )
        ]

        self.system_description = OMBCSystemDescription(
            message_id=uuid.uuid4(),
            valid_from=datetime.now(timezone.utc),
            operation_modes=operation_modes_temp,
            transitions=[],
            timers=[]
        )

        logger.info("Only offering 'Charged' Mode.")
        await self.send_msg_and_await_reception_status(self.system_description)

        await self.send_msg_and_await_reception_status(
            OMBCStatus(
                message_id=uuid.uuid4(),
                active_operation_mode_id="{}".format(self.charged_id),
                operation_mode_factor=1.0, # hmmm? doesn't matter at this point.
            )
        )

        #set current mode.
        for opm in self.system_description.operation_modes:
            if opm.id == self.charged_id:
                self.active_operation_mode = opm

class EVCSMock(Service):
    
    def __init__(self, bus, name, instance):
        self.instance = instance
        super().__init__(bus, "{}.m_{}".format(name, instance))

    def setup_rm0(self):
        self.rm0 = RM0(
            '/Devices/0/S2', 
            AssetDetails(
                uuid.uuid4(),
                False,
                [CommodityQuantity.ELECTRIC_POWER_3_PHASE_SYMMETRIC],
                Duration.from_milliseconds(5000),
                [Role(role=RoleType.ENERGY_CONSUMER, commodity=Commodity.ELECTRICITY)],
                None,
                "EVCS",
                "Victron EVCS",
                "22kW EVCS",
                "1.0",
                "1337"
            ), self)
        
        self.add_item(self.rm0)

    async def _loop(self):
        while True:
            await asyncio.sleep(2)
            await self.rm0.loop()

def configure_logger():
    from logging.handlers import TimedRotatingFileHandler
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

if __name__ == "__main__":
    try:
        from dbus_fast.aio import MessageBus
        from dbus_fast.constants import BusType
    except ImportError:
        from dbus_next.aio import MessageBus
        from dbus_next.constants import BusType

    async def main():
        from dbus.mainloop.glib import DBusGMainLoop # type: ignore
        DBusGMainLoop(set_as_default=True)

        configure_logger()

        def restart_service(signum, frame):
            logger.info("Received sigterm, ending service.")
            sys.exit(0)

        signal.signal(signal.SIGTERM, restart_service)

        instance = 920
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        service = EVCSMock(bus, 'com.victronenergy.switch', instance)
        
        service.add_item(TextItem("/ProductName", "ShellyMock"))
        service.add_item(IntegerItem("/DeviceInstance", instance))
        service.add_item(TextItem("/Mgmt/ProcessVersion", "1.0"))
        service.add_item(TextItem("/Mgmt/Connection", "dbus via aiovelib"))
        service.add_item(IntegerItem("/Connected", 1))
        service.add_item(TextItem('/CustomName', "Mock {}".format(instance)))

        service.add_item(IntegerItem('/Devices/0/DeviceInstance', instance))
        service.add_item(TextItem('/Devices/0/ServiceName', service.name))
        service.add_item(TextItem('/Devices/0/CustomName', None))
        service.add_item(TextItem('/Devices/0/IpAddress', None))
        service.add_item(IntegerItem('/Devices/0/Notification', 0))

        #Some to be discussed items. Suspect to User-Configuration.
        #Actual (generic) rm may use more here, like Power and Phase(s) used by the RSS.
        #(Because a generic RM controlling a shelly / switchable output can't know.)
        service.add_item(IntegerItem('/Devices/0/S2/Priority', 25)) #Priority , EMS will read
        service.add_item(IntegerItem('/Devices/0/S2/ConsumerType', 1)) # 0=primary load, 1=secondary load, EMS will read
        service.setup_rm0()

        #finally register our service.
        await service.register()
        asyncio.get_event_loop().create_task(service._loop())
        
        await service.bus.wait_for_disconnect()
    
    asyncio.get_event_loop().run_until_complete(main())