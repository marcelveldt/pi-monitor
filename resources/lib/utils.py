#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
import subprocess
import socket
import logging
import inspect
import sys
import platform
import os

try:
    from subprocess import DEVNULL # py3k
except ImportError:
    import os
    DEVNULL = open(os.devnull, 'wb')


HOSTNAME = u"%s" % socket.gethostname()
APPNAME = "pi-monitor"
LOGGER = logging.getLogger(APPNAME)
PLATFORM = platform.machine()
IS_DIETPI = os.path.isfile("/DietPi/dietpi/.hw_model")


PAUSED_STATE = "paused"
PLAYING_STATE = "playing"
NOTIFY_STATE = "notification"
LISTENING_STATE = "playing"
LOADING_STATE = "loading"
STOPPED_STATE = "stopped"
IDLE_STATE = "idle"
OFF_STATE = "off"
IDLE_STATES = [STOPPED_STATE, IDLE_STATE, OFF_STATE, PAUSED_STATE]
PLAYING_STATES = [PLAYING_STATE, LISTENING_STATE, LOADING_STATE, NOTIFY_STATE]

LIBS_FOLDER = os.path.dirname(os.path.abspath(__file__))
RESOURCES_FOLDER = os.path.abspath(os.path.join(LIBS_FOLDER, os.pardir))
MODULES_FOLDER = os.path.join(os.path.abspath(os.path.join(RESOURCES_FOLDER, os.pardir)), "modules")



def check_software(dietpi_id="", bin_path="", installapt=""):
    ''' check is software is installed, if not try to install it'''
    success = False
    success = is_installed(dietpi_id, bin_path)
    if not success and dietpi_id:
        success = dietpi_install(dietpi_id)
    elif not success and installapt:
        LOGGER.info("Installing %s with apt-get..." % installapt)
        os.system("apt-get install -y %s" % installapt)
        success = is_installed(dietpi_id, bin_path)
    return success


def dietpi_install(dietpi_id):
    ''' install dietpi package by its id '''
    if not IS_DIETPI:
        return False
    LOGGER.info("installing dietpi package %s" % dietpi_id)
    os.system("/DietPi/dietpi/dietpi-software install %s" % dietpi_id)
    return is_installed(dietpi_id)


def is_installed(dietpi_id="", bin_path=""):
    ''' check if specified package is installed '''
    is_installed = False
    if IS_DIETPI and dietpi_id:
        sw_status = run_proc('/DietPi/dietpi/dietpi-software list', True)
        if sw_status and isinstance(sw_status, (str, unicode)):
            for line in sw_status.split('\n'):
                if ("| =2 |" in line and dietpi_id in line):
                    is_installed = True
                    break
    elif bin_path:
        is_installed = os.path.exists(bin_path)
    return is_installed


def run_proc(cmd_str, check_result=False, ignore_error=False):
    ''' execute command with optional waiting for the results'''
    try:
        if check_result:
            # execute command and wait for result
            output = ""
            for cmd in cmd_str.split(' && '):
                res = subprocess.check_output(cmd, shell=True)
                if res and isinstance(res, (str, unicode)):
                    output += res
                else:
                    LOGGER.debug("Error while executing command %s --> %s" % (cmd, output))
            return output
        else:
            # execute command without waiting
            for cmd in cmd_str.split(' && '):
                subprocess.Popen(cmd, shell=True,
                         stdin=None, stdout=DEVNULL, stderr=subprocess.STDOUT, close_fds=True)
            return True
    except Exception as exc:
        if not ignore_error:
            LOGGER.error(str(exc))
        return False


def import_or_install(modulename, shortname=None, asfunction=False, installpip="", installapt=""):
    '''try to import module and if that fails, try to install it with pip'''
    # see: https://stackoverflow.com/questions/11990556/python-how-to-make-global-imports-from-a-function
    frm = inspect.stack()[1]
    calling_module = inspect.getmodule(frm[0])
    try:
        global_import(modulename, shortname, asfunction, calling_module)
    except ImportError, NameError:
        if not installapt and not installpip:
            installpip = modulename
        if installapt or installpip:
            # install with apt-get
            LOGGER.info("Installing %s with apt-get..." % installapt)
            res = run_proc("apt-get install -y build-essential python-dev python-cffi python-wheel python-setuptools %s" % installapt, True)
            LOGGER.debug(res)
        if installpip:
            # install with pip
            if not installpip:
                installpip = modulename
            if not isinstance(installpip, list):
                installpip = installpip.split(' ')
            for mod in installpip:
                LOGGER.info("Installing module %s with pip..." % mod)
                res = run_proc("pip install -q --upgrade %s" % mod, True)
                LOGGER.debug(res)
        global_import(modulename, shortname, asfunction, calling_module)


def global_import(modulename, shortname = None, asfunction = False, calling_module=None):
    # conditional importing workaround
    # see: https://stackoverflow.com/questions/11990556/python-how-to-make-global-imports-from-a-function
    # global_import("mymodule") --> import mymodule
    # global_import("mymodule, "test") ---> import mymodule as test
    # global_import("mymodyle", "Test", True) ---> from mymodule import Test
    if not calling_module:
        frm = inspect.stack()[1]
        calling_module = inspect.getmodule(frm[0])
    if shortname is None: 
        shortname = modulename
    if asfunction is False:
        setattr(calling_module, shortname, __import__(modulename, fromlist=['']))
    else:
        if isinstance(shortname, list):
            shortnames = shortname
        else:
            shortnames = [shortname]
        result = []
        for name in shortnames:
            setattr(calling_module, name, getattr(__import__(modulename, fromlist=['']), name))

