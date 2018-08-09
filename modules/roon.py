#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading
from resources.lib.utils import PlayerMetaData, json, requests, PLATFORM, check_software, run_proc, subprocess, DEVNULL, PLAYING_STATE


LOOP_WAIT = 4


def setup(monitor):
    '''setup the module'''
    if not monitor.config.get("ENABLE_MODULE_ROON", False):
        LOGGER.debug("Roon module is not enabled!")
        return False
    enable_squeezelite = monitor.config.get("ROON_USE_SQUEEZELITE", False)
    roon_proxy = monitor.config.get("ROON_PROXY", u"http://192.168.1.1:3006")
    if not roon_proxy:
        LOGGER.warning("Roon proxy address is empty")
        return False
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
    # TODO: check if service is actually installed
    return RoonPlayer(monitor, roon_proxy, player_name, enable_squeezelite)


class RoonPlayer(threading.Thread):
    _exit = threading.Event()
    _callback = None
    _output_id = None
    _last_state = None
    _squeezelite_proc = None

    def __init__(self, monitor, proxy_address, player_name, enable_squeezelite):
        threading.Thread.__init__(self)
        self.player_name = player_name
        self.proxy_address = proxy_address
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
        if self._squeezelite_proc:
            self._squeezelite_proc.terminate()
            #run_proc("service squeezelite start")
        threading.Thread.join(self, 10)

    def command(self, cmd, cmd_data=None):
        ''' send command to roon output/zone'''
        if cmd == "volume_up":
            return self._api_execute(
                    "change_volume_relative", {"volume":2, "output": self.output_id})
        elif cmd == "volume_down":
            return self._api_execute(
                    "change_volume_relative", {"volume":-2, "output": self.output_id})
        elif cmd == "volume_set":
            return self._set_volume(cmd_data)
        else:
            params = {
                "zone": self.output_id,
                "control": cmd
            }
            return self._api_execute("control", params=params)

    def run(self):
        if self.enable_squeezelite:
            # we start squuezelite manually with our optimal settings
            run_proc("service squeezelite stop", check_result=True, ignore_error=True)
            exec_path = "/usr/bin/squeezelite"
            args = [exec_path, "-C", "1", "-n", self.player_name, "-a", "4096:1024"]
            if self.monitor.config["ALSA_VOLUME_CONTROL"]:
                args += ["-V", self.monitor.config["ALSA_VOLUME_CONTROL"], "-X"]
            if self.monitor.config["ALSA_SOUND_DEVICE"]:
                args += ["-o", self.monitor.config["ALSA_SOUND_DEVICE"]]
            self._squeezelite_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)


        # some players need to be unmuted when freshly started
        if self.output_id:
            self._api_execute("mute", {"how":"unmute", "output": self.output_id})
        # loop: poll for player changes
        # TODO: use pure websockets implementation to access Roon
        while not self._exit.isSet():
            cur_state = self._get_state()
            if cur_state != self._last_state:
                self._last_state = cur_state
                self._update_metadata()
            if cur_state == PLAYING_STATE:
                self._update_metadata()
                LOOP_WAIT = 0.5
            else:
                LOOP_WAIT = 2
            self._exit.wait(LOOP_WAIT)


    #### PRIVATE CLASS METHODS #######

    def _get_output_id(self):
        ''' get output_id for this player'''
        output_id = self._api_request("output_id_by_name", {"name": self.player_name})
        if output_id:
            LOGGER.debug("detected output id: %s" % output_id)
        else:
            LOGGER.debug("unable to detect Roon output ID, skip for now...")
            output_id = None
        return output_id

    def _get_state(self):
        return self._api_request("zone_state", {"output": self.output_id})

    def _set_volume(self, volume_level):
        ''' set volume level '''
        output_details = self._api_request("output", {"output": self.output_id})
        if output_details and output_details.get("volume"):
            if output_details["volume"]["type"] == "db":
                volume_level = int((float(volume_level) / 100) * 80) - 80
            return self._api_execute("change_volume", {"volume":volume_level, "output": self.output_id})
        return False

    def _get_volume(self):
        ''' get current volume level of player'''
        vol_level = 0
        output_details = self._api_request("output", {"output": self.output_id})
        if output_details and output_details.get("volume"):
            if output_details["volume"]["type"] == "db":
                vol_level = int(float(output_details['volume']['value']) / 80 * 100 + 100)
            else:
                vol_level = output_details["volume"]["value"]
        return vol_level

    def _update_metadata(self):
        zone_details = self._api_request("zone_by_output_id", {"output": self.output_id})
        self.monitor.states["roon"]["state"] = zone_details.get("state")
        self.monitor.states["roon"]["volume_level"] = self._get_volume()
        if zone_details and zone_details.get("now_playing"):
            zone_details = zone_details["now_playing"]
            self.monitor.states["roon"]["artist"] = zone_details["three_line"]["line2"]
            self.monitor.states["roon"]["album"] = zone_details["three_line"]["line3"]
            self.monitor.states["roon"]["title"] = zone_details["three_line"]["line1"]
            self.monitor.states["roon"]["duration"] = zone_details["length"]
            if zone_details["image_key"]:
                url = '%s/image?image_key=%s&width=500&height=500&scale=fit' % (self.proxy_address, zone_details["image_key"])
                self.monitor.states["roon"]["cover_url"] = url
            else:
                self.monitor.states["roon"]["cover_url"] = ""
        else:
            self.monitor.states["roon"]["artist"] = ""
            self.monitor.states["roon"]["album"] = ""
            self.monitor.states["roon"]["title"] = ""
            self.monitor.states["roon"]["duration"] = ""
            self.monitor.states["roon"]["cover_url"] = ""

    def _api_request(self, endpoint, params=None):
        '''get info from json api'''
        result = {}
        url = "{}/{}".format(self.proxy_address, endpoint)
        params = params if params else {}
        try:
            response = requests.get(url, params=params, timeout=10)
            if response and response.content and response.status_code == 200:
                if "{" in response.content:
                    result = json.loads(response.content.decode('utf-8', 'replace'))
                else:
                    result = response.content.decode('utf-8')
            else:
                LOGGER.debug("Invalid or empty reponse from server - endpoint: %s - server response: %s - %s" %
                        (endpoint, response.status_code, response.content))
        except Exception as exc:
            LOGGER.error(exc)
            result = None
        return result

    def _api_execute(self, endpoint, params=None):
        '''execute command on json api without waiting for result'''
        url = "{}/{}".format(self.proxy_address, endpoint)
        params = params if params else {}
        try:
            requests.get(url, params=params, timeout=0.5)
            return True
        except Exception as exc:
            LOGGER.debug(exc)
            return False
