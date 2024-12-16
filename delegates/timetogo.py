from delegates.base import SystemCalcDelegate

class TimeToGo(SystemCalcDelegate):
    """ Calculates the TimeToGo for batteries. Taking the active soc limit into account will even be more precise than what the bms would report. """
    def __init__(self, sc):
        super(TimeToGo, self).__init__()
        self.systemcalc = sc
        self.capacity = None

    def set_sources(self, dbusmonitor, settings, dbusservice):
        SystemCalcDelegate.set_sources(self, dbusmonitor, settings, dbusservice)
        
    def get_output(self):
        return [('/Dc/Battery/TimeToGo', {'gettext': '%.0F s'})]

    def get_input(self):
       return [('com.victronenergy.settings', [
				'/Settings/DynamicEss/BatteryCapacity'])]

    def update_values(self, newvalues):
        #TODO: DM: For now DESS only, until battery capacity has a centralized place.
        #If we don't have the capacity available from dess, the Delegate will do nothing in update_values
        self.capacity = self._dbusmonitor.get_value("com.victronenergy.settings", '/Settings/DynamicEss/BatteryCapacity')
        if self.capacity is not None:
            self.capacity *= 1000
        else:
            #No dess capacity, do nothing.
            return
        
        ttg = None #for recalculation
        try:
            #get the values we need for that.
            battery_power = self._dbusservice['/Dc/Battery/Power']
            battery_soc = self._dbusservice['/Dc/Battery/Soc']
            active_soc_limit = self._dbusservice['/Control/ActiveSocLimit']

            if battery_power is not None and battery_soc is not None and active_soc_limit is not None:
                remaining_capacity = (active_soc_limit/100.0) * self.capacity
                missing_capacity = (1 - battery_soc/100.0) * self.capacity 
                current_capacity = (battery_soc/100.0) * self.capacity 
                usable_capacity = current_capacity - remaining_capacity

                if (battery_power < 0):
                    ttg = round((usable_capacity / battery_power) * 60 * 60 * - 1)
                elif (battery_power > 0):
                    ttg = round((missing_capacity / battery_power) * 60 * 60)
  
        except Exception:
            ttg = None
        
        newvalues['/Dc/Battery/TimeToGo'] = ttg
