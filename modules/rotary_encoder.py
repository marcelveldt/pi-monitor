#!/usr/bin/env python

from time import sleep
import threading


def setup(monitor):
    '''setup the module'''
    if not monitor.config.get("ENABLE_MODULE_ROTARY_ENCODER", False):
        LOGGER.debug("Rotary Encoder module is not enabled!")
        return False
    pin_a = monitor.config.get("GPIO_ROTARY_ENCODER_UP_PIN",0)
    pin_b = monitor.config.get("GPIO_ROTARY_ENCODER_DOWN_PIN",0)
    pin_button = monitor.config.get("GPIO_ROTARY_ENCODER_SWITCH_PIN",0)
    if not pin_a or not pin_b:
        LOGGER.debug("Rotary encoder module is not setup!")
        return False
    gpio = monitor.get_module("gpio")
    if not gpio:
        LOGGER.warning("GPIO module is not loaded!. Aborting.")
        return False
    return RotaryEncoder(monitor, gpio)



class RotaryEncoder(threading.Thread):
    """
    A class to decode mechanical rotary encoder pulses.
    """
    _exit = threading.Event()
    
    def __init__(self, monitor, gpio):
        self.gpio = gpio.gpio_mod
        self.monitor = monitor
        self.last_pin = None
        self.pin_a = self.monitor.config["GPIO_ROTARY_ENCODER_UP_PIN"]
        self.pin_b = self.monitor.config["GPIO_ROTARY_ENCODER_DOWN_PIN"]
        self.pin_button = self.monitor.config["GPIO_ROTARY_ENCODER_SWITCH_PIN"]
        self.lev_a = 0
        self.lev_b = 0
        self.callback_busy = False
        threading.Thread.__init__(self)

        
    def stop(self):
        self._exit.set()
        try:
            self.gpio.remove_event_detect(self.pin_a)
            self.gpio.remove_event_detect(self.pin_b)
            self.gpio.remove_event_detect(self.pin_button)
        except RuntimeError:
            # objects are probably already cleaned up
            pass


    def run(self):
        self.gpio.setup(self.pin_a, self.gpio.IN)
        self.gpio.setup(self.pin_b, self.gpio.IN)
        self.gpio.add_event_detect(self.pin_a, self.gpio.BOTH, self._callback)
        self.gpio.add_event_detect(self.pin_b, self.gpio.BOTH, self._callback)
        if self.pin_button:
            self.gpio.setup(self.pin_button, self.gpio.IN)
            self.gpio.add_event_detect(self.pin_button, self.gpio.FALLING, self._btn_callback, bouncetime=500)
        LOGGER.debug("RotaryEncoder is now listening for events")
        # mainloop: just keep the thread alive
        while not self._exit.isSet():
            self._exit.wait(1200)


    def rotary_event(self, event):
        ''' rotary encoder event callback puts events in queue'''
        if event == 1:
            LOGGER.debug("rotary encoder is turned clockwise")
            # rotary turned clockwise
            # cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_CLOCKWISE", "volume_up")
            # self.monitor.command("player", cmd)
            self.monitor.command("player", "volume_up")
        elif event == 2:
            LOGGER.debug("rotary encoder is turned counter-clockwise")
            # rotary turned counter clockwise
            # cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_COUNTER_CLOCKWISE", "volume_down")
            # self.monitor.command("player", cmd)
            self.monitor.command("player", "volume_down")
        elif event == 3:
            # fired when button is pressed shortly
            LOGGER.debug("rotary encoder button is pushed")
            # cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_PRESS", "play")
            # if self.monitor.is_playing:
            #     cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_PRESS_PLAYING", "next")
            # self.monitor.command("player", cmd)
            if self.monitor.is_playing:
                self.monitor.command("player", "next")
            else:
                self.monitor.command("player", "play")
        elif event == 4:
            LOGGER.debug("rotary encoder button is pressed for more than 1 second")
            # fired when button is held for 1 second
            #cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_HOLD", "stop")
            self.monitor.command("player", "stop")

        
    def _btn_callback(self, channel):
        ''' callback when button pressed 3=single press, 4= hold '''
        if self.callback_busy:
            return
        retries = 15
        while retries:
                event = 4
                if self.gpio.input(channel): 
                        event = 3
                        break
                sleep(0.1)
                retries = retries - 1
        self.rotary_event(event)
        if event == 4:
            self.callback_busy = True
            sleep(4)
            self.callback_busy = False
        return

        
    def _callback(self, channel):
        level = self.gpio.input(channel)
        if channel == self.pin_a:
            self.lev_a = level
        else:
            self.lev_b = level
        if channel == self.last_pin:
            return # Debounce.
        self.last_pin = channel
        if channel == self.pin_a and level == 1:
            if self.lev_b == 1:
                self.rotary_event(1)
        elif channel == self.pin_b and level == 1:
            if self.lev_a == 1:
                self.rotary_event(2)
