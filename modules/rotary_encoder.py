#!/usr/bin/env python

import time
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
    if monitor.config.get("GPIO_ROTARY_ENCODER_USE_KY040", False):
        return KY040RotaryEncoder(monitor, gpio, pin_a, pin_b, pin_button)
    else:
        return RotaryEncoder(monitor, gpio, pin_a, pin_b, pin_button)



class RotaryEncoder(threading.Thread):
    """
    A class to decode mechanical rotary encoder pulses.
    """
    _exit = threading.Event()
    
    def __init__(self, monitor, gpio, pin_a, pin_b, pin_button):
        self.gpio = gpio.gpio_mod
        self.monitor = monitor
        self.last_pin = None
        self.pin_a = pin_a
        self.pin_b = pin_b
        self.pin_button = pin_button
        self.lev_a = 0
        self.lev_b = 0
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
        self.gpio.setup(self.pin_a, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)
        self.gpio.setup(self.pin_b, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)
        self.gpio.add_event_detect(self.pin_a, self.gpio.BOTH, self._rotary_callback)
        self.gpio.add_event_detect(self.pin_b, self.gpio.BOTH, self._rotary_callback)
        if self.pin_button:
            self.gpio.setup(self.pin_button, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)
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
            cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_CLOCKWISE", "volume_up")
            self.monitor.command("player", cmd)
        elif event == 2:
            LOGGER.debug("rotary encoder is turned counter-clockwise")
            # rotary turned counter clockwise
            cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_COUNTER_CLOCKWISE", "volume_down")
            self.monitor.command("player", cmd)
        elif event == 3:
            # fired when button is pressed shortly
            LOGGER.debug("rotary encoder button is pushed")
            cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_PRESS", "play")
            if self.monitor.is_playing:
                cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_PRESS_PLAYING", "next")
            self.monitor.command("player", cmd)
        elif event == 4:
            LOGGER.debug("rotary encoder button is pressed for more than 1 second")
            # fired when button is held for 1 second
            cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_HOLD", "stop")
            self.monitor.command("player", cmd)

        
    def _btn_callback(self, channel):
        ''' callback when button pressed 3=single press, 4= hold '''
        retries = 15
        while retries:
            event = 4
            data = self.gpio.input(channel)
            if data == 1 and retries < 3:
                return # debounce
            elif data == 1 and retries > 3: 
                event = 3
                break
            time.sleep(0.1)
            retries = retries - 1
        self.rotary_event(event)

        
    def _rotary_callback(self, channel):
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


class KY040RotaryEncoder(threading.Thread):
    """
    A class to decode mechanical rotary encoder pulses from a KY040 rotary encoder
    """
    _exit = threading.Event()

    def __init__(self, monitor, gpio, clock_pin, data_pin, switch_pin):
        """
        Instantiate the class. Takes three arguments: the two pin numbers to
        which the rotary encoder is connected, plus a callback to run when the
        switch is turned.
        """
        threading.Thread.__init__(self)
        self.clock_pin = clock_pin
        self.data_pin = data_pin
        self.switch_pin = switch_pin
        self.monitor = monitor
        self.gpio = gpio.gpio_mod
        self.callback_busy = False

    def run(self):
        self.gpio.setup(self.clock_pin, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)
        self.gpio.setup(self.data_pin, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)
        self.gpio.setup(self.switch_pin, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)
        self.gpio.add_event_detect(self.clock_pin, self.gpio.FALLING, self._rotary_callback, bouncetime=250)
        self.gpio.add_event_detect(self.switch_pin, self.gpio.FALLING, self._btn_callback, bouncetime=300)
        # keep thread alive
        while not self._exit.isSet():
            self._exit.wait(1200)

    def stop(self):
        self._exit.set()
        try:
            self.gpio.remove_event_detect(self.clock_pin)
            self.gpio.remove_event_detect(self.switch_pin)
        except RuntimeError:
            # objects are probably already cleaned up
            pass


    def rotary_event(self, event):
        ''' rotary encoder event callback puts events in queue'''
        if event == 1:
            LOGGER.debug("rotary encoder is turned clockwise")
            # rotary turned clockwise
            cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_CLOCKWISE", "volume_up")
            self.monitor.command("player", cmd)
        elif event == 2:
            LOGGER.debug("rotary encoder is turned counter-clockwise")
            # rotary turned counter clockwise
            cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_COUNTER_CLOCKWISE", "volume_down")
            self.monitor.command("player", cmd)
        elif event == 3:
            # fired when button is pressed shortly
            LOGGER.debug("rotary encoder button is pushed")
            cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_PRESS", "play")
            if self.monitor.is_playing:
                cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_PRESS_PLAYING", "next")
            self.monitor.command("player", cmd)
        elif event == 4:
            LOGGER.debug("rotary encoder button is pressed for more than 1 second")
            # fired when button is held for 1 second
            cmd = self.monitor.config.get("GPIO_ROTARY_ENCODER_CMD_HOLD", "stop")
            self.monitor.command("player", cmd)


    def _btn_callback(self, channel):
        ''' callback when button pressed 3=single press, 4= hold '''
        if self.gpio.input(self.clock_pin) == 0 or self.gpio.input(self.data_pin) == 0:
            return # the btn pin is interfering with the rotary pins
        retries = 15
        while retries:
            event = 4
            data = self.gpio.input(channel)
            if data == 1 and retries < 3:
                return # debounce
            elif data == 1 and retries > 3: 
                event = 3
                break
            time.sleep(0.1)
            retries = retries - 1
        self.rotary_event(event)


    def _rotary_callback(self, channel):
        ''' gpio event from rotary pin '''
        if self.gpio.input(channel) == 0:
            data = self.gpio.input(self.data_pin)
            if data == 1:
                self.rotary_event(2)
            else:
                self.rotary_event(1)
