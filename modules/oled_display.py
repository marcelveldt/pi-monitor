#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2014-17 Richard Hull and contributors
# See LICENSE.rst for details.
# PYTHON_ARGCOMPLETE_OK

"""
Scrolling artist + song and play/pause indicator for small oled displays
"""

import os
import time
import threading
import time
from resources.lib.utils import import_or_install

def setup(monitor):
    '''setup the module'''
    if not monitor.config.get("ENABLE_MODULE_OLED_DISPLAY", False):
        LOGGER.debug("OLED Display module is not enabled!")
        return False
    # TODO: add config entries for type of display and hardware address
    # currently display is hardcoded
    import_or_install("luma.core.render", "canvas", True, "luma.core")
    import_or_install("luma.core.image_composition", ["ImageComposition", "ComposableImage"], True, "luma.core")
    import_or_install("luma.core.interface.serial", "i2c", True, "luma.core")
    import_or_install("luma.oled.device", "ssd1306", True, "luma.oled")
    return OLEDDisplay(monitor)


class OLEDDisplay(threading.Thread):
    _exit = threading.Event()

    def __init__(self, monitor):
        self.monitor = monitor
        threading.Thread.__init__(self)

    def stop(self):
        self._exit.set()
        threading.Thread.join(self, 2)

    def run(self):

        device = None

        #font = make_font("code2000.ttf", 16)
        font = make_font("pixelmix.ttf", 18)
        last_song = ""

        try:
            while not self._exit.isSet():

                # TODO !!!!
                media_details = self.monitor.player_info
                cur_song = media_details["title"] + media_details["artist"]

                if not cur_song and last_song:
                    last_song = ""
                    device.cleanup()
                    device = None
                elif not cur_song:
                    self._exit.wait(2)
                else:
                    if not device:
                        serial = i2c(port=1, address=0x3C)
                        device = ssd1306(serial, rotate=0)
                        image_composition = ImageComposition(device)
                    synchroniser = Synchroniser()
                    ci_song = ComposableImage(TextImage(device, media_details.title, font).image, position=(1, 2))
                    ci_artist = ComposableImage(TextImage(device, media_details.artist, font).image, position=(1, 30))
                    song = Scroller(image_composition, ci_song, 100, synchroniser)
                    artist = Scroller(image_composition, ci_artist, 100, synchroniser)
                    last_song = cur_song
                    cycles = 0

                    while cycles < 3 and not self.exit and last_song == cur_song:
                        artist.tick()
                        song.tick()
                        time.sleep(0.025)
                        cycles = song.get_cycles()
                        media_details = self.metadata_func()
                        cur_song = media_details.title + media_details.artist
                        with canvas(device, background=image_composition()) as draw:
                            image_composition.refresh()
                            draw.rectangle(device.bounding_box, outline="black")
                    del artist
                    del song
            
        except KeyboardInterrupt:
            pass


class TextImage():
    def __init__(self, device, text, font):
        with canvas(device) as draw:
            w, h = draw.textsize(text, font)
        self.image = Image.new(device.mode, (w, h))
        draw = ImageDraw.Draw(self.image)
        draw.text((0, 0), text, font=font, fill="white")
        del draw
        self.width = w
        self.height = h


class Synchroniser():
    def __init__(self):
        self.synchronised = {}

    def busy(self, task):
        self.synchronised[id(task)] = False

    def ready(self, task):
        self.synchronised[id(task)] = True

    def is_synchronised(self):
        for task in self.synchronised.iteritems():
            if task[1] is False:
                return False
        return True


class Scroller():
    WAIT_SCROLL = 1
    SCROLLING = 2
    WAIT_REWIND = 3
    WAIT_SYNC = 4

    def __init__(self, image_composition, rendered_image, scroll_delay, synchroniser):
        self.image_composition = image_composition
        self.speed = 1
        self.image_x_pos = 0
        self.rendered_image = rendered_image
        self.image_composition.add_image(rendered_image)
        self.max_pos = rendered_image.width - image_composition().width
        self.delay = scroll_delay
        self.ticks = 0
        self.state = self.WAIT_SCROLL
        self.synchroniser = synchroniser
        self.render()
        self.synchroniser.busy(self)
        self.cycles = 0
        self.must_scroll = self.max_pos > 0

    def __del__(self):
        self.image_composition.remove_image(self.rendered_image)

    def tick(self):

        # Repeats the following sequence:
        #  wait - scroll - wait - rewind -> sync with other scrollers -> wait
        if self.state == self.WAIT_SCROLL:
            if not self.is_waiting():
                self.cycles += 1
                self.state = self.SCROLLING
                self.synchroniser.busy(self)

        elif self.state == self.WAIT_REWIND:
            if not self.is_waiting():
                self.synchroniser.ready(self)
                self.state = self.WAIT_SYNC

        elif self.state == self.WAIT_SYNC:
            if self.synchroniser.is_synchronised():
                if self.must_scroll:
                    self.image_x_pos = 0
                    self.render()
                self.state = self.WAIT_SCROLL

        elif self.state == self.SCROLLING:
            if self.image_x_pos < self.max_pos:
                if self.must_scroll:
                    self.render()
                    self.image_x_pos += self.speed
            else:
                self.state = self.WAIT_REWIND

    def render(self):
        self.rendered_image.offset = (self.image_x_pos, 0)

    def is_waiting(self):
        self.ticks += 1
        if self.ticks > self.delay:
            self.ticks = 0
            return False
        return True

    def get_cycles(self):
        return self.cycles


def make_font(name, size):
    font_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), 'fonts', name))
    return ImageFont.truetype(font_path, size)