import_or_install("requests")
import_or_install("simplejson", "json")
import_or_install("collections", ["OrderedDict", "defaultdict"], True)


def try_encode(text, encoding="utf-8"):
    try:
        return text.encode(encoding)
    except:
        return text

def try_decode(text, encoding="utf-8"):
    try:
        return text.decode(encoding)
    except:
        return text


def etree_to_dict(t):
    """
    Function to modify a xml.etree.ElementTree thingy to be a dict.
    Attributes will be accessible via ["@attribute"],
    and get the text (aka. content) inside via ["#text"]
    """
    # THANKS http://stackoverflow.com/a/10077069

    d = {t.tag: {} if t.attrib else None}
    children = list(t)
    if children:
        dd = defaultdict(list)
        for dc in map(etree_to_dict, children):
            for k, v in dc.items():
                dd[k].append(v)
        d = {t.tag: {k: v[0] if len(v) == 1 else v for k, v in dd.items()}}  # .items() is bad for python 2
    if t.attrib:
        d[t.tag].update(('@' + k, v) for k, v in t.attrib.items())  # .items() is bad for python 2
    if t.text:
        text = t.text.strip()
        if children or t.attrib:
            if text:
                d[t.tag]['#text'] = text
        else:
            d[t.tag] = text
    return d


class StatesList(list):
    state_listener = None
    parent = None
    def __setitem__(self, key, value):
        self.state_changed_event(key)
        super(StatesList, self).__setitem__(key, value)
    def append(self, *args, **kwargs):
        super(StatesList, self).append(*args, **kwargs)
        self.state_changed_event(*args, **kwargs)
    def remove(self, *args, **kwargs):
        super(StatesList, self).append(*args, **kwargs)
        self.state_changed_event(*args, **kwargs)
    def state_changed_event(self, key):
        subkey = key
        if self.parent:
            subkey = key
            key = self.parent
        if self.state_listener:
            self.state_listener((key, self, subkey))

class StatesDict(dict):
    state_listener = None
    parent = None

    def __setitem__(self, key, value):
        # optional processing here
        if isinstance(value, dict):
            value = StatesDict(value)
            value.parent = key
            value.state_listener = self.state_listener
        elif isinstance(value, list):
            value = StatesList(value)
            value.parent = key
            value.state_listener = self.state_listener
        if self.get(key) != value and key != "last_updated":
            super(StatesDict, self).__setitem__(key, value)
            super(StatesDict, self).__setitem__("last_updated", time.time())
            self.state_changed_event(key)

    def __delitem__(self, key):
        super(StatesDict, self).__delitem__(key)
        self.state_changed_event(key)

    def update(self, new_values):
        for key, value in new_values.items():
            self.__setitem__(key, value)

    def __init__(self, *args, **kwargs):
        val = super(StatesDict, self).__init__(*args, **kwargs)
        super(StatesDict, self).__setitem__("last_updated", time.time())
        return val


    def state_changed_event(self, key):
        subkey = key
        if self.parent:
            subkey = key
            key = self.parent
        if self.state_listener:
            self.state_listener((key, self.get(subkey), subkey))

    @property
    def json(self):
        return json.dumps(self, indent=4)

    @property
    def last_updated(self):
        return self["last_updated"]
    
class ConfigDict(OrderedDict):
    def __init__(self, *args, **kwargs):
        val = super(ConfigDict, self).__init__(*args, **kwargs)
        super(ConfigDict, self).__setitem__("last_updated", time.time())
        return val
    def __update__(self, *args, **kwargs):
        val = super(ConfigDict, self).__update__(*args, **kwargs)
        super(ConfigDict, self).__setitem__("last_updated", time.time())
        return val
    def __setitem__(self, key, value):
        # optional processing here
        if isinstance(value, (str, unicode)):
            value = value.replace(HOSTNAME, "%hostname%")
        super(ConfigDict, self).__setitem__(key, value)
        if self.get(key) != value:
            super(ConfigDict, self).__setitem__("last_updated", time.time())
    def __getitem__(self, key):
        # optional processing here
        value = super(ConfigDict, self).__getitem__(key)
        if isinstance(value, (str, unicode)):
            value = value.replace("%hostname%", HOSTNAME)
        return value
    def get(self, key, defaultvalue=None):
        if not key in self and defaultvalue != None:
            super(ConfigDict, self).__setitem__(key, defaultvalue)
        return self.__getitem__(key)

    @property
    def json(self):
        return json.dumps(self, indent=4)
    
class PlayerMetaData(StatesDict):
    def __init__(self, playername):
        super(PlayerMetaData, self).__init__()
        self["state"] = ""
        self["playername"] = playername
        self["artist"] = ""
        self["album"] = ""
        self["title"] = ""
        self["cover_url"] = ""
        self["covert_art"] = ""
        self["cover_file"] = ""
        self["volume_level"] = 0
        self["repeat"] = False
        self["shuffle"] = False

