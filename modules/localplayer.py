#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading
import subprocess
from resources.lib.utils import PlayerMetaData, json, DEVNULL, HOSTNAME, requests, PLATFORM, run_proc, check_software, RESOURCES_FOLDER, VOLUME_CONTROL_DISABLED

'''
    NotificationPlayer
    local media player using sox, allowing to play local/remote files (such as notifications)
'''


LOOP_WAIT = 5


def setup(monitor):
    '''setup the module'''

    if not check_software(bin_path="/usr/bin/play", installapt="sox libsox-fmt-all"):
        LOGGER.error("sox is not installed! Please install manually")
        return False
    return False # wip
    return LocalPlayer(monitor)



class LocalPlayer(threading.Thread):
    _exit = threading.Event()
    _last_state = None
    _spotify_proc = None
    _avahi_proc = None

    def __init__(self, monitor):
        self.monitor = monitor
        self.monitor.states["spotify"] = PlayerMetaData("LocalPlayer")
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        if self._proc:
            self._proc.terminate()
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

    def _volume_up(self):
        success = False
        # send command to current player
        if self.is_playing:
            player_mod = self.get_module(self.states["player"]["current_player"])
            success = player_mod.command("volume_up")
        # fallback to direct alsa control
        if not success and self.config["ALSA_VOLUME_CONTROL"]:
            run_proc('amixer set "%s" 2+' % self.config["ALSA_VOLUME_CONTROL"])
            self.states["player"]["volume_level"] = self._volume_get()

    def _volume_down(self):
        success = False
        # send command to current player
        if self.is_playing:
            player_mod = self.get_module(self.states["player"]["current_player"])
            success = player_mod.command("volume_down")
        # fallback to direct alsa control
        if not success and self.config["ALSA_VOLUME_CONTROL"]:
            run_proc('amixer set "%s" 2-' % self.config["ALSA_VOLUME_CONTROL"])
            self.states["player"]["volume_level"] = self._volume_get()

    def _volume_set(self, volume_level):
        ''' set volume level '''
        success = False
        # send command to current player
        if self.is_playing:
            player_mod = self.get_module(self.states["player"]["current_player"])
            success = player_mod.command("volume_set", volume_level)
        # fallback to direct alsa control
        if not success and self.config["ALSA_VOLUME_CONTROL"]:
            run_proc('amixer set "%s" %s' % (self.config["ALSA_VOLUME_CONTROL"], str(volume_level) + "%"))
            self.states["player"]["volume_level"] = volume_level

    def _volume_get(self):
        ''' get current volume level of player'''
        vol_level = 0
        current_player = self.states["player"].get("current_player")
        if current_player and self.states[current_player]["state"] == "playing":
            vol_level = self.states[current_player]["volume_level"]
        # fallback to alsa
        if not vol_level and self.config["ALSA_VOLUME_CONTROL"]:
            amixer_result = run_proc('amixer get "%s"' % self.config["ALSA_VOLUME_CONTROL"], True)
            if amixer_result:
                cur_vol = amixer_result.split("[")[1].split("]")[0].replace("%","")
                vol_level = int(cur_vol)
        return vol_level

    def _play_sound(self, url, volume_level=None, loop=False, force=True):
        ''' play notification/alert by url '''
        if volume_level == None:
            volume_level = self.config["NOTIFY_VOLUME"]
        LOGGER.info("play_sound --> url: %s - volume_level: %s - loop: %s - force: %s" % (url, volume_level, loop, force))
        self.states["notification"] = True

        # get current state of player
        prev_vol = self._volume_get()
        prev_play = self.is_playing
        prev_pwr = self.states.get("power")
        LOGGER.debug("Current state of player --> power: %s - playing: %s - volume_level: %s" % (prev_pwr, prev_play, prev_vol))

        # send stop to release audio from current player
        self._player_command("stop")
        
        if not prev_pwr and not force:
            self.states["notification"] = False
            return

        # set notification volume level
        self.set_volume(volume_level)

        # enable power if needed
        self._set_power(True)

        if prev_play:
            time.sleep(1) # allow some time for the audio device to become available

        # play file and wait for completion (using SOX play executable)
        if loop:
            # play untill stop requested
            while self.states["notification"]:
                LOGGER.debug("playing notification....")
                run_proc('play %s bass -10' % url, True)
        else:
            # just play once
            run_proc('play %s bass -10' % url, True)

        # restore volume level and playback
        LOGGER.debug("restore state of player")
        self.set_volume(prev_vol)
        if prev_play:
            self._player_command("play")
        else:
            self._set_power(False)
        self.states["notification"] = False

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
        exec_dir = None
        for item in ["/mnt/dietpi_userdata/spotify-web-chroot/usr/src/app", "/root/spotify-web-chroot/usr/src/app"]:
            if os.path.isdir(item):
                exec_dir = item
        if exec_dir:
            # fix for image size (HACK!!)
            mod_file = os.path.join(exec_dir, "utils.py")
            if os.path.isfile(mod_file):
                with open(mod_file) as f:
                    cur_contents = f.read()
                if "lib.kSpImageSizeSmall" in cur_contents:
                    with open(mod_file, "w") as f:
                        cur_contents = cur_contents.replace("lib.kSpImageSizeSmall", "lib.kSpImageSizeLarge")
                        f.write(cur_contents)

        # finally start the spotify executable
        # currently always use the chroot version as it is the most stable (surpisingly enough)
        # the chroot version works on both armv6 and armv7
        exec_path = os.path.join(RESOURCES_FOLDER, "spotify-connect-web-chroot.sh")
        args = [exec_path, "--bitrate", "320", "--name", HOSTNAME]
        if self.monitor.config["ALSA_VOLUME_CONTROL"] and self.monitor.config["ALSA_VOLUME_CONTROL"] != VOLUME_CONTROL_DISABLED:
            args += ["--mixer", self.monitor.config["ALSA_VOLUME_CONTROL"]]
        if self.monitor.config["ALSA_SOUND_DEVICE"]:
            args += ["--playback_device", self.monitor.config["ALSA_SOUND_DEVICE"]]
        if self.monitor.config["ENABLE_DEBUG"]:
            LOGGER.debug("Starting spotify-connect-web: %s" % " ".join(args))
            self._spotify_proc = subprocess.Popen(args, cwd=exec_dir)
        else:
            self._spotify_proc = subprocess.Popen(args, cwd=exec_dir, stdout=DEVNULL, stderr=subprocess.STDOUT)

        while not self._exit.isSet():
            cur_state = self._get_state()
            if cur_state != self._last_state:
                self._last_state = cur_state
                self._update_metadata()
            if cur_state == "playing":
                self._update_metadata()
                LOOP_WAIT = 0.5
            else:
                LOOP_WAIT = 3
            if self._spotify_proc.returncode and self._spotify_proc.returncode > 0 and not self._exit:
                # daemon crashed ? restart ?
                LOGGER.error("spotify-connect-web exited")
                break
            self._exit.wait(LOOP_WAIT)
        