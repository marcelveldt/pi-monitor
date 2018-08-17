#!/usr/bin/env python
# -*- coding: utf-8 -*-


import os
import time
import threading
import thread
import subprocess
from resources.lib.utils import PlayerMetaData, json, DEVNULL, HOSTNAME, requests, PLATFORM, run_proc, check_software, RESOURCES_FOLDER, VOLUME_CONTROL_DISABLED, PAUSED_STATE, PLAYING_STATE, STOPPED_STATE
import socket 

"""
    SpotifyPlayer
    player implementation for Spotify
    we use librespot altough it doesn't support hardware volume control
"""


def setup(monitor):
    '''setup the module'''
    if not ("armv6" in PLATFORM or "armv7" in PLATFORM):
        LOGGER.error("unsupported platform! %s" % PLATFORM)
        return False
    if not monitor.config.get("ENABLE_MODULE_SPOTIFY", False):
        LOGGER.warning("Spotify module is not enabled!")
        return False
    return SpotifyPlayer(monitor)



class SpotifyPlayer(threading.Thread):
    _exit = threading.Event()
    _last_state = None
    _spotify_proc = None
    _spotify_socket = None
    _token = None

    def __init__(self, monitor):
        self.monitor = monitor
        self.monitor.states["spotify"] = PlayerMetaData("Spotify")
        run_proc("service spotify-connect-web stop", check_result=False, ignore_error=True) # make sure that the original service is stopped
        run_proc("service raspotify stop", check_result=False, ignore_error=True) # make sure that the original service is stopped
        threading.Thread.__init__(self)
        
    def stop(self):
        self._exit.set()
        if self._spotify_proc:
            self._spotify_proc.terminate()
        if self._spotify_socket:
            self._spotify_socket.stop()
        threading.Thread.join(self, 2)

    def command(self, cmd, cmd_data=None):
        ''' send command to player'''
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
        return self._api_post("me/player/%s" % cmd)

    def _api_request(self, endpoint, params=None):
        '''get info from json api'''
        result = {}
        url = "https://api.spotify.com/v1/%s" % endpoint
        params = params if params else {}
        try:
            headers = {"Authorization: Bearer": self._token["accessToken"]}
            response = requests.get(url, params=params, headers=headers, timeout=10)
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

    def _api_post(self, endpoint, params=None):
        '''get info from json api'''
        result = {}
        url = "https://api.spotify.com/v1/%s" % endpoint
        params = params if params else {}
        try:
            headers = {"Authorization: Bearer": self._token["accessToken"]}
            response = requests.post(url, data=params, headers=headers, timeout=2)
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

    def _event_callback(self, event, data):
        ''' event received from socket to librespot'''
        LOGGER.info("Got event from librespot: %s" % event)
        if event == "metadata":
            albumartId = data["albumartId"][2] if len(data["albumartId"]) > 1 else data["albumartId"][0]
            self.monitor.states["spotify"].update({
                    "title": data["track_name"],
                    "artist": data["artist_name"],
                    "album": data["album_name"],
                    "duration": data["duration_ms"]/1000,
                    "cover_url": "https://i.scdn.co/image/%s" % albumartId
                })
        elif event == "token":
            self._token = data
        elif event == "kSpPlaybackNotifyBecameActive":
            self.monitor.states["spotify"]["state"] = PAUSED_STATE
        elif event == "kSpDeviceActive":
            self.monitor.states["spotify"]["state"] = PAUSED_STATE
        elif event == "kSpDeviceInactive":
            self.monitor.states["spotify"]["state"] = STOPPED_STATE
        elif event == "kSpSinkActive":
            self.monitor.states["spotify"]["state"] = PLAYING_STATE
        elif event == "kSpSinkInactive":
            self.monitor.states["spotify"]["state"] = PAUSED_STATE
        elif event == "kSpPlaybackNotifyBecameInactive":
            self.monitor.states["spotify"]["state"] = STOPPED_STATE

    def run(self):
        # finally start the librespot executable
        exec_path = os.path.join(RESOURCES_FOLDER, "spotify", "librespot")
        args = [exec_path, "--bitrate", "320", "--name", HOSTNAME, "--initial-volume", "100"]
        # if self.monitor.config["ALSA_VOLUME_CONTROL"] and self.monitor.config["ALSA_VOLUME_CONTROL"] != VOLUME_CONTROL_DISABLED:
        #     args += ["--mixer", self.monitor.config["ALSA_VOLUME_CONTROL"]]
        if self.monitor.config["ALSA_SOUND_DEVICE"]:
            args += ["--backend", "alsa", "--device", self.monitor.config["ALSA_SOUND_DEVICE"]]
        if self.monitor.config.get("SPOTIFY_VOLUME_NORMALISATION", False):
            args += ["--enable-volume-normalisation"]
        if self.monitor.config["ENABLE_DEBUG"]:
            LOGGER.debug("Starting librespot: %s" % " ".join(args))
            self._spotify_proc = subprocess.Popen(args)
        else:
            self._spotify_proc = subprocess.Popen(args, stdout=DEVNULL, stderr=subprocess.STDOUT)
        # start socket connection to listen for events
        self._spotify_socket = SpotifySocket(self._event_callback)
        self._spotify_socket.start()

        loop_wait = 1200
        while not self._exit.isSet():
            if self._spotify_proc.returncode and self._spotify_proc.returncode > 0 and not self._exit:
                # daemon crashed ? restart ?
                LOGGER.error("librespot exited ?!")
                break
            self._exit.wait(loop_wait) # we just wait as we'll be notified of updates through the socket


class SpotifySocket(threading.Thread):
    _exit = threading.Event()
    _socket = None
    def __init__(self, callback):
        self.callback = callback
        threading.Thread.__init__(self)
        self.daemon = True

    def stop(self):
        self._exit.set()
        if self._socket:
            self._socket.close()
        threading.Thread.join(self, 2)

    def run(self, host='127.0.0.1', port=5030):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        LOGGER.info("Listening on udp %s:%s" % (host, port))
        s.bind((host, port))
        while not self._exit.isSet():
            (data, addr) = s.recvfrom(128*1024)
            LOGGER.debug("received from %s --> %s" %(addr, data))
            event = ""
            data = data.decode("utf-8")
            if data.startswith("{"):
                data = json.loads(data)
                if "token" in data:
                    event = "token"
                    data = data["token"]
                elif "metadata" in data:
                    event = "metadata"
                    data = data["metadata"]
            else:
                event = data
            self.callback(event, data)
        