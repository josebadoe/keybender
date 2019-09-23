import itertools, functools
from collections import namedtuple
from Xlib import X, error
import time
from keybender.knox import Modifiers
from keybender.event import Event

class Listener:
    MapEntry = namedtuple("MapEntry", "keysym mask_variety filter_mods filter_mod_bits mask mask_bits modifiers modifier_bits trigger")

    def __new__(cls, *args, level=1, **kwargs):
        if cls == Listener:
            if level <= 1:
                return XKeyListener(*args, level=level, **kwargs)
            elif level >= 2:
                return XListener(*args, level=level, **kwargs)
        return object.__new__(cls)

    def __init__(self, knox, event_loop, triggers, level=1):
        # event.state & w.mask.modifiers.bitmap == w.trigger.modifiers.bitmap
        # keysym = self.know.display.keycode_to_keysym(event.detail, 0)
        # event.keycode = w.trigger.
        self.knox = knox
        self.event_loop = event_loop
        self.event_map = dict()
        self.level = level
        self.chained_listeners = dict()
        self.x_state = None
        for t in triggers:
            if t.waiter:
                self.chained_listeners[t] = Listener(
                    self.knox, event_loop, t.waiter.triggers, level=level+1)

            # keysym to keycode
            # all bitcombos outside of the mask
            for (i, pm) in enumerate((~t.mask.modifiers).possible_values()):
                # print("    %5d: %s :: %s" % (i, "{0:08b}".format(pm.bitmap), pm))
                # print("           Grabkey + %s -> %s with %r" % (
                #     w.trigger.modifiers, "{0:08b}".format((pm | w.trigger.modifiers).bitmap),
                #     w.trigger.keysym))
                e = self.MapEntry(t.key.keysym,
                                  pm,
                                  (pm | t.key.modifiers), (pm | t.key.modifiers).bitmap,
                                  t.mask.modifiers, t.mask.modifiers.bitmap,
                                  t.key.modifiers, t.key.modifiers.bitmap,
                                  t)
                a = Modifiers(self.knox)
                if e.keysym not in self.event_map:
                    self.event_map[e.keysym] = list()
                self.event_map[t.key.keysym].append(e)

    def next_event(self, e, event_loop):
        # e.fd is knox
        while True:
            x = e.fd.next_event(wait=False)
            if x is None:
                break
            else:
                yield x

    def triggered(self, map_entry):
        if map_entry is None:
            return None
        #print("Triggered: %s" % map_entry.waiter.name)
        if self.x_state:
            self.knox.restore_state(self.x_state)
        #print("Executing triggered: %s" % map_entry.waiter.name)
        map_entry.trigger.execute(x_state=self.x_state)
        if map_entry.trigger in self.chained_listeners:
            self.chained_listeners[map_entry.trigger].listen()

    def find_entry(self, keysym=None, keycode=None, state=None):
        if keysym is not None:
            keysyms = [ keysym ]
        elif keycode is not None:
            keysyms = self.knox.keycode_to_keysym(keycode)

        for keysym in keysyms:
            print("KEY %s, state %s"
                  % (self.knox.keysym_to_string(keysym),
                     Modifiers(self.knox, None) | state))
            if keysym not in self.event_map:
                # different keysyms for the same keycode, for example
                # upper and a lowercase letters...
                continue
            for em in self.event_map[keysym]:
                if state & em.mask_bits == em.modifier_bits:
                    return em
        return None


class XKeyListener(Listener):

    def listen(self):
        self.grab_key_errors = None
        with self.knox.silenced_error(error.BadAccess):
            for e in itertools.chain(*self.event_map.values()):
                #print("Grab: %s (%s)" % (e.waiter.trigger, bin(e.filter_mod_bits)))
                self.knox.root.grab_key(
                    self.knox.keysym_to_keycode(e.keysym), e.filter_mod_bits,
                    True, X.GrabModeAsync, X.GrabModeAsync,
                    onerror=self.knox.error_handler(self.grab_key_error, e))
            self.knox.sync()

        triggered = None
        handler_key = self.event_loop.register(
            Event.READABLE, self.next_event, fd=self.knox)
        print("Starting level %d listener" % self.level)
        for event in self.event_loop.process():
            if event.type == X.KeyPress:
                triggered = self.find_entry(keycode=event.detail, state=event.state)
                if triggered:
                    self.event_loop.quit()
            else: # X.KeyRelease or whatever else (mouse button)
                pass
        #print("Unregistering %r" % handler_key)
        self.event_loop.unregister(handler_key)

        for e in itertools.chain(*self.event_map.values()):
            self.knox.root.ungrab_key(
                self.knox.keysym_to_keycode(e.keysym),
                e.filter_mod_bits)
        self.knox.display.flush()

        return self.triggered(triggered)


    def grab_key_error(self, map_entry, *args, **kwargs):
        if self.grab_key_errors is None:
            self.grab_key_errors = dict()
        key = map_entry.trigger.key
        mask = map_entry.trigger.mask
        #mods = knox.Modifiers(self.knox) & map_entry.modifiers
        print("Cannot bind %s and mask variety %s"
              % (map_entry.trigger.key, map_entry.mask_variety))


class XListener(Listener):

    def listen(self):
        self.x_state = self.knox.save_state()
        root = self.knox.root
        screen = self.knox.screen

        window = root.create_window(
            10, 10, 400, 400, 1,
            screen.root_depth,
            background_pixel=screen.black_pixel,
            event_mask=X.ExposureMask | X.KeyPressMask | X.KeyReleaseMask)
        self.knox.set_wm_states(window, [ "above", "skip_pager", "skip_taskbar" ], True)
        self.knox.set_desktop_for_window(window, self.knox.current_desktop())
        gc = window.create_gc(
            foreground=screen.white_pixel,
            background=screen.black_pixel)
        self.x_state["Ignore"] = set([ window.id ])

        self.knox.set_opacity(window, 0.5)
        self.knox.toggle_frame(window, frame=False, wait=False)
        window.map()
        self.knox.active_window(window)

        print("EXPOSE")
        window.fill_rectangle(gc, 40, 40, 300, 300)
        window.draw_text(gc, 10, 10, "Macilaci picipuci")

        handler_key = self.event_loop.register(
            Event.READABLE, self.next_event, fd=self.knox)

        print("Starting level %d listener" % self.level)
        triggered = None
        keydown = 0
        window.grab_keyboard(X.KeyPressMask | X.KeyReleaseMask,
                             X.GrabModeAsync, X.GrabModeAsync,
                             X.CurrentTime)
        # for e in self.event_map:
        #     print("MAP: %s" % (e,))
        for event in self.event_loop.process():
            print("X Says", event)
            if event.type == X.Expose:
                print("EXPOSE")
            elif event.type == X.KeyPress:
                print("KEYPRESS @%d" % keydown)
                keydown += 1
                triggered = self.find_entry(keycode=event.detail, state=event.state)
                if triggered:
                    self.event_loop.quit()
            elif event.type == X.KeyRelease:
                print("KEYPRELEASE @%d" % keydown)
                if keydown:
                    keydown -= 1
                    if keydown == 0:
                        self.event_loop.quit()
        self.knox.display.ungrab_keyboard(X.CurrentTime)
        window.destroy()
        return self.triggered(triggered)
