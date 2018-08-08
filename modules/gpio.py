#!/usr/bin/env python

from time import sleep
import os
from resources.lib.utils import import_or_install, IS_DIETPI


def setup(monitor):
    '''setup this module'''
    if not monitor.config.get("ENABLE_MODULE_GPIO", False):
        LOGGER.debug("GPIO module is not enabled!")
        return False
    # Check we have the necessary module
    model_info = ""
    if IS_DIETPI:
        # dietpi detected
        with open("/DietPi/dietpi/.hw_model") as hw_file:
            model_info = hw_file.read()
    if "RPi" in model_info:
        import_or_install("RPi.GPIO", "gpio_mod", installpip="RPi.GPIO")
    else:
        # fall back to orangepi version of GPIO module which also supports other boards.
        import_or_install("OPi.GPIO", "gpio_mod", installpip="OPi.GPIO")

    # default config entries init
    monitor.config.get("GPIO_PINS_IN", [])
    monitor.config.get("GPIO_PINS_OUT", [])
    monitor.config.get("GPIO_AUDIO_RELAY_PIN", 0)
    monitor.config.get("GPIO_BUZZER_PIN", 0)
    monitor.config.get("GPIO_INVERTED_PINS", [])
    monitor.config.get("GPIO_NEGATIVE_PINS", [])
    monitor.config.get("GPIO_CUSTOM_LAYOUT", {})

    # hack for orangepi/nanopi boards
    try: 
        test_pud_up = gpio_mod.PUD_UP
    except AttributeError:
        gpio_mod.PUD_UP = 2
    
    return GPIO(monitor, gpio_mod)

class GPIO(object):
    
    @property
    def gpio_mod(self):
        return self._gpio

    def __init__(self, monitor, gpio):
        """
        Initialise the GPIO library
        """
        self.config = monitor.config
        self.command = monitor.command
        self.states = monitor.states
        self.monitor = monitor
        self._gpio = None
        self.states["gpio"] = {}
        # store GPIO object
        self._gpio = gpio
        self._gpio.setwarnings(False)
        LOGGER.debug("Monitoring gpio INPUT: %s -- OUTPUT: %s" % (self.config["GPIO_PINS_IN"], self.config["GPIO_PINS_OUT"]))
        LOGGER.debug("Initialised gpio using physical pin numbering")

    def command(self, cmd, opt_data=None):
        '''process command received from the command bus'''
        pin = int(cmd)
        if opt_data in ["on", "ON", "1", "true", "True"]:
            value = True
        elif opt_data in ["off", "OFF", "0", "false", "False"]:
            value = False
        else:
            value = bool(opt_data)
        self.set(pin, value)
           
    def start(self):
        if self.config["GPIO_CUSTOM_LAYOUT"]:
            custom_map = {}
            for key, value in self.config["GPIO_CUSTOM_LAYOUT"].items():
                custom_map[int(key)] = value
            self._gpio.setmode(custom_map)
        else:
            self._gpio.setmode(self._gpio.BOARD)

        for pin in self.config["GPIO_PINS_IN"]:
            LOGGER.debug("Initialising gpio input pin %s..." % (pin))
            try:
                self._gpio.setup(pin, self._gpio.IN, pull_up_down=self._gpio.PUD_UP)
            except:
                self._gpio.setup(pin, self._gpio.IN)
            self._gpio.add_event_detect(pin, self._gpio.FALLING, callback=self._gpio_event, bouncetime=200)

        if self.config["GPIO_AUDIO_RELAY_PIN"]:
            self.config["GPIO_PINS_OUT"].append(self.config["GPIO_AUDIO_RELAY_PIN"])
            self.monitor.register_state_callback(self.state_changed_event, "player")
        pins_out = self.config["GPIO_PINS_OUT"]
        if self.config["GPIO_BUZZER_PIN"]:
            pins_out.append(self.config["GPIO_BUZZER_PIN"])
        for pin in pins_out:
            LOGGER.debug("Initialising gpio output pin %s..." % (pin))
            if pin not in self.config["GPIO_NEGATIVE_PINS"]:
                self._gpio.setup(pin, self._gpio.OUT)
                if pin in self.config["GPIO_INVERTED_PINS"]:
                    self._gpio.output(pin, 1)

    def state_changed_event(self, key, value=None, subkey=None):
        if key == "player" and subkey == "power":
            self.set_audio_relay(self.states["player"]["power"])

    def stop(self):
        self._gpio.cleanup()
        if self.config["GPIO_AUDIO_RELAY_PIN"]:
            self.monitor.deregister_state_callback(self.state_changed_event, "player")

    def gpio_states(self):
        for pin in self.config["GPIO_PINS_IN"] + self.config["GPIO_PINS_OUT"]:
            oldstate = self.states["gpio"].get(pin, "UNKNOWN")
            newstate = "ON" if self.get(pin) else "OFF"
            if newstate != oldstate:
                self.states["gpio"][pin] = newstate
                self.publish_gpio(pin)

    def set_audio_relay(self, power):
        ''' toggle relay '''
        audio_relay = self.config["GPIO_AUDIO_RELAY_PIN"]
        if not audio_relay:
            return
        relay_powered = self.get(audio_relay)
        if relay_powered and not power:
            LOGGER.debug("turn off audio relay.")
            self.set(audio_relay, 0)
        elif power and not relay_powered:
            LOGGER.debug("turn on audio relay.")
            self.set(audio_relay, 1)

    def get(self, pin):
        ''' get state of self._gpio pin'''
        if not self._gpio:
            return 0
        if pin in self.config["GPIO_INVERTED_PINS"]:
            return 0 if self._gpio.input(pin) else 1
        else:
            try:
                return self._gpio.input(pin)
            except RuntimeError:
                # will happen for negative pin
                return 0

    def set(self, pin, new_state):
        '''sets new state for gpgio state'''
        self.states["gpio"][pin] = new_state
        if not pin in self.config["GPIO_PINS_OUT"]:
            LOGGER.warning("pin %s is not monitored!" % pin)
            return
        if pin in self.config["GPIO_INVERTED_PINS"] and new_state:
            self._gpio.output(pin, self._gpio.LOW)
        elif pin in self.config["GPIO_INVERTED_PINS"] and not new_state:
            self._gpio.output(pin, self._gpio.HIGH)
        elif pin in self.config["GPIO_NEGATIVE_PINS"] and new_state:
            self._gpio.setup(pin, self._gpio.OUT)
            self._gpio.output(pin, 1)
        elif pin in self.config["GPIO_NEGATIVE_PINS"] and not new_state:
            self._gpio.cleanup(pin)
        else:
            self._gpio.output(pin, new_state)

    def _gpio_event(self, pin):
        ''' publish state of gpio pin'''
        newstate = "ON" if self.get(pin) else "OFF"
        LOGGER.info("Pin %s changed to %s" % (pin, newstate))
        self.states["gpio"][pin] = newstate




