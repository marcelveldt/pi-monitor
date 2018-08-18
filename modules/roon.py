#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading
from resources.lib.utils import PlayerMetaData, json, requests, PLATFORM, check_software, run_proc, subprocess, DEVNULL, PLAYING_STATE, VOLUME_CONTROL_DISABLED, import_or_install, HOSTNAME


LOOP_WAIT = 4


def setup(monitor):
    '''setup the module'''
    if not monitor.config.get("ENABLE_MODULE_ROON", False):
        LOGGER.debug("Roon module is not enabled!")
        return False
    is_armv6 = "armv6" in PLATFORM
    enable_squeezelite = monitor.config.get("ROON_USE_SQUEEZELITE", is_armv6)
    player_name = monitor.config.get("ROON_PLAYER_NAME", u"%hostname%")
    if not player_name:
        LOGGER.warning("Roon player name is empty")
        return False
    if "armv6" in PLATFORM and not enable_squeezelite:
        LOGGER.warning("unsupported platform! %s" % PLATFORM)
        return False
    if enable_squeezelite and not check_software(dietpi_id="36", bin_path="/usr/bin/squeezelite", installapt="squeezelite"):
        LOGGER.warning("Squeezelite is not installed, please install manually.")
        return False
    elif not enable_squeezelite and not check_software(dietpi_id="121", bin_path="/opt/RoonBridge/RoonBridge"):
        LOGGER.warning("RoonBridge is not installed, please install manually.")
        return False
    if enable_squeezelite and monitor.config.get("ENABLE_MODULE_SQUEEZELITE", False):
        LOGGER.debug("Squeezelite module is enabled. You can not use Roon in squeezelite mode at the same time!")
        return False
    import_or_install("roon", "RoonApi", True, installpip="roonapi")
    return RoonPlayer(monitor, player_name, enable_squeezelite)


class RoonPlayer(threading.Thread):
    _exit = threading.Event()
    _callback = None
    _output_id = None
    _last_state = None
    _squeezelite_proc = None
    _roonapi = None

    def __init__(self, monitor, player_name, enable_squeezelite):
        threading.Thread.__init__(self)
        self.player_name = player_name
        self.monitor = monitor
        self.enable_squeezelite = enable_squeezelite
        self.monitor.states["roon"] = PlayerMetaData("Roon")
        

    @property   
    def output_id(self):
        if self._output_id:
            return self._output_id
        else:
            output_id = self._get_output_id()
        self._output_id = output_id
        return output_id

    def stop(self):
        self._exit.set()
        self.monitor.config["ROON_AUTH_TOKEN"] = self._roonapi.token
        if self._squeezelite_proc:
            self._squeezelite_proc.terminate()
        if self._roonapi:
            self._roonapi.stop()
        threading.Thread.join(self, 2)

    def command(self, cmd, cmd_data=None):
        ''' send command to roon output/zone'''
        if not self._roonapi:
            return False
        if cmd == "volume_up":
            return self._roonapi.change_volume(self.output_id, 2, "relative")
        elif cmd == "volume_down":
            return self._roonapi.change_volume(self.output_id, -2, "relative")
        elif cmd == "volume_set":
            return self._set_volume(cmd_data)
        else:
            return self._roonapi.playback_control(self.output_id, cmd)

    def run(self):
        if self.enable_squeezelite:
            # we start squuezelite manually with our optimal settings
            run_proc("service squeezelite stop", check_result=True, ignore_error=True)
            exec_path = "/usr/bin/squeezelite"
            args = [exec_path, "-C", "1", "-n", self.player_name, "-a", "4096:1024"]
            if self.monitor.config["ALSA_VOLUME_CONTROL"] and self.monitor.config["ALSA_VOLUME_CONTROL"] != VOLUME_CONTROL_DISABLED:
                args += ["-V", self.monitor.config["ALSA_VOLUME_CONTROL"], "-X"]
            if self.monitor.config["ALSA_SOUND_DEVICE"]:
                args += ["-o", self.monitor.config["ALSA_SOUND_DEVICE"]]
            self._squeezelite_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)
        # connect to the roon websockets api
        appinfo = {
            "extension_id": "pi_monitor_%s" % HOSTNAME,
            "display_name": "Pi Monitor (%s)" % HOSTNAME,
            "display_version": "1.0.0",
            "publisher": "marcelveldt",
            "email": "marcelveldt@users.noreply.github.com",
            "website": "https://github.com/marcelveldt/pi-monitor"
        }
        token = monitor.config.get("ROON_AUTH_TOKEN","")
        self._roonapi = RoonApi(appinfo, token, blocking_init=False)
        self._roonapi.register_state_callback(self._roon_state_callback, event_filter="zones_changed", id_filter=self.player_name)

        # some players need to be unmuted when freshly started
        if self.output_id:
            self._roonapi.mute(self.output_id, False)

        # mainloop: just keep the thread alive
        while not self._exit.isSet():
            self._exit.wait(1200)


    #### PRIVATE CLASS METHODS #######

    def _roon_state_callback(self, event, changed_items):
        '''will be called when roon reports events for our player'''
        self._update_metadata()

    def _get_output_id(self):
        ''' get output_id for this player'''
        output_id = ""
        output = self._roonapi.output_by_name(self.player_name)
        if output:
            output_id = output["output_id"]
            LOGGER.debug("detected output id: %s" % output_id)
        else:
            LOGGER.debug("unable to detect Roon output ID, skip for now...")
        return output_id

    def _set_volume(self, volume_level):
        ''' set volume level '''
        return self._roonapi.change_volume(self.output_id, volume_level)

    def _get_volume(self):
        ''' get current volume level of player'''
        vol_level = 0
        output_details = self._roonapi.outputs.get(self.output_id)
        if output_details and output_details.get("volume"):
            if output_details["volume"]["type"] == "db":
                vol_level = int(float(output_details['volume']['value']) / 80 * 100 + 100)
            else:
                vol_level = output_details["volume"]["value"]
        return vol_level

    def _update_metadata(self):
        zone_details = self._roonapi.zone_by_output_name(self.player_name)
        self.monitor.states["roon"]["state"] = zone_details.get("state")
        self.monitor.states["roon"]["volume_level"] = self._get_volume()
        if zone_details and zone_details.get("now_playing"):
            zone_details = zone_details["now_playing"]
            self.monitor.states["roon"].update({
                    "artist": zone_details["three_line"]["line2"],
                    "album": zone_details["three_line"]["line3"],
                    "title": zone_details["three_line"]["line1"],
                    "duration": zone_details["length"],
                    "cover_url": self._roonapi.get_image(zone_details["image_key"])
                })
        else:
            self.monitor.states["roon"].update({
                    "artist": "",
                    "album": "",
                    "title": "",
                    "duration": 0,
                    "cover_url": ""
                })
