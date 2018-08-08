#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading
import subprocess
from resources.lib.utils import PlayerMetaData, json, DEVNULL, HOSTNAME, requests, PLATFORM, run_proc, check_software

LOOP_WAIT = 2


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

    if "armv7" in PLATFORM and check_software(dietpi_id="141", bin_path="/mnt/dietpi_userdata/spotify-connect-web/spotify-connect-web'"):
        exec_path = '/mnt/dietpi_userdata/spotify-connect-web/spotify-connect-web'
    else:
        # chroot version
        base_dir = os.path.dirname(os.path.abspath(__file__))
        exec_path = os.path.join(base_dir, "..","resources", "spotify-connect-web-chroot.sh")
    return SpotifyPlayer(monitor, exec_path)



class SpotifyPlayer(threading.Thread):
    _exit = threading.Event()
    _last_state = None
    _spotify_proc = None
    _avahi_proc = None

    def __init__(self, monitor, exec_path):
        self.monitor = monitor
        self.exec_path = exec_path
        self.monitor.states["spotify"] = PlayerMetaData("Spotify")
        run_proc("service spotify-connect-web stop", check_result=True, ignore_error=True) # make sure that the original service is stopped
        run_proc("service raspotify stop", check_result=True, ignore_error=True) # make sure that the original service is stopped
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        self._spotify_proc.terminate()
        self._avahi_proc.terminate()
        threading.Thread.join(self, 10)

    def command(self, cmd, cmd_data=None):
        ''' send command to roon output/zone'''
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

        # launch avahi for auto discovery
        args = ["/usr/bin/avahi-publish-service", HOSTNAME, 
                "_spotify-connect._tcp", "4000", "VERSION=1.0", "CPath=/login/_zeroconf"]
        self._avahi_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)

        # launch the spotify-connect-web executable
        # fix some stuff if needed
        if "chroot" in self.exec_path:
            exec_dir = os.path.join(os.path.dirname(os.path.abspath(self.exec_path)), "spotify-web-chroot", "usr", "src", "app")
        else:
            exec_dir = os.path.dirname(os.path.abspath(self.exec_path))
        mod_file = os.path.join(exec_dir, "utils.py")
        key_file_dest = os.path.join(exec_dir, "spotify_appkey.key")
        key_file_org = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..","resources", "spotify_appkey.key")
        # copy the key file
        if not os.path.isfile(key_file_dest):
            from shutil import copyfile
            copyfile(key_file_org, key_file_dest)

        # fix for image size (HACK!!)
        if os.path.isfile(mod_file):
            with open(mod_file) as f:
                cur_contents = f.read()
            if "lib.kSpImageSizeSmall" in cur_contents:
                with open(mod_file, "w") as f:
                    cur_contents = cur_contents.replace("lib.kSpImageSizeSmall", "lib.kSpImageSizeLarge")
                    f.write(cur_contents)

        args = [self.exec_path, "--bitrate", "320", "--name", HOSTNAME]
        if self.monitor.config["ALSA_VOLUME_CONTROL"]:
            args += ["--mixer", self.monitor.config["ALSA_VOLUME_CONTROL"]]
        if self.monitor.config["ALSA_SOUND_DEVICE"]:
            args += ["--playback_device", self.monitor.config["ALSA_SOUND_DEVICE"]]
        #self._spotify_proc = subprocess.Popen(args, cwd=exec_dir, stdout=DEVNULL, stderr=subprocess.STDOUT)
        LOGGER.debug("Starting spotify-connect-web with exec: %s" % self.exec_path)
        self._spotify_proc = subprocess.Popen(args, cwd=exec_dir)

        while not self._exit.isSet():
            cur_state = self._get_state()
            if cur_state != self._last_state:
                self._last_state = cur_state
                self._update_metadata()
            if cur_state == "playing":
                self._update_metadata()
                LOOP_WAIT = 1
            else:
                LOOP_WAIT = 3
            if self._spotify_proc.returncode and self._spotify_proc.returncode > 0 and not self._exit:
                # daemon crashed ? restart ?
                LOGGER.error("spotify-connect-web exited")
                break
            self._exit.wait(LOOP_WAIT)
        