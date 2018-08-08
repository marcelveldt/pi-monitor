#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading


LOOP_WAIT = 600


def setup(monitor):
    '''setup the module'''
    return SystemState(monitor)


class SystemState(threading.Thread):
    _exit = threading.Event()

    def __init__(self, monitor):
        self.monitor = monitor
        self.monitor.states["systemstate"] = {"cputemp": 0}
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        threading.Thread.join(self, 10)

    def update_states(self):
        self.monitor.states["systemstate"]["cputemp"] = self._get_cputemp()
        # TODO: add some more to monitor

    def _get_cputemp(self):
        cputemp = 0
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as cpufile:
                cputemp = int(float(cpufile.readline()))
                if cputemp > 200:
                    cputemp = cputemp / 1000
        except ValueError:
            LOGGER.error("Could not read CPU temperature...")
        return cputemp


    def run(self):
        while not self._exit.isSet():
            cputemp = self.update_states()
            self._exit.wait(LOOP_WAIT)