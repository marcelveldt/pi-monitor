#!/usr/bin/env python
from __future__ import absolute_import, print_function, unicode_literals
import os
import time
import threading
import thread
import subprocess
from resources.lib.utils import PlayerMetaData, json, DEVNULL, HOSTNAME, requests, PLATFORM, run_proc, check_software, import_or_install, PAUSED_STATE, PLAYING_STATE, STOPPED_STATE
import_or_install("dbus", installapt="python-dbus")
import dbus.service
import dbus.mainloop.glib

"""
    BluetoothPlayer
    player implementation for Bluetooth
    very basic which uses bluez alsa
"""

AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_PATH = "/test/agent"

def setup(monitor):
    '''setup the module'''
    if not monitor.config.get("ENABLE_MODULE_BLUETOOTH", False):
        LOGGER.warning("Bluetooth module is not enabled!")
        return False
    if not check_software(bin_path="/usr/bin/bluealsa-aplay", installapt="bluetooth bluez-firmware bluealsa"):
        LOGGER.warning("Bluez Alsa is not installed, please install manually.")
        return False

    import_or_install("gobject", "GObject", installapt="python3-gi python-gobject")
    
    return BluetoothPlayer(monitor)



class BluetoothPlayer(threading.Thread):
    _exit = threading.Event()
    _last_state = None
    _bluealsa_proc = None
    _token = None

    def __init__(self, monitor):
        self.monitor = monitor
        self.monitor.states["bluetooth"] = PlayerMetaData("Bluetooth")
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        if self._bluealsa_proc:
            self._bluealsa_proc.terminate()
        threading.Thread.join(self, 2)

    def command(self, cmd, cmd_data=None):
        ''' send command to player'''
        if cmd == "stop":
            cmd = "pause"
        if cmd == "volume_up":
            return False
        elif cmd == "volume_down":
            return False
        elif cmd == "volume_set":
            return False
        elif cmd == "previous":
            cmd = "prev"
        return False

    def run(self):
        # check bluetooth config
        with open('/etc/bluetooth/main.conf') as f:
            cur_config = f.read()
        if 'DiscoverableTimeout = 0' in cur_config:
            cur_config = cur_config.replace('#DiscoverableTimeout = 0', 'DiscoverableTimeout = 0')
            cur_config = cur_config.replace('#Class = 0x000100', 'Class = 0x200414')
            cur_config = cur_config.replace('#AutoEnable=true', 'AutoEnable=true')
            cur_config = cur_config.replace('#AutoEnable=false', 'AutoEnable=true')
            cur_config = cur_config.replace('AutoEnable=false', 'AutoEnable=true')
            os.system('service bluetooth restart')
            os.system('hciconfig hci0 piscan')
            os.system('hciconfig hci0 sspmode 1')

        args = ["/usr/bin/bluealsa-aplay", "--a2dp-volume", "-vv", "00:00:00:00:00:00"]
        if self.monitor.config["ENABLE_DEBUG"]:
            LOGGER.debug("Starting bluealsa-aplay: %s" % " ".join(args))
            self._bluealsa_proc = subprocess.Popen(args)
        else:
            self._bluealsa_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)
        # start dbus connection to listen for events
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        agent = Agent(bus, AGENT_PATH)
        obj = bus.get_object("org.bluez", "/org/bluez");
        manager = dbus.Interface(obj, "org.bluez.AgentManager1")
        manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
        LOGGER.info("A2DP Agent Registered")
        manager.RequestDefaultAgent(AGENT_PATH)
        mainloop = GObject.MainLoop()
        mainloop.run()

        # loop_wait = 1200
        # while not self._exit.isSet():
        #     if self._spotify_proc.returncode and self._spotify_proc.returncode > 0 and not self._exit:
        #         # daemon crashed ? restart ?
        #         LOGGER.error("librespot exited ?!")
        #         break
        #     self._exit.wait(loop_wait) # we just wait as we'll be notified of updates through the socket


class Rejected(dbus.DBusException):
    _dbus_error_name = "org.bluez.Error.Rejected"

class Agent(dbus.service.Object):
    exit_on_release = True

    def set_exit_on_release(self, exit_on_release):
        self.exit_on_release = exit_on_release

    @dbus.service.method(AGENT_INTERFACE,
                    in_signature="", out_signature="")
    def Release(self):
        print("Release")
        if self.exit_on_release:
            mainloop.quit()

    @dbus.service.method(AGENT_INTERFACE,
                    in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        print("AuthorizeService (%s, %s)" % (device, uuid))
        if uuid == "0000110d-0000-1000-8000-00805f9b34fb":
            print("Authorized A2DP Service")
            return
        print("Rejecting non-A2DP Service")
        raise Rejected("Connection rejected")

    @dbus.service.method(AGENT_INTERFACE,
                    in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        print("RequestPinCode (%s)" % (device))
        return "0000"

    @dbus.service.method(AGENT_INTERFACE,
                    in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        print("RequestPasskey (%s)" % (device))
        return dbus.UInt32("password")

    @dbus.service.method(AGENT_INTERFACE,
                    in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print("DisplayPasskey (%s, %06u entered %u)" %
                        (device, passkey, entered))

    @dbus.service.method(AGENT_INTERFACE,
                    in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        print("DisplayPinCode (%s, %s)" % (device, pincode))

    @dbus.service.method(AGENT_INTERFACE,
                    in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        print("RequestConfirmation (%s, %06d)" % (device, passkey))
        return

    @dbus.service.method(AGENT_INTERFACE,
                    in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        print("RequestAuthorization (%s)" % (device))
        raise Rejected("Pairing rejected")

    @dbus.service.method(AGENT_INTERFACE,
                    in_signature="", out_signature="")
    def Cancel(self):
        print("Cancel")