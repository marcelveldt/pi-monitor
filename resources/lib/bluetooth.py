#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import sys
import time
import threading
import subprocess
from utils import PlayerMetaData, json, DEVNULL
from __future__ import absolute_import, print_function, unicode_literals


try:
    import dbus
except ImportError:
    print("Installing python-dbus with apt-get")
    os.system("apt-get install -y python-dbus")
    import dbus

if not os.path.isfile("/usr/bin/bluealsa-aplay"):
    print("Installing bluealsa with apt-get")
    os.system("apt-get install -y bluealsa")

import dbus.service
import dbus.mainloop.glib
try:
  from gi.repository import GObject
except ImportError:
  import gobject as GObject

AGENT_INTERFACE = "org.bluez.Agent1"
AGENT_PATH = "/test/agent"
  
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

class BluetoothPlayer(threading.Thread):
    _exit = False
    _callback = None
    _output_id = None
    _last_state = None
    _bluealsa_proc = None

    def __init__(self, logger, player_name, volume_control, app_key, callback):
        self._callback = callback
        self.logger = logger
        self.app_key = app_key
        self.player_name = player_name
        self.volume_control = volume_control
        self._metadata = PlayerMetaData()
        self.event = threading.Event()
        os.system("service spotify-connect-web stop") # make sure that the original service is stopped
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit = True
        self._bluealsa_proc.terminate()
        self._dbus_loop.stop()
        self.event.set()
        self.join(1)

    @property
    def metadata(self):
        return self._metadata

    def player_control(self, cmd):
        ''' send command to roon output/zone'''
        if cmd == "stop":
            self._api_execute("/login/logout")
        else:
            if cmd == "previous":
                cmd = "prev"
            self._api_execute("playback/%s" % cmd)

    def volume_up(self):
        cur_vol = self.get_volume()
        self.set_volume(cur_vol + 2)

    def volume_down(self):
        cur_vol = self.get_volume()
        self.set_volume(cur_vol - 2)

    def set_volume(self, volume_level):
        ''' set volume level '''
        self.logger.warning("set_volume is currently not supported for this player")
        pass

    def get_volume(self):
        ''' get current volume level of player'''
        vol_level = 0
        output_details = self._api_request("playback/volume")
        if output_details and output_details.get("volume"):
            vol_level = int(float(output_details['volume'] / 655.35))
        return vol_level

    def get_player_state(self):
        ''' current state of zone '''
        cur_state = "stopped"
        state_details = self._api_request("info/status")
        if state_details:
            if state_details["active"] and state_details["playing"]:
                cur_state = "playing"
            elif state_details["active"] and not state_details["playing"]:
                cur_state = "paused"
        return cur_state

    def is_playing(self):
        return self.get_player_state() == "playing"

    def _api_request(self, endpoint, params=None):
        '''get info from json api'''
        result = {}
        url = "http://localhost:4000/api/%s" % endpoint
        params = params if params else {}
        try:
            response = requests.get(url, params=params, timeout=10)
            if response and response.content and response.status_code == 200:
                if "{" in response.content:
                    result = json.loads(response.content.decode('utf-8', 'replace'))
                else:
                    result = response.content.decode('utf-8')
            else:
                self.logger.error("Invalid or empty reponse from server - endpoint: %s - server response: %s - %s" %
                        (endpoint, response.status_code, response.content))
        except Exception as exc:
            self.logger.error(exc)
            result = None
        return result

    def _api_execute(self, endpoint, params=None):
        '''execute command on json api without waiting for result'''
        url = "http://localhost:4000/api/%s" % endpoint
        params = params if params else {}
        try:
            requests.get(url, params=params, timeout=0.5)
        except Exception as exc:
            self.logger.debug(exc)

    def update_metadata(self):
        metadata = self._api_request("info/metadata")
        if metadata:
            self._metadata.artist = metadata["artist_name"]
            self._metadata.album = metadata["album_name"]
            self._metadata.title = metadata["track_name"]
            self._metadata.duration = metadata["duration"]
            if metadata["cover_uri"]:
                self._metadata.cover_url = "http://localhost:4000/api/info/image_url/%s" % metadata["cover_uri"]
            else:
                self._metadata.cover_url = None
        else:
            self._metadata = PlayerMetaData()

    def run(self):
        # launch the dbus watcher for bluetooth discovery
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        agent = Agent(bus, AGENT_PATH)
        obj = bus.get_object("org.bluez", "/org/bluez");
        manager = dbus.Interface(obj, "org.bluez.AgentManager1")
        manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
        self.logger.info("A2DP Agent Registered")
        manager.RequestDefaultAgent(AGENT_PATH)
        self._dbus_loop = GObject.MainLoop()
        self._dbus_loop.run()
        # launch bluealsa for playback
        args = ["/usr/bin/bluealsa-aplay", "--profile-a2dp", "00:00:00:00:00:00"]
        self._bluealsa_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)

        while not self._exit:
            # cur_state = self.get_player_state()
            # if cur_state != self._last_state:
            #     self._last_state = cur_state
            #     self.update_metadata()
            #     self._callback("spotify", cur_state)
            # if cur_state == "playing":
            #     self.update_metadata()
            # if self._spotify_proc.returncode and self._spotify_proc.returncode > 0 and not self._exit:
            #     # daemon crashed ? restart ?
            #     self.logger.error("spotify-connect-web exited")
            #     break
            time.sleep(LOOP_WAIT)
        