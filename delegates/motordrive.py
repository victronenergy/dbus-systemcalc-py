from delegates.base import SystemCalcDelegate

PREFIX = "/MotorDrive"


class MotorDrive(SystemCalcDelegate):
    """Collect electric motor drive data."""

    def get_input(self):
        return [
            (
                "com.victronenergy.motordrive",
                ["/Dc/0/Voltage", "/Dc/0/Current", "/Dc/0/Power", "/Motor/RPM"],
            )
        ]

    def get_output(self):
        return [
            (PREFIX + "/0/Service", {"gettext": "%s"}),
            (PREFIX + "/0/DeviceInstance", {"gettext": "%d"}),
            (PREFIX + "/0/RPM", {"gettext": "%drpm"}),
            (PREFIX + "/1/Service", {"gettext": "%s"}),
            (PREFIX + "/1/DeviceInstance", {"gettext": "%d"}),
            (PREFIX + "/1/RPM", {"gettext": "%drpm"}),
            (PREFIX + "/Power", {"gettext": "%dW"}),
            (PREFIX + "/Voltage", {"gettext": "%.1fV"}),
            (PREFIX + "/Current", {"gettext": "%.2fA"}),
        ]

    def get_settings(self):
        return [
            (
                "DualDrive/Left/DeviceInstance",
                "/Settings/Gui/ElectricPropulsionUI/DualDrive/Left/DeviceInstance",
                -1,
                0,
                0,
            ),
            (
                "DualDrive/Right/DeviceInstance",
                "/Settings/Gui/ElectricPropulsionUI/DualDrive/Right/DeviceInstance",
                -1,
                0,
                0,
            ),
        ]

    def device_added(self, service, instance, *args):
        if service.startswith("com.victronenergy.motordrive."):
            self._settings["electricpropulsionenabled"] = 1

    def _get_service_for_device_instance(self, instance):
        services = self._dbusmonitor.get_service_list("com.victronenergy.motordrive")
        for k, v in services.items():
            if v == instance:
                return (k, v)
        return None

    def _get_service_having_lowest_instance(self):
        services = self._dbusmonitor.get_service_list("com.victronenergy.motordrive")
        if len(services) == 0:
            return None
        s = sorted((value, key) for (key, value) in services.items())
        return (s[0][1], s[0][0])

    def _update_values_dual_drive(self, newvalues):
        left_device_instance = self._settings["DualDrive/Left/DeviceInstance"]
        right_device_instance = self._settings["DualDrive/Right/DeviceInstance"]

        if left_device_instance == -1 or right_device_instance == -1:
            return False

        left_service = self._get_service_for_device_instance(left_device_instance)
        left_service_name = left_service[0] if left_service else None
        right_service = self._get_service_for_device_instance(right_device_instance)
        right_service_name = right_service[0] if right_service else None

        if (
            left_service_name is None
            or right_service_name is None
            or left_service_name == right_service_name
        ):
            return False

        left_voltage = self._dbusmonitor.get_value(left_service_name, "/Dc/0/Voltage")
        right_voltage = self._dbusmonitor.get_value(right_service_name, "/Dc/0/Voltage")

        left_current = self._dbusmonitor.get_value(left_service_name, "/Dc/0/Current")
        right_current = self._dbusmonitor.get_value(right_service_name, "/Dc/0/Current")

        left_power = self._dbusmonitor.get_value(left_service_name, "/Dc/0/Power")
        if left_power is None and left_voltage is not None and left_current is not None:
            left_power = left_voltage * left_current
        right_power = self._dbusmonitor.get_value(right_service_name, "/Dc/0/Power")
        if (
            right_power is None
            and right_voltage is not None
            and right_current is not None
        ):
            right_power = right_voltage * right_current

        aggregated_current = (
            left_current + right_current
            if left_current is not None and right_current is not None
            else None
        )
        aggregated_power = (
            left_power + right_power
            if left_power is not None and right_power is not None
            else None
        )
        aggregated_voltage = (
            aggregated_power / aggregated_current
            if aggregated_power is not None
            and aggregated_current is not None
            and aggregated_current != 0
            else None
        )

        newvalues[PREFIX + "/0/Service"] = left_service_name
        newvalues[PREFIX + "/0/DeviceInstance"] = left_device_instance
        newvalues[PREFIX + "/0/RPM"] = self._dbusmonitor.get_value(
            left_service_name, "/Motor/RPM"
        )

        newvalues[PREFIX + "/1/Service"] = right_service_name
        newvalues[PREFIX + "/1/DeviceInstance"] = right_device_instance
        newvalues[PREFIX + "/1/RPM"] = self._dbusmonitor.get_value(
            right_service_name, "/Motor/RPM"
        )
        newvalues[PREFIX + "/Current"] = aggregated_current
        newvalues[PREFIX + "/Power"] = aggregated_power
        newvalues[PREFIX + "/Voltage"] = aggregated_voltage
        return True

    def _update_values_single_drive(self, newvalues):
        service = self._get_service_having_lowest_instance()
        if service is None:
            return

        service_name, device_instance = service

        newvalues[PREFIX + "/0/Service"] = service_name
        newvalues[PREFIX + "/0/DeviceInstance"] = device_instance
        newvalues[PREFIX + "/Voltage"] = self._dbusmonitor.get_value(
            service_name, "/Dc/0/Voltage"
        )
        newvalues[PREFIX + "/Current"] = self._dbusmonitor.get_value(
            service_name, "/Dc/0/Current"
        )

        # RPM of multiple drives can't be aggregated, so store it with the index.
        # RPM is needed here because we need to track its maximum.
        newvalues[PREFIX + "/0/RPM"] = self._dbusmonitor.get_value(
            service_name, "/Motor/RPM"
        )

        # Not sure power is available, calculate it if not
        newvalues[PREFIX + "/Power"] = self._dbusmonitor.get_value(
            service_name, "/Dc/0/Power"
        )
        if (
            newvalues[PREFIX + "/Power"] is None
            and newvalues[PREFIX + "/Voltage"] is not None
            and newvalues[PREFIX + "/Current"] is not None
        ):
            newvalues[PREFIX + "/Power"] = (
                newvalues[PREFIX + "/Voltage"] * newvalues[PREFIX + "/Current"]
            )

    def update_values(self, newvalues):
        if self._update_values_dual_drive(newvalues) == True:
            return
        self._update_values_single_drive(newvalues)
