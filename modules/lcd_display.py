#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading
import time
from resources.lib.utils import import_or_install


def setup(monitor):
    '''setup the module'''
    if not monitor.config.get("ENABLE_MODULE_LCD_DISPLAY", False):
        LOGGER.debug("LCD Display module is not enabled!")
        return False
    # TODO: add config entries for type of display and hardware address
    # currently hardcoded to 20x4 display at address 0x3f
    import_or_install("RPLCD.i2c", "CharLCD", True, "RPLCD")
    return LCDDisplay(monitor)


class LCDDisplay(threading.Thread):
    _exit = threading.Event()

    def __init__(self, monitor):
        self.lcd = CharLCD(i2c_expander='PCF8574', address=0x3f, port=1,
              cols=20, rows=4, dotsize=8,
              charmap='A02',
              auto_linebreaks=True,
              backlight_enabled=True)
        self.monitor
        self.monitor.register_state_callback(self.state_changed_event, "player")
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        self.disable_lcd()
        self.monitor.deregister_state_callback(self.state_changed_event, "player")
        threading.Thread.join(self, 10)

    def run(self):
        while not self._exit.isSet():
            self._exit.wait(3600) # keep thread alive

    def disable_lcd(self):
        self.lcd.clear()
        self.lcd.backlight_enabled = False
        self.lcd.display_enabled = False

    def enable_lcd(self):
        self.lcd.display_enabled = True
        self.lcd.backlight_enabled = True
        self.lcd.clear()

    def state_changed_event(self, key, value=None, subkey=None):
        if key == "player" and subkey == "power":
            if self.monitor.states["player"]["power"]:
                self.enable_lcd()
            else:
                self.disable_lcd()
        elif key == "player" and subkey == "details":
            self.update_display_info()

    def update_display_info(self):
        player_info = self.monitor.player_info
        if player_info["state"] == "paused":
            artist = ""
            title = "PAUSED"
        elif player_info["state"] == "stopped":
            artist = ""
            title = "STOPPED"
        else:
            artist = player_info["details"]["artist"]
            title = player_info["details"]["title"]
        # update display info
        self.lcd.clear()
        _artist = artist[:39]
        _title = title[:39]
        self.lcd.write_string(_artist)
        self.lcd.cursor_pos = (2, 0)
        self.lcd.write_string(_title)

