#!/usr/bin/env python

import logging
import os
import signal
import sys
import time
import thread
import threading
from Queue import Queue
import datetime
from resources.lib.utils import DEVNULL, PlayerMetaData, StatesDict, ConfigDict, HOSTNAME, APPNAME, json, import_or_install, run_proc, IS_DIETPI, PLAYING_STATES


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_PATH = os.path.join(BASE_DIR, "modules")
logformat = logging.Formatter('%(asctime)-15s %(levelname)-5s  %(module)s -- %(message)s')
LOGGER = logging.getLogger(APPNAME)

filehandler = logging.FileHandler(filename='/tmp/pi-monitor.log', filemode='w')
filehandler.setFormatter(logformat)
LOGGER.addHandler(filehandler)
consolehandler = logging.StreamHandler()
consolehandler.setFormatter(logformat)
LOGGER.addHandler(consolehandler)
LOGGER.setLevel(logging.INFO)

CONFIG_FILE = '/etc/pi-monitor.json'

import_or_install("alsaaudio", installapt="libasound2-dev", installpip="pyalsaaudio")

class Monitor():
    states = StatesDict()
    config = ConfigDict()
    _cmd_queue = Queue()
    _event = threading.Event()
    _loaded_modules = []
    _exit = False
    _state_watcher = None
    
    #### PUBLIC CLASS METHODS ############################

    @property
    def is_playing(self):
        return self.player_info["state"] in PLAYING_STATES

    @property
    def player_info(self):
        return self.states["player"]

    def command(self, target, cmd, data=None, blocking=False):
        '''put command in the queue'''
        if blocking:
            self._process_command(target, cmd, data)
        else:
            self._cmd_queue.put((target, cmd, data))
            self._event.set()

    def set_volume(self, vol_level):
        self.command("player", "set_volume", vol_level)

    def register_state_callback(self, callback, filter=None):
        '''allow modules to listen for state changed events'''
        self._state_watcher.register_state_callback(callback, filter)

    def deregister_state_callback(self, callback, keyfilter=None):
        self._state_watcher.deregister_state_callback(callback, keyfilter)

    def get_module(self, module_name):
        ''' allow optional module to access another module'''
        item = [item for item in self._loaded_modules if 
                (item.__module__ == module_name) or item.__module__ == "modules." + module_name]
        item = item[0] if item else None
        if not item:
            self._setup_module(module_name)
            item = [item for item in self._loaded_modules if 
                    (item.__module__ == module_name) or item.__module__ == "modules." + module_name]
            item = item[0] if item else None
        return item

    #### PRIVATE CLASS METHODS #################

    def __init__(self, *args, **kwargs):
        LOGGER.info("Starting %s" % APPNAME)

        if not IS_DIETPI:
            LOGGER.warning("WARNING: You're not running this on DietPi, there is no guarantee that all functionality works as expected!")

        # start states watcher
        self._state_watcher = StatesWatcher(self)
        self.states["modules"] = []
        self._state_watcher.start()

        # parse config from file
        self.config.update(self._parseconfig())
        self._lastconfig = self.config.copy()
        self.states["player"] = PlayerMetaData("")
        self.states["player"].update({
                "power": False, 
                "current_player": "",
                "players": [],
                "interrupted_player": ""
                })
        
        # Initialise logger
        if self.config["ENABLE_DEBUG"]:
            LOGGER.setLevel(logging.DEBUG)
            LOGGER.debug("config: %s" % self.config)

        # Use the signal module to handle signals
        for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT]:
            signal.signal(sig, self._cleanup)

        # setup (optional) modules
        self._setup_modules()
        # set default/startup volume if needed
        if self.config["STARTUP_VOLUME"]:
            self.command("player", "set_volume", self.config["STARTUP_VOLUME"])
        # start the main loop
        self.command("player","ping")
        loop_timeout = 1200
        while not self._exit:
            try:
                # process commands queue
                while not self._cmd_queue.empty():
                    data = self._cmd_queue.get()
                    self._process_command(*data)
            except Exception:
                LOGGER.exception("Error while processing Queue - %s" % str(data))
            # wait for events in the queue
            self._event.wait(loop_timeout)
            self._event.clear()

    def _process_command(self, target, cmd, cmd_data=None):
        ''' process command from the queue '''
        if target == "player":
            # redirect player commands
            self._player_command(cmd, cmd_data)
        elif target == "power":
            # power commands
            if cmd == "power":
                self._set_power(cmd_data)
            elif cmd == "poweron":
                self._set_power(True)
            elif cmd == "poweroff":
                self._set_power(False, cmd_data)
        elif target == "system":
            # system commands
            if cmd == "saveconfig":
                self._saveconfig()
            elif cmd == "run_proc" and cmd_data:
                run_proc(cmd_data)
            elif cmd == "restart":
                self._cleanup(2, 2, True)
            elif cmd == "reload":
                LOGGER.info("Restart of service requested!")
                self._cleanup(2, 2)
            elif cmd == "shutdown":
                self._cleanup(0, 2, False)
        elif target and cmd:
            # direct command to module
            mod = self.get_module(target)
            if mod and getattr(mod, "command"):
                mod.command(cmd, cmd_data)
            else:
                LOGGER.warning("module %s does not accept commands or is not loaded!" % target)
    
    def _player_command(self, cmd, cmd_data=None):
        ''' send command to player'''
        if cmd in ["next", "nexttrack", "next_track"]:
            cmd = "next"
        elif cmd in ["previous", "prev", "previous_track", "previoustrack"]:
            cmd = "next"
        elif cmd in ["toggle", "toggleplaypause", "toggleplay", "togglepause"]:
            cmd = "pause" if self.is_playing else "play"
        elif cmd in ["volup", "volumeup", "volume_up"]:
            return self._volume_up()
        elif cmd in ["voldown", "volumedown", "volume_down"]:
            return self._volume_down()
        elif cmd in ["volume", "setvolume", "volume_set", "set_volume"]:
            return self._volume_set(cmd_data)
        elif cmd == "play_sound":
            return self._play_sound(cmd_data)
        elif cmd == "ping":
            return self._play_ping()
        # redirect command to current player
        if self.states["player"]["current_player"]:
            player_mod = self.get_module(self.states["player"]["current_player"])
            player_mod.command(cmd, cmd_data)

    def _volume_up(self):
        if self.states["player"].get("volume_limiter"):
            LOGGER.warning("volume limiter is active!")
            return
        success = False
        # send command to current player
        if self.is_playing:
            player_mod = self.get_module(self.states["player"]["current_player"])
            success = player_mod.command("volume_up")
        # fallback to direct alsa control
        if not success and self.config["ALSA_VOLUME_CONTROL"]:
            run_proc('amixer set "%s" 2+' % self.config["ALSA_VOLUME_CONTROL"])
        thread.start_new_thread(self._check_volume_limiter, ())

    def _volume_down(self):
        if self.states["player"].get("volume_limiter"):
            LOGGER.warning("volume limiter is active!")
            return
        success = False
        # send command to current player
        if self.is_playing:
            player_mod = self.get_module(self.states["player"]["current_player"])
            success = player_mod.command("volume_down")
        # fallback to direct alsa control
        if not success and self.config["ALSA_VOLUME_CONTROL"]:
            run_proc('amixer set "%s" 2-' % self.config["ALSA_VOLUME_CONTROL"])
        self.states["player"]["volume_limiter"] = False

    def _volume_set(self, volume_level):
        ''' set volume level '''
        now = datetime.datetime.now()
        volume_limiter = False
        if self.config["VOLUME_LIMITER_MORNING"] and (now.hour > 0 and now.hour < 8):
            if volume_level >= self.config["VOLUME_LIMITER_MORNING"]:
                volume_limiter = True
        elif self.config["VOLUME_LIMITER"]:
            if volume_level >= self.config["VOLUME_LIMITER_MORNING"]:
                volume_limiter = True
        if volume_limiter:
            LOGGER.warning("requested volume level is above the limiter treshold, ignoring request")
            return False
        success = False
        # send command to current player
        if self.is_playing:
            player_mod = self.get_module(self.states["player"]["current_player"])
            success = player_mod.command("volume_set", volume_level)
        # fallback to direct alsa control
        if not success and self.config["ALSA_VOLUME_CONTROL"]:
            run_proc('amixer set "%s" %s' % (self.config["ALSA_VOLUME_CONTROL"], str(volume_level) + "%"))
        self.states["player"]["volume_level"] = volume_level
        self.states["player"]["volume_limiter"] = False

    def _volume_get(self):
        ''' get current volume level of player'''
        vol_level = 0
        current_player = self.states["player"]["current_player"]
        if current_player and self.states[current_player]["state"] == "playing":
            vol_level = self.states[current_player]["volume_level"]
        # fallback to alsa
        if not vol_level and self.config["ALSA_VOLUME_CONTROL"]:
            amixer_result = run_proc('amixer get "%s"' % self.config["ALSA_VOLUME_CONTROL"], True)
            if amixer_result:
                cur_vol = amixer_result.split("[")[1].split("]")[0].replace("%","")
                vol_level = int(cur_vol)
        self.states["player"]["volume_level"] = vol_level
        return vol_level

    def _check_volume_limiter(self):
        '''check if volume limiter should be enabled'''
        cur_vol = self._volume_get()
        now = datetime.datetime.now()
        volume_limiter = False
        if self.config["VOLUME_LIMITER_MORNING"] and (now.hour > 0 and now.hour < 8):
            if cur_vol >= self.config["VOLUME_LIMITER_MORNING"]:
                volume_limiter = True
        elif self.config["VOLUME_LIMITER"]:
            if cur_vol >= self.config["VOLUME_LIMITER_MORNING"]:
                volume_limiter = True
        self.states["player"]["volume_limiter"] = volume_limiter

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

    def _play_alarm_sound(self):
        ''' play loud beep through speakers for signalling errors'''
        self._play_ping()
        self._play_ping()
        self._play_ping()

    def _play_ping(self, alt_sound=False):
        ''' play ping or buzz sound'''
        if "GPIO_BUZZER_PIN" in self.config and self.config["GPIO_BUZZER_PIN"]:
            self.set_gpio(self.config["GPIO_BUZZER_PIN"], 1)
            time.sleep(0.2)
            self.set_gpio(self.config["GPIO_BUZZER_PIN"], 0)
        else:
            filename = os.path.join(BASE_DIR, 'resources', 'ding.wav')
            if alt_sound:
                filename = os.path.join(BASE_DIR, 'resources', 'dong.wav')
            self._play_sound(filename)

    def _set_power(self, player_powered, stop_players=False):
        if isinstance(player_powered, (str, unicode)):
            player_powered = player_powered in ["on", "ON", "true", "True", "1"]
        ''' turn on/off player '''
        if not player_powered and stop_players:
            # stop any active players
            for player in self.states["player"]["players"]:
                if self.states[player]["state"] not in ["off", "stopped", "", "paused", "loading"]:
                    self.get_module(player).command("stop")
        self.states["player"]["power"] = player_powered

    def _setup_modules(self):
        '''load all optional modules'''
        for item in os.listdir(MODULES_PATH):
            if (os.path.isfile(os.path.join(MODULES_PATH, item)) and 
                not item.startswith("_") and 
                item.endswith('.py')
                and not item.startswith('.')):
                name = item.replace(".py","")
                self._setup_module(name)

    def _setup_module(self, module_name):
        '''load optional module'''
        mod_exists = [item for item in self._loaded_modules if 
                (item.__module__ == module_name) or item.__module__ == "modules." + module_name]
        if mod_exists:
            LOGGER.debug("Module is already loaded: %s" % module_name)
            return
        LOGGER.debug("Loading module %s" % module_name)
        try:
            mod = __import__("modules." + module_name, fromlist=[''])
            mod.LOGGER = LOGGER
            mod = mod.setup(self)
            if mod:
                self._loaded_modules.append(mod)
                cls_name = mod.__class__.__name__
                self.states["modules"].append(cls_name)
                if "Player" in cls_name:
                    self.states["player"]["players"].append(module_name)
                mod.start()
                LOGGER.info("Successfully initialized module %s" % cls_name)
        except Exception as exc:
            LOGGER.exception("Error loading module %s: %s" %(module_name, exc))

    def _unload_modules(self):
        for module in self._loaded_modules:
            try:
                mod_name = module.__class__.__module__.replace("modules.","")
                cls_name = module.__class__.__name__
                LOGGER.debug("Stopping module %s" % mod_name)
                module.stop()
            except Exception as exc:
                LOGGER.exception("Error while unloading module %s" % mod_name)

    def _cleanup(self, signum, frame):
        """
        Signal handler to ensure we disconnect cleanly
        in the event of a SIGTERM or SIGINT.
        """
        self._exit = True
        self._event.set()
        self._state_watcher.stop()

        # stop all loaded optional modules
        self._unload_modules()

        #turn off power
        self._set_power(False)
        
        # Exit from our application
        LOGGER.info("Exiting on signal %d" % (signum))
        sys.exit(signum)

    def _saveconfig(self):
        config_changed = self._lastconfig["last_updated"] != self.config["last_updated"]
        if config_changed:
            LOGGER.info("The configuration is changed! We need to reload.")
            self._lastconfig = self.config
            with open(CONFIG_FILE, "w") as json_file:
                json_file.write(self.config.json)
            self.command("system", "reload")
            #self.command("system", "restart")
        else:
            LOGGER.info("Configuration did not change!")

    def _get_alsa_config(self):
        ''' get details about the alsa configuration'''
        alsa_mixers = alsaaudio.mixers()
        self.states["alsa_mixers"] = alsa_mixers
        default_mixer = alsa_mixers[0].decode("utf-8")
        if "Digital" in alsa_mixers:
            default_mixer = u"Digital"
        elif "PCM" in alsa_mixers:
            default_mixer = u"PCM"
        elif "Analog" in alsa_mixers:
            default_mixer = u"Analog"
        elif "Lineout volume control" in alsa_mixers:
            default_mixer = u"Lineout volume control"
        elif "SoftMaster" in alsa_mixers:
            default_mixer = u"SoftMaster"
        elif "Master" in alsa_mixers:
            default_mixer = u"Master"
        default_audio_device = "default"
        alsa_devices = alsaaudio.pcms(alsaaudio.PCM_PLAYBACK)
        if 'null' in alsa_devices:
            alsa_devices.remove('null')
        self.states["alsa_devices"] = alsa_devices
        LOGGER.debug("alsa devices: %s" % str(alsa_devices))
        for device in alsa_devices:
            if "default:" in device:
                default_audio_device = device
                break
        alsa_devices = alsaaudio.pcms(alsaaudio.PCM_CAPTURE)
        if 'null' in alsa_devices:
            alsa_devices.remove('null')
        self.states["alsa_capture_devices"] = alsa_devices
        LOGGER.debug("alsa capture devices: %s" % str(alsa_devices))
        return default_audio_device, default_mixer

    def _parseconfig(self):
        ''' get config from player's json configfile'''
        CONFIG_FILE = '/etc/pi-monitor.json'
        try:
            with open(CONFIG_FILE) as json_file:
                json_data = json_file.read()
                config = json.loads(json_data.decode("utf-8"))
        except Exception as exc:
            # Error while reading config file, starting with defaults...
            config = {}
        default_audio_device, default_mixer = self._get_alsa_config()
        result = ConfigDict([
            ("ALSA_VOLUME_CONTROL", config.get("ALSA_VOLUME_CONTROL", default_mixer)),
            ("ALSA_SOUND_DEVICE", config.get("ALSA_SOUND_DEVICE", default_audio_device)),
            ("STARTUP_VOLUME", config.get("STARTUP_VOLUME",0)),
            ("NOTIFY_VOLUME", config.get("NOTIFY_VOLUME", 60)),
            ("VOLUME_LIMITER", config.get("VOLUME_LIMITER",0)),
            ("VOLUME_LIMITER_MORNING", config.get("VOLUME_LIMITER_MORNING",0)),            
            ("ENABLE_DEBUG", config.get("ENABLE_DEBUG", False))
        ])
        # append other config keys which are set by modules
        for key, value in config.items():
            if key not in result:
                result[key] = value
        return result
        
class StatesWatcher(threading.Thread):
    _exit = False
    _event = threading.Event()
    _callback = None
    _event_queue = Queue()
    _state_listeners = []

    def __init__(self, monitor):
        self.monitor = monitor
        self.states = monitor.states
        threading.Thread.__init__(self)
        self.monitor.states.state_listener = self._state_callback
        self.monitor.config.state_listener = self._state_callback

    def run(self):
        while not self._exit:
            try:
                # process queue
                while not self._event_queue.empty():
                    data = self._event_queue.get()
                    self._handle_state_event(data)
            except Exception:
                LOGGER.exception("Error while processing Queue - %s" % str(data))
            # wait for events in the queue
            self._event.wait(1200)
            self._event.clear()

    def stop(self):
        self._exit = True
        self._event.set()
        self.join(1)

    def register_state_callback(self, callback, filter=None):
        '''allow modules to listen for state changed events'''
        self._state_listeners.append((callback, filter))

    def deregister_state_callback(self, callback, keyfilter=None):
        item = (callback, keyfilter)
        try:
            self._state_listeners.remove(item)
        except Exception:
            LOGGER.exception("error while deregistering callback")

    def _state_callback(self, event_data):
        '''put state update in the queue'''
        self._event_queue.put(event_data)
        self._event.set()

    def _handle_state_event(self, event_data):
        '''handle a state changed event on the command bus'''
        LOGGER.debug("state changed! %s - " % str(event_data))
        key = event_data[0]
        subkey = event_data[2]
        for state_listener in self._state_listeners:
            key_filter = state_listener[1]
            if not key_filter or key_filter == key:
                # pass event to attached listeners in seperate threads to make sure nothing is blocking
                try:
                    thread.start_new_thread(state_listener[0], (event_data))
                except Exception:
                    LOGGER.exception("failed to handle state event")
        if "player" in self.states and "players" in self.states["player"]: # check is needed because of initialization order
            if key in self.states["player"]["players"]:
                # we received an update from one of the players
                self._handle_player_state_change(key)
            
    def _handle_player_state_change(self, player_key):
        ''' handle state changed event for the mediaplayers'''
        cur_player = self.states["player"]["current_player"]
        if ((cur_player != player_key and self.states[player_key]["state"] in ["playing", "listening"]) or 
                (not cur_player and self.states[player_key]["state"] in ["paused", "idle"])):
            # we have a new active player
            self.states["player"]["current_player"] = player_key
            LOGGER.info("active player changed to %s" % player_key)
            # signal any other players about this so they must stop playing
            for player in self.states["player"]["players"]:
                if self.states[player]["state"] not in ["off", "stopped", "standby", ""] and player != player_key:
                    self.monitor.command(player, "stop")
        # metadadata update of current player
        if player_key == self.states["player"]["current_player"]:
            self.states["player"].update(self.states[player_key])
        # turn player on if needed
        if not self.states["player"]["power"] and self.states["player"]["state"] in PLAYING_STATES:
            self.monitor.command("power", "poweron")
    

# main entry point
Monitor()
