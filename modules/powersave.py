#!/usr/bin/env python

import os
from resources.lib.utils import PLATFORM, run_proc, check_software, IDLE_STATES
import thread
import time

def setup(monitor):
    '''setup this module'''
    if not monitor.config.get("ENABLE_MODULE_POWERSAVE", True):
        LOGGER.debug("Powersave module is not enabled!")
        return False
    cmd_on = monitor.config.get("POWERSAVE_COMMAND_ON", "cpufreq-set -g powersave")
    cmd_off = monitor.config.get("POWERSAVE_COMMAND_OFF", "cpufreq-set -g ondemand")
    cmd_auto_off = monitor.config.get("AUTO_POWER_OFF_WHEN_IDLE_SECONDS", 5)
    # Check we have the necessary module
    if not check_software(bin_path="/usr/bin/cpufreq-set", installapt="cpufrequtils"):
        LOGGER.error("cpufrequtils is not installed! Please install manually")
        return False

    if not (cmd_on or cmd_off or cmd_auto_off):
        LOGGER.debug("Powersave module settings are not set.")
        return False
    
    return PowerSave(monitor)


class PowerSave(object):
    

    def __init__(self, monitor):
        """
        Initialise the GPIO library
        """
        self.monitor = monitor
        self._interrupted = False
           
    def start(self):
        self.monitor.register_state_callback(self.state_changed_event, "player")

    def state_changed_event(self, key, value=None, subkey=None):
        if key == "player" and subkey == "power":
            player_powered = self.monitor.states["player"]["power"]
            if player_powered and self.monitor.config["POWERSAVE_COMMAND_OFF"]:
                # player is powered, disable powersave
                self.monitor.command("system", "run_proc", self.monitor.config["POWERSAVE_COMMAND_OFF"])
            elif not player_powered and self.monitor.config["POWERSAVE_COMMAND_ON"]:
                # player is not powered, enable powersave
                self.monitor.command("system", "run_proc", self.monitor.config["POWERSAVE_COMMAND_ON"])
        elif key == "player" and subkey == "state" and self.monitor.config["AUTO_POWER_OFF_WHEN_IDLE_SECONDS"]:
            if self.monitor.states["player"]["state"] in IDLE_STATES and self.monitor.states["player"]["power"]:
                self._interrupted = False
                thread.start_new_thread(self.watch_paused_state,())
            else:
                self._interrupted = True

    def watch_paused_state(self):
        ''' power off the player if paused for X seconds '''
        seconds = self.monitor.config["AUTO_POWER_OFF_WHEN_IDLE_SECONDS"]
        sleep_time = 0.5
        ticks = (seconds / sleep_time)
        while ticks >= 0 and not self._interrupted:
            ticks -= 1
            is_paused = self.monitor.states["player"]["state"] in IDLE_STATES
            if self._interrupted or not is_paused:
                break
            elif ticks <= 0 and is_paused:
                # X seconds have passed and player is still idle, we should power off
                LOGGER.info("Player is idle for %s seconds, auto powering off..." % seconds)
                self.monitor.command("power", "poweroff")
                break
            else:
                time.sleep(sleep_time)

    def stop(self):
        self._interrupted = True
        self.monitor.deregister_state_callback(self.state_changed_event, "player")
        if self.monitor.config["POWERSAVE_COMMAND_OFF"]:
            self.monitor.command("system", "run_proc", self.monitor.config["POWERSAVE_COMMAND_OFF"])
