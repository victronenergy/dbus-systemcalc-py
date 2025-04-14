import logging
import asyncio

from aiovelib.client import Monitor
from aiovelib.localsettings import Setting, SETTINGS_SERVICE, SettingsService


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SettingsMonitor(Monitor):
    def __init__(self, bus, **kwargs):
        super().__init__(bus, handlers = {
            'com.victronenergy.settings': SettingsService
        }, **kwargs)


class LocalSettingsServiceMixin:

    role = None
    instance = None

    async def _wait_for_settings(self):
        """ Attempt a connection to localsettings. """
        settingsmonitor = await SettingsMonitor.create(
            self.bus,
            itemsChanged=self._itemsChanged
        )
        self.settings = await asyncio.wait_for(
            settingsmonitor.wait_for_service(SETTINGS_SERVICE), 5)

    def _role_instance(self, value):
        val = value.split(':')
        return val[0], int(val[1])

    async def set_role_and_instance(self, ident, role, default_instance=40):
        settingprefix = '/Settings/Devices/' + ident

        await self._wait_for_settings()
        await self.settings.add_settings(
            Setting(settingprefix + "/ClassAndVrmInstance", f"{ role }:{ default_instance }", 0, 0, alias="instance"),
            Setting(settingprefix + "/Enabled", 1, 0, 0, alias="enabled")
        )

        self.role, self.instance = self._role_instance(
            self.settings.get_value(self.settings.alias("instance")))

    def _itemsChanged(self, service, values):
        logger.debug(service, values)