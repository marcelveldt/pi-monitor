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
from resources.lib.utils import RESOURCES_FOLDER, DEVNULL, PlayerMetaData, StatesDict, ConfigDict, HOSTNAME, APPNAME, json, import_or_install, run_proc, IS_DIETPI, PLAYING_STATES, VOLUME_CONTROL_SOFT, VOLUME_CONTROL_DISABLED, PLAYING_STATE, INTERRUPT_STATES, IDLE_STATES, PAUSED_STATE, IDLE_STATE, ALERT_STATE


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODULES_PATH = os.path.join(BASE_DIR, "modules")
logformat = logging.Formatter('%(asctime)-15s %(levelname)-5s  %(module)s -- %(message)s')
LOGGER = logging.getLogger(APPNAME)

filehandler = logging.FileHandler('/tmp/pi-monitor.log', 'w')
filehandler.setFormatter(logformat)
LOGGER.addHandler(filehandler)
consolehandler = logging.StreamHandler()
consolehandler.setFormatter(logformat)
LOGGER.addHandler(consolehandler)
LOGGER.setLevel(logging.INFO)

CONFIG_FILE = '/etc/pi-monitor.json'


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
        self._lastconfig = self.config["last_updated"]
        if self.config["ENABLE_DEBUG"]:
            LOGGER.setLevel(logging.DEBUG)
            LOGGER.debug("using config: %s" % self.config)
        self.states["player"] = PlayerMetaData("")
        self.states["player"].update({
                "power": False, 
                "current_player": "",
                "players": [],
                "interrupted_player": "",
                "interrupted_volume": 0,
                "volume_level": 0
                })
        # Use the signal module to handle signals
        for sig in [signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT]:
            signal.signal(sig, self._cleanup)

        # setup (optional) modules
        self._setup_modules()
        # set default/startup volume if needed
        if self.config["STARTUP_VOLUME"]:
            self.command("player", "set_volume", self.config["STARTUP_VOLUME"])
        # start the main loop
        self.command("system","ping")
        loop_timeout = 1200
        while not self._exit:
            # process commands queue
            while not self._cmd_queue.empty() and not self._exit:
                data = self._cmd_queue.get()
                # fire each command in it's own seperate thread
                #data = *data
                thread.start_new_thread(self._process_command, (data))
            # wait for events in the queue
            if self._exit:
                break
            self._event.wait(loop_timeout)
            self._event.clear()

    def _process_command(self, target, cmd, cmd_data=None):
        ''' process command from the queue '''
        try:
            LOGGER.debug("processing command %s for target %s with data %s" %(cmd, target, str(cmd_data)))
            if target == "player":
                # redirect player commands
                self._player_command(cmd, cmd_data)
            elif target == "power":
                # power commands
                if cmd == "power":
                    self._set_power(cmd_data)
                elif cmd == "poweron":
                    self._set_power(True, cmd_data)
                elif cmd == "poweroff":
                    self._set_power(False, cmd_data)
            elif target == "system":
                # system commands
                if cmd == "saveconfig":
                    self._saveconfig()
                elif cmd == "run_proc" and cmd_data:
                    run_proc(cmd_data)
                elif cmd == "restart":
                    LOGGER.warning("System will now reboot!")
                    os.system("reboot")
                elif cmd == "reload":
                    LOGGER.info("Restart of service requested!\n")
                    #self._cleanup(15, 15)
                    os.kill(os.getpid(), 15)
                elif cmd in ["ping", "beep", "buzz"]:
                    self._beep(cmd_data)
            elif target and cmd:
                # direct command to module
                mod = self.get_module(target)
                result = mod.command(cmd, cmd_data)
                LOGGER.debug("redirected command %s with data %s to module %s with result %s" %(cmd, str(cmd_data), target, result))
        except Exception:
            LOGGER.exception("error while executing command %s for target %s" %(cmd, target))
    
    def _player_command(self, cmd, cmd_data=None):
        ''' send command to player'''
        if cmd in ["next", "nexttrack", "next_track"]:
            cmd = "next"
        elif cmd in ["previous", "prev", "previous_track", "previoustrack"]:
            cmd = "next"
        elif cmd in ["toggle", "toggleplaypause", "toggleplay", "togglepause", "playpause"]:
            cmd = "pause" if self.is_playing else "play"
        elif cmd in ["volup", "volumeup", "volume_up"]:
            cmd = "volume_up"
        elif cmd in ["voldown", "volumedown", "volume_down"]:
            cmd = "volume_down"
        elif cmd in ["volume", "setvolume", "volume_set", "set_volume"]:
            cmd = "volume_set"
        elif cmd in ["play_sound", "play_url", "play_media", "media_play"]:
            cmd = "play_media"
        elif cmd in ["play_notify", "notify", "play_notification"]:
            cmd = "play_notification"
        elif cmd in ["play_alert", "alert", "play_alarm", "alarm"]:
            cmd = "play_alert"
        elif cmd in ["ping", "beep", "buzz"]:
            return self._beep(cmd_data)
        # redirect command to current player
        cur_player = self.states["player"]["current_player"]
        success = False
        if "volume" in cmd and self.states["player"]["state"] != PLAYING_STATE:
            # prefer direct alsa control of volume
            LOGGER.debug("forward command %s to alsa" % cmd)
            self.get_module("alsa").command(cmd, cmd_data)
        elif cur_player:
            # all other commands will be forwarded to the current player
            LOGGER.debug("forward command %s with data %s to player %s" %(cmd, str(cmd_data), cur_player))
            player_mod = self.get_module(cur_player)
            success = player_mod.command(cmd, cmd_data)
            if not success:
                LOGGER.warning("unable to process command %s on player %s" % (cmd, cur_player))
        if not success:
            # fallback to direct alsa control for volume commands
            if "volume" in cmd:
                LOGGER.debug("forward command %s to alsa" % cmd)
                self.get_module("alsa").command(cmd, cmd_data)
            # fallback to local player for other commands
            else:
                LOGGER.debug("forward command %s to localplayer" % cmd)
                if not self.get_module("localplayer").command(cmd, cmd_data):
                    LOGGER.warning("unable to process command %s on localplayer" % (cmd))

    def _beep(self, alt_sound=False):
        ''' play beep through gpio buzzer or speakers '''
        if "GPIO_BUZZER_PIN" in self.config and self.config["GPIO_BUZZER_PIN"]:
            self.get_module("gpio").command("beep", alt_sound)
        elif self.player_info["state"] != PLAYING_STATE:
            filename = os.path.join(RESOURCES_FOLDER, 'ding.wav')
            if alt_sound:
                filename = os.path.join(RESOURCES_FOLDER, 'dong.wav')
            # play sound with sox (ignore if it fails)
            run_proc("/usr/bin/play %s" % filename, check_result=False, ignore_error=True)

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
        '''load all modules from the modules directory'''
        for item in ["alsa", "localplayer", "webconfig"]:
            # first load our required modules (they will be ignored if already imported later on)
            self._setup_module(item)
        # load all other modules
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
                mod.daemon = True # needed to properly exit all child processes at signals
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

        LOGGER.info("Exit requested!")
        self._saveconfig(False, True)

        #turn off power
        self._set_power(False)

        self._event.set()
        self._state_watcher.stop()

        # stop all loaded optional modules
        self._unload_modules()
        
        # Exit from our application
        LOGGER.info("Exiting on signal %d" % (signum))
        sys.exit(signum)

    def _saveconfig(self, autoreload=True, force=False):
        config_changed = self._lastconfig != self.config["last_updated"]
        if config_changed or force:
            self._lastconfig = self.config
            with open(CONFIG_FILE, "w") as json_file:
                json_file.write(self.config.json)
            if autoreload:
                LOGGER.info("The configuration is changed! We need to reload.")
                self.command("system", "reload")
        else:
            LOGGER.info("Configuration did not change!")

    def _parseconfig(self):
        ''' get config from player's json configfile'''
        try:
            with open(CONFIG_FILE) as json_file:
                json_data = json_file.read()
                config = json.loads(json_data.decode("utf-8"))
        except Exception as exc:
            # Error while reading config file, starting with defaults...
            config = {}
        result = ConfigDict([
            ("STARTUP_VOLUME", config.get("STARTUP_VOLUME",0)),
            ("NOTIFY_VOLUME", config.get("NOTIFY_VOLUME", 40)),
            ("ALERT_VOLUME", config.get("ALERT_VOLUME", 70)),
            ("VOLUME_LIMITER", config.get("VOLUME_LIMITER",0)),
            ("VOLUME_LIMITER_MORNING", config.get("VOLUME_LIMITER_MORNING",0)),            
            ("ENABLE_DEBUG", config.get("ENABLE_DEBUG", False)),
            ("AUTO_UPDATE_ON_STARTUP", config.get("AUTO_UPDATE_ON_STARTUP", True))
        ])
        # append other config keys which are set by modules
        for key, value in config.items():
            if key not in result:
                result[key] = value
        # save first config file
        if not os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "w") as json_file:
                json_file.write(result.json)
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
                LOGGER.exception("Error while processing StatesQueue - %s" % str(data))
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
        if "volume_level" in [key, subkey]:
            self._handle_volume_limiter()

    def _handle_player_state_change(self, player_key):
        ''' handle state changed event for the mediaplayers'''
        cur_player = self.states["player"]["current_player"]
        cur_player_state = self.states[cur_player]["state"] if cur_player else IDLE_STATE
        new_player = player_key
        new_player_state = self.states[player_key]["state"]
        update_info = {}

        # if we don't have a current player and a (new) player starts playing or if we have an idle current player and the new player is not idle (e.g. paused etc.)
        if ((cur_player != new_player and new_player_state in PLAYING_STATES) or 
                (cur_player_state == IDLE_STATE and new_player_state != IDLE_STATE)):
            # we have a new active player!
            self.states["player"]["current_player"] = new_player
            LOGGER.info("active player changed to %s" % new_player)
            
            # signal current player about this so it must stop playing
            if new_player_state in PLAYING_STATES and cur_player_state in PLAYING_STATES:
                self.monitor.get_module(cur_player).command("stop")
                # todo: handle flush of audio device if needed ?
            
            # handle notifications and alerts
            if new_player_state in INTERRUPT_STATES:
                # a notification or alert started, store previous player and set notification volume level
                self.states["player"]["interrupted_player"] = cur_player
                self.states["player"]["interrupted_volume"] = self.monitor.get_module("alsa").volume
                self.states["player"]["interrupted_state"] = cur_player_state
                if new_player_state == ALERT_STATE and self.monitor.config["ALERT_VOLUME"]:
                    self.monitor.get_module("alsa").command("volume_set", self.monitor.config["ALERT_VOLUME"])
                elif self.monitor.config["NOTIFY_VOLUME"]:
                    self.monitor.get_module("alsa").command("volume_set", self.monitor.config["NOTIFY_VOLUME"])
        
        # the notification/alert stopped, restore the previous state
        elif (self.states["player"]["interrupted_player"] and 
                    player_key == cur_player and new_player_state in IDLE_STATES):
            self.monitor.get_module("alsa").command("volume_set", self.states["player"]["interrupted_volume"])
            if self.states["player"]["interrupted_state"] == PLAYING_STATE:
                self.monitor.command(self.states["player"]["interrupted_player"], "play")
            self.states["player"]["current_player"] = self.states["player"]["interrupted_player"]
            self.states["player"]["interrupted_volume"] = 0
            self.states["player"]["interrupted_player"] = ""
            self.states["player"]["interrupted_state"] = ""
        
        # metadadata update of current player
        cur_player = self.states["player"]["current_player"]
        if cur_player:
            self.states["player"].update(self.states[cur_player])
        
        # turn player on if needed
        if not self.states["player"]["power"] and self.states["player"]["state"] in PLAYING_STATES:
            self.monitor.command("power", "poweron")
    
    def _handle_volume_limiter(self):
        ''' check if volume_level is higher than the allowed setting '''
        cur_vol = self.states["player"]["volume_level"]
        now = datetime.datetime.now()
        if self.monitor.config["VOLUME_LIMITER_MORNING"] and (now.hour > 0 and now.hour < 9):
            if cur_vol > self.monitor.config["VOLUME_LIMITER_MORNING"]:
                self.monitor.command("player", "volume_set", self.monitor.config["VOLUME_LIMITER_MORNING"])
                LOGGER.warning("volume limiter is active!")
        elif self.monitor.config["VOLUME_LIMITER"]:
            if cur_vol > self.monitor.config["VOLUME_LIMITER"]:
                self.monitor.command("player", "volume_set", self.monitor.config["VOLUME_LIMITER"])
                LOGGER.warning("volume limiter is active!")

# main entry point
Monitor()
