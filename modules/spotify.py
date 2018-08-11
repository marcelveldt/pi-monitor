#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading
import subprocess
from resources.lib.utils import PlayerMetaData, json, DEVNULL, HOSTNAME, requests, PLATFORM, run_proc, check_software, RESOURCES_FOLDER, VOLUME_CONTROL_DISABLED

"""
    SpotifyPlayer
    player implementation for Spotify
    for now still using spotify-connect-web (and not raspotify/librespot) because it has hardware volume control
"""


def setup(monitor):
    '''setup the module'''
    if not ("armv6" in PLATFORM or "armv7" in PLATFORM):
        LOGGER.error("unsupported platform! %s" % PLATFORM)
        return False
    if not monitor.config.get("ENABLE_MODULE_SPOTIFY", False):
        LOGGER.warning("Spotify module is not enabled!")
        return False
    if not check_software(bin_path="/usr/bin/avahi-publish-service", installapt="avahi-utils"):
        LOGGER.error("avahi-utils is not installed! Please install manually")
        return False

    return SpotifyPlayer(monitor)



class SpotifyPlayer(threading.Thread):
    _exit = threading.Event()
    _last_state = None
    _spotify_proc = None
    _avahi_proc = None

    def __init__(self, monitor):
        self.monitor = monitor
        self.monitor.states["spotify"] = PlayerMetaData("Spotify")
        run_proc("service spotify-connect-web stop", check_result=True, ignore_error=True) # make sure that the original service is stopped
        run_proc("service raspotify stop", check_result=True, ignore_error=True) # make sure that the original service is stopped
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        if self._avahi_proc:
            self._avahi_proc.terminate()
        if self._spotify_proc:
            self._spotify_proc.terminate()
        threading.Thread.join(self, 10)

    def command(self, cmd, cmd_data=None):
        ''' send command to player'''
        if cmd == "update":
            return self._update_metadata()
        if cmd == "stop":
            cmd = "pause"
        elif cmd == "volume_up":
            return False
        elif cmd == "volume_down":
            return False
        elif cmd == "volume_set":
            return False
        elif cmd == "previous":
            cmd = "prev"
        return self._api_execute("api/playback/%s" % cmd)

    def _volume_get(self):
        ''' get current volume level of player'''
        vol_level = 0
        output_details = self._api_request("api/playback/volume")
        if output_details and output_details.get("volume"):
            vol_level = int(float(output_details['volume'] / 655.35))
        return vol_level

    def _api_request(self, endpoint, params=None):
        '''get info from json api'''
        result = {}
        url = "http://localhost:4000/%s" % endpoint
        params = params if params else {}
        try:
            response = requests.get(url, params=params, timeout=10)
            if response and response.content and response.status_code == 200:
                if "{" in response.content:
                    result = json.loads(response.content.decode('utf-8', 'replace'))
                else:
                    result = response.content.decode('utf-8')
            else:
                LOGGER.error("Invalid or empty reponse from server - endpoint: %s - server response: %s - %s" %
                        (endpoint, response.status_code, response.content))
        except Exception as exc:
            #LOGGER.error(exc)
            result = None
        return result

    def _api_execute(self, endpoint, params=None):
        '''execute command on json api without waiting for result'''
        url = "http://localhost:4000/%s" % endpoint
        params = params if params else {}
        try:
            requests.get(url, params=params, timeout=0.5)
            return True
        except Exception as exc:
            #LOGGER.debug(exc)
            return False

    def _update_metadata(self):
        metadata = self._api_request("api/info/metadata")
        self.monitor.states["spotify"]["state"] = self._get_state()
        if metadata:
            self.monitor.states["spotify"]["volume_level"] = self._volume_get()
            self.monitor.states["spotify"]["artist"] = metadata["artist_name"]
            self.monitor.states["spotify"]["album"] = metadata["album_name"]
            self.monitor.states["spotify"]["title"] = metadata["track_name"]
            self.monitor.states["spotify"]["duration"] = metadata["duration"]
            if metadata["cover_uri"]:
                self.monitor.states["spotify"]["cover_url"] = "http://localhost:4000/api/info/image_url/%s" % metadata["cover_uri"]
            else:
                self.monitor.states["spotify"]["cover_url"] = ""

    def _get_state(self):
        ''' current state of zone '''
        cur_state = "stopped"
        state_details = self._api_request("api/info/status")
        if state_details:
            if state_details["active"] and state_details["playing"]:
                cur_state = "playing"
            elif state_details["active"] and not state_details["playing"]:
                cur_state = "paused"
        return cur_state

    def run(self):
        # finally start the spotify executable
        # currently always use the chroot version as it is the most stable (surpisingly enough)
        # the chroot version works on both armv6 and armv7
        exec_path = os.path.join(RESOURCES_FOLDER, "spotify", "spotify-connect-web-chroot.sh")
        args = [exec_path, "--bitrate", "320", "--name", HOSTNAME]
        if self.monitor.config["ALSA_VOLUME_CONTROL"] and self.monitor.config["ALSA_VOLUME_CONTROL"] != VOLUME_CONTROL_DISABLED:
            args += ["--mixer", self.monitor.config["ALSA_VOLUME_CONTROL"]]
        if self.monitor.config["ALSA_SOUND_DEVICE"]:
            args += ["--playback_device", self.monitor.config["ALSA_SOUND_DEVICE"]]
        if self.monitor.config["ENABLE_DEBUG"]:
            LOGGER.debug("Starting spotify-connect-web: %s" % " ".join(args))
            self._spotify_proc = subprocess.Popen(args)
        else:
            self._spotify_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)

        # launch avahi for auto discovery
        args = ["/usr/bin/avahi-publish-service", HOSTNAME, 
                "_spotify-connect._tcp", "4000", "VERSION=1.0", "CPath=/login/_zeroconf"]
        self._avahi_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)

        loop_wait = 120
        while not self._exit.isSet():
            if self._spotify_proc.returncode and self._spotify_proc.returncode > 0 and not self._exit:
                # daemon crashed ? restart ?
                LOGGER.error("spotify-connect-web exited !")
                break
            if not self.monitor.config["ENABLE_MODULE_WEBCONFIG"]:
                # if webinterface is disabled, we need to poll
                cur_state = self._get_state()
                if cur_state != self._last_state:
                    self._last_state = cur_state
                    self._update_metadata()
                if cur_state == "playing":
                    self._update_metadata()
                    loop_wait = 1
                else:
                    loop_wait = 3
            self._exit.wait(loop_wait) # we just wait as we'll be notified of updates through the webinterface
        