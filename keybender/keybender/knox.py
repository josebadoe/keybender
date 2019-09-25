from Xlib.display import Display
from Xlib import protocol, error
from Xlib import X, XK, Xatom, Xutil
from array import array
import itertools
import functools
import numbers
import time, datetime
import select
import os
from collections import namedtuple
from contextlib import contextmanager

# root properties: https://specifications.freedesktop.org/wm-spec/1.3/ar01s03.html


class Keysyms:

    def __init__(self):
        self.names = dict()
        self.keysyms = dict()
        from Xlib.keysymdef import miscellany
        self.load_dict(miscellany.__dict__)
        from Xlib.keysymdef import latin1
        self.load_dict(latin1.__dict__)
        from Xlib.keysymdef import xkb
        self.load_dict(xkb.__dict__)
        from Xlib.keysymdef import xf86
        # this is missing from there, for some reason...
        xf86.XK_XF86_AudioMicMute = 0x1008ffb2
        self.load_dict(xf86.__dict__)

    def load_dict(self, d):
        for (name, keysym) in d.items():
            if not (isinstance(name, str)
                    and name.startswith("XK_")
                    and isinstance(keysym, int)):
                continue
            self.names[name] = keysym
            self.keysyms[keysym] = name


    def __getitem__(self, name):
        if isinstance(name, str):
            return self.names.get(name)
        else:
            return self.keysyms.get(name)

    def friendly_name(self, keysym, simplest=True):
        s = self[keysym]
        if s.startswith("XK_"):
            s = s[3:]
        if simplest and (s.endswith("_L") or s.endswith("_R")):
            s = s[:-2]
        return s


class Modifier:
    def __init__(self, knox, keysym, bit):
        self.keysym = keysym
        self.bit = bit
        self.full_name = knox.keysym_to_string(keysym, friendly=True) or ("XK_%d" % keysym)
        self.common_name = knox.keysym_to_string(keysym, very_friendly=True) or self.full_name
        self.mask = 1 << bit
    def __eq__(a, b):
        return a.bit == b.bit and a.full_name == b.full_name
    def __hash__(self):
        return hash((self.keysym, self.bit, self.full_name))
    def __str__(self):
        return self.common_name
    def __repr__(self):
        return self.common_name


class Modifiers:
    def __init__(self, knox, *mods):
        self.knox = knox
        self.modifiers = dict()
        self.ordered_mods = list()
        if mods:
            for m in mods:
                if isinstance(m, Modifier):
                    self.add(m)
                elif m is not None:
                    # then must be iterable
                    for mm in m:
                        self.add(mm)
            return
        processed_keysyms = set()
        for (bit, keys) in enumerate(knox.display.get_modifier_mapping()):
            for keycode in keys:
                if keycode == 0:
                    continue
                try:
                    for keysym in knox.keycode_to_keysym(keycode):
                        if not keysym or keysym in processed_keysyms:
                            continue
                        processed_keysyms.add(keysym)
                        self.add(Modifier(knox, keysym, bit))
                except KeyError:
                    pass

    def all(self, named_only=False):
        l = self.modifiers.get("bit", None)
        if l is None:
            return []
        return [
            m for m in itertools.chain.from_iterable(
                    [ self.modifiers["bit"][b] for b in sorted(l.keys()) ])
            if not named_only or m.full_name is not None ]

    def __contains__(self, m):
        if not self.ordered_mods:
            return False
        mods = self.find(bit=m.bit)
        if not mods:
            return False
        for m2 in mods:
            if m == m2:
                return True
        return False

    def add(self, m):
        if m in self.ordered_mods:
            return
        self.ordered_mods.append(m)
        for a in [ "bit", "full_name", "common_name", "keysym" ]:
            if a not in self.modifiers:
                self.modifiers[a] = dict()
            v = getattr(m, a)
            if v not in self.modifiers[a]:
                self.modifiers[a][v] = set()
            self.modifiers[a][v].add(m)

    def possible_values(self):
        values = [ Modifiers(self.knox, []) ]
        for m in self.simplified.all():
            for i in range(len(values)):
                values.append(values[i] | m)
        return values

    def all(self):
        return self.ordered_mods

    def find(self, keysym=None, name=None, bit=None):
        if not self.ordered_mods:
            return None
        if keysym is not None and keysym in self.modifiers["keysym"]:
            return self.modifiers["keysym"][keysym]
        if name is not None and name in self.modifiers["full_name"]:
            return self.modifiers["full_name"][name]
        if name is not None and name in self.modifiers["common_name"]:
            return self.modifiers["common_name"][name]
        if bit is not None and bit in self.modifiers["bit"]:
            return self.modifiers["bit"][bit]
        return None

    def remove (self, **kwargs):
        ms = self.find(**kwargs)
        #print("REMOVING %r, %s" % (kwargs, ms))
        for m in list(ms):
            for a in [ "bit", "full_name", "common_name", "keysym" ]:
                v = getattr(m, a, None)
                if v is not None:
                    self.modifiers[a][v].remove(m)
                #print("Remained in %s:%r: %s" % (a, v, self.modifiers[a][v]))
            self.ordered_mods.remove(m)

    @property
    def bitmap(self):
        bitmap = 0
        for m in self.all():
            bitmap |= 1 << m.bit
        return bitmap

    def __str__(self):
        return "+".join(map(str, self.all()))


    def __invert__(self):
        mods = Modifiers(self.knox, None)
        for m in self.knox.modifiers.all():
            if self.find(bit=m.bit) is None:
                mods.add(m)
        return mods

    possible_aliases = { "Pointer_EnableKeys": "Num_Lock" }
    @property
    def simplified(self):
        mods = Modifiers(self.knox, None)
        for m in self.all():
            mo = mods.find(bit=m.bit)
            if not mo:
                mods.add(m)
            else:
                for m2 in mo:
                    if (m2.common_name in self.possible_aliases
                        and self.possible_aliases[m2.common_name] == m.common_name):
                        mods.remove(bit=m.bit)
                        mods.add(m)
                        break
        return mods


    def __nonzero__(self):
        if self.ordered_mods:
            return True
        else:
            return False

    def __and__(self, mod):
        if isinstance(mod, Modifiers):
            mr = Modifiers(self.knox, [])
            for m in mr.all():
                if m in self:
                    mr.add(m)
            return mr
        elif isinstance(mod, int):
            mr = Modifiers(self.knox, None)
            for m in self.all():
                if mod & (1 << m.bit):
                    mr.add(m)
            return mr


    def __or__(self, mod):
        if isinstance(mod, Modifier):
            if self.find(bit=mod.bit):
                return self
            mr = Modifiers(self.knox, *self.all(), mod)
            return mr
        elif isinstance(mod, Modifiers):
            mr = Modifiers(self.knox, None)
            for m in mod.all():
                mr.add(m)
            for m in self.all():
                mr.add(m)
            return mr
        elif isinstance(mod, int):
            m_full = Modifiers(self.knox)
            mr = Modifiers(self.knox, None)
            for m in m_full.all():
                if mod & (1 << m.bit) or self.find(bit=m.bit):
                    mr.add(m)
            return mr
        assert False, "What %r" % type(mod)


class Waiter:
    def __init__(self, wait=None, step=0.1):
        self.started = None
        if wait is False or wait is None:
            self.timeout = 0
        elif wait is True:
            self.timeout = True
        else:
            self.timeout = wait
        self.step = step

    @property
    def remaining(self):
        if self.started is not None:
            d = datetime.datetime.now() - self.started
            if isinstance(self.timeout, numbers.Number):
                return max(self.timeout - d.total_seconds(), 0)
            else:
                return 9999
        elif self.timeout:
            if isinstance(self.timeout, numbers.Number):
                return self.timeout
            else:
                return 9999
        return 0

    @property
    def progressed(self):
        if self.started:
            d = datetime.datetime.now() - self.started
            if d.total_seconds() > 0.001:
                return True
        return False

    def wait(self):
        "Return false when timout is reached"
        if isinstance(self.timeout, numbers.Number):
            t = datetime.datetime.now()
            if self.started is None:
                self.started = t
            else:
                time.sleep(self.step)
                d = t - self.started
                if d.total_seconds() > self.timeout:
                    return False
        return True

import traceback

class KnoX:
    Geometry = namedtuple("Geometry", "x y width height")
    FrameExtents = namedtuple("FrameExtents", "left right top bottom")

    def __init__(self):
        #self.display = Display(os.environ.get("DISPLAY", ":0.0"))
        self.display = Display()
        print("Connected to X DISPLAY %r" % self.display.get_display_name())
        self.display.set_error_handler(self.knox_error_handler)
        self.screen = self.display.screen()
        self.root = self.screen.root
        self.atoms = dict()
        self.atom_names = dict()
        self.keysyms = Keysyms()
        self.modifiers = Modifiers(self)
        self._supported_properties = None
        self._acceptable_error_sequence = 0
        self._acceptable_errors = dict()
        self._silenced_errors = set()

    def fileno(self):
        """This function is here to make select work with this object"""
        return self.display.fileno()

    @contextmanager
    def silenced_error(self, error):
        silencer = self.silence_error(error)
        try:
            yield silencer
        finally:
            self.remove_silencer(silencer)

    def silence_error(self, error):
        k = self._acceptable_error_sequence
        self._acceptable_errors[k] = error
        self._acceptable_error_sequence += 1
        self._silenced_errors = set(self._acceptable_errors.values())
        return k

    def remove_silencer(self, key):
        if key in self._acceptable_errors:
            del self._acceptable_errors[key]
            self._silenced_errors = set(self._acceptable_errors.values())

    def knox_error_handler(self, err, *args):
        if type(err) not in self._silenced_errors:
            print("X protocol error: %s" % err)
            traceback.print_stack()
    # def wait_for_event(self, timeout_seconds):
    #     """ Wait up to `timeout_seconds` seconds for an event to be queued.
    #     Return True, if a xevent is available.
    #     Return False, if the timeout was reached.
    #     from https://gist.github.com/fphammerle/d81ca3ff0a169f062a9f28e57b18f04d"""
    #     rlist = select.select(
    #         [self.display], # rlist
    #         [], # wlist
    #         [], # xlist
    #         timeout_seconds, # timeout [seconds]
    #     )[0]
    #     return len(rlist) > 0

    def next_event(self, wait=True):
        if (wait or self.display.pending_events()):
            return self.display.next_event()
        else:
            return None

    # def next_event(self, event_loop):
    #     event_loop.register_reader(self.display,



    def atom(self, name, only_if_exists=False):
        if isinstance(name, int):
            a = name
        elif name not in self.atoms:
            a = self.display.get_atom(name, only_if_exists=only_if_exists)
            self.atoms[name] = a
        else:
            a = self.atoms[name]
        return a

    def atom_name(self, atom):
        if atom in self.atom_names:
            return self.atom_names[atom]
        name = self.display.get_atom_name(atom)
        if name:
            self.atom_names[atom] = name
            if name not in self.atoms:
                self.atoms[name] = atom
            return name


    def get_prop(self, window, name):
        prop_name = self.atom(name, only_if_exists=True)
        if not prop_name:
            return None
        if isinstance(window, int):
            window = self.get_window(window)
        p = window.get_full_property(prop_name, X.AnyPropertyType)
        if p:
            return p.value

    def get_text_prop(self, window, name):
        prop_name = self.atom(name, only_if_exists=True)
        if not prop_name:
            return None
        s = window.get_full_text_property(prop_name, Xatom.STRING)
        if not s:
            t = self.atom("UTF8_STRING", only_if_exists=True)
            if t:
                s = window.get_full_text_property(prop_name, t)
        return s


    def onerror(self, *args, **kwargs):
        print("ERROR: something bad happened about %r and %r" % (args, kwargs))
        raise Exception("Error is bad...")

    def set_prop(self, window, name, type_name, value):
        if isinstance(window, int):
            window = self.get_window(window)
        if isinstance(type_name, int):
            prop_type_name = type_name
            #type_name = self.atom_name(prop_type_name)
        else:
            prop_type_name = self.atom(type_name, only_if_exists=False)

        prop_name = self.atom(name, only_if_exists=False)

        if value is None:
            window.delete_property(prop_name)
        else:
            window.change_property(prop_name, prop_type_name,
                                   32, value,
                                   mode=X.PropModeReplace,
                                   onerror=self.onerror)

    def send_prop_change_event(self, property_name, data, target=None, window=None, ):
        if target is None:
            target = self.root
        if window is None:
            window = target
        ev = protocol.event.ClientMessage(
            window=window,
            client_type=self.atom(property_name),
            data=data)
        target.send_event(
            ev,
            event_mask=X.SubstructureNotifyMask | X.SubstructureRedirectMask,
            propagate=False, onerror=self.onerror)

    def current_desktop(self, desktop=None, wait=True):
        prop_name = "_NET_CURRENT_DESKTOP"
        if desktop is None:
            pv = self.get_prop(self.root, prop_name)
            if pv:
                return pv[0]
        else:
            v = array('I', [ desktop ])
            #self.set_prop(self.root, prop_name, Xatom.CARDINAL, v)
            self.send_prop_change_event(prop_name, (32, [ desktop, X.CurrentTime, 0, 0, 0 ]))
            self.flush()
            w = Waiter(wait)
            while w.wait():
                print("DESKTOPCHECK", hex(desktop))
                if self.current_desktop() == desktop:
                    print("DESKTOP OK")
                    break


    def get_wm_pid(self, window):
        pid_prop = self.get_prop(window, "_NET_WM_PID")
        if pid_prop:
            return pid_prop[0]
        return None

    def get_wm_name(self, window):
        if isinstance(window, int):
            window = self.get_window(window)
        # window.get_wm_name gets only STRING property and returns nothing
        # if it's UTF8_STRING
        return self.get_text_prop(window, Xatom.WM_NAME)


    def active_window(self, window=None, wait=3, id_only=False):
        prop_name = "_NET_ACTIVE_WINDOW"
        if window is None:
            pv = self.get_prop(self.root, prop_name)
            if pv and pv[0]:
                window = self.get_window(pv[0])
                if window and window.get_wm_name() != 'Desktop':
                    if id_only:
                        return window.id
                    else:
                        return window
        else:
            if isinstance(window, int):
                window = self.get_window(window)
            desktop = self.get_desktop_for_window(window)
            self.current_desktop(desktop)
            #v = array('I', [ window.id, 0 ])
            #self.set_prop(self.root, prop_name, Xatom.WINDOW, v)
            # data[0]: source indication
            #   1: when the request comes from an application
            #   2: from a pager
            #   0: no spec.
            self.send_prop_change_event(prop_name,
                                        (32, [2, X.CurrentTime, 0, 0, 0]),
                                        window=window)
            self.flush()
            #self.raise_window(window)
            # it won't become active until it's focused
            focused = self.set_focused_window(window, wait=1)
            w = Waiter(wait)
            while w.wait():
                a = self.active_window()
                self.flush()
                if not focused:
                    focused = self.set_focused_window(window, wait=1)
                    self.flush()
                if a and a.id == window.id:
                    print("Activated %r!"% window.id)
                    return True
                self.send_prop_change_event(prop_name,
                                            (32, [2, X.CurrentTime, 0, 0, 0]),
                                            window=window)
                self.flush()
            print("Can't activate %d" % window.id)
            return False


    def get_focused_window(self, toplevel=True):
        f = self.display.get_input_focus()
        #f = protocol.request.GetInputFocus(display=self.display.display)
        if f.focus in [ X.NONE, X.PointerRoot ]:
            return None
        if toplevel:
            w = self.get_client_window(f.focus)
            if w is not None:
                return w.id
        return f.focus.id


    def raise_window(self, window):
        if isinstance(window, int):
            window = self.get_window(window)
        elif window is None:
            return
        window.raise_window()


    def focus_error(self, *args, **kwargs):
        print("Cannot set_input_focus: %r %r" % (args, kwargs))

    def set_focused_window(self, window, wait=3):
        if window is None:
            self.display.set_input_focus(X.NONE, X.RevertToParent, X.CurrentTime,
                                         onerror=self.focus_error)
            return True
        elif not wait:
            self.display.set_input_focus(window, X.RevertToParent, X.CurrentTime)
            return True
        else:
            with self.silenced_error(error.BadMatch):
                if isinstance(window, int):
                    window = self.get_window(window)
                self.display.set_input_focus(window, X.RevertToParent, X.CurrentTime)
                self.flush()
                w = Waiter(wait)
                while w.wait():
                    if w.timeout:
                        if w.progressed:
                            print("WAITING %.3f seconds more for focus on %r"
                                  % (w.remaining, window.id))
                        else:
                            print("READY TO WAIT %.3f seconds for focus on %r"
                                  % (w.remaining, window.id))
                    focused_win_id = self.get_focused_window()
                    if focused_win_id == window.id:
                        print("FOCUSED %r" % window.id)
                        return True
                    # many times it's needed to repeat the command, esp. when mouse is
                    # not inside the target window
                    self.display.set_input_focus(window, X.RevertToParent, X.CurrentTime)
                    self.flush()
                    #self.display.set_input_focus(window, X.RevertToParent, X.CurrentTime)
                    #self.display.flush()
            return False


    def get_desktop_for_window(self, window):
        pv = self.get_prop(window, "_NET_WM_DESKTOP")
        if pv:
            return pv[0]

    def set_desktop_for_window(self, window, desktop):
        if desktop is None:
            return
        name = self.atom("_NET_WM_DESKTOP", only_if_exists=True)
        if name in self.supported_properties:
            pv = self.set_prop(window, name, Xatom.CARDINAL, array('I', [ desktop ]))

    def save_state(self):
        state = {
            "Current Desktop": self.current_desktop(),
            "Active Window":   self.active_window(id_only=True),
            "Focused Window":  self.get_focused_window()
        }
        return state

    def restore_state(self, state):
        a = self.supported_properties
        self.current_desktop(state["Current Desktop"])
        self.flush()
        try:
            self.set_focused_window(state["Focused Window"])
        except error.BadWindow:
            print("Sorry, the old focused window went away...")
        # self.active_window(state["Active Window"])


    def keysym_to_string(self, keysym, friendly=False, very_friendly=False):
        if keysym not in self.keysyms.keysyms:
            return chr(keysym)
        if very_friendly:
            return self.keysyms.friendly_name(keysym, simplest=True)
        if friendly:
            return self.keysyms.friendly_name(keysym, simplest=False)
        else:
            return self.keysyms[keysym]

    def keycode_to_keysym(self, keycode, idx=None):
        if idx is None:
            syms = set()
            for i in range(4):
                keysym = self.display.keycode_to_keysym(keycode, i)
                if keysym:
                    syms.add(keysym)
            return syms
        else:
            return self.display.keycode_to_keysym(event.detail, i)

    def keysym_to_keycode(self, keysym):
        return self.display.keysym_to_keycode(keysym)

    def string_to_keysym(self, s):
        k = self.keysyms[s]
        if not k:
            k = self.keysyms["XK_" + s]
        if k:
            return k
        k = XK.string_to_keysym(s)
        return k
        # allow simpler names, like AudioRaiseVolume?
        # if s.startswith("XF86_"):
        #     s = "XF86" + s[5:]
        #     return XK.string_to_keysym(s)


    def error_handler(self, fn, *args, **kwargs):
        return functools.partial(fn, *args, **kwargs)


    def toggle_frame(self, window, frame=None, wait=1):
        """Set window frame. Value should be True or False for on and off, or None for toggle."""
        # flags - set bit for every iteresting value
        # 0 functions   => integer bits
        # 1 decorations => integer bits
        # 2 input_mode  => enum string or integer
        # 3 status      => integer bits
        #
        # functions:
        # bit    actions offered
        # ---    ---------------
        #  1     all functions
        #  2     resize window
        #  4     move window
        #  8     minimize, to iconify
        # 16     maximize, to full-screen (with a frame still)
        # 32     close window
        #
        # decorations:
        # bit       decorations displayed
        # ---       ---------------------
        #  1        all decorations
        #  2        border around the window
        #  4        resizeh, handles to resize by dragging
        #  8        title bar, showing WM_NAME
        # 16        menu, drop-down menu of the "functions" above
        # 32        minimize button, to iconify
        # 64        maximize button, to full-screen
        #
        # input mode:
        #   string                   integer
        # "modeless"                    0    not modal (the default)
        # "primary_application_modal"   1    modal to its "transient for"
        # "system_modal"                2    modal to the whole display
        # "full_application_modal"      3    modal to the current client
        #
        # status:
        #
        # bit
        #  1    tearoff menu window

        name = self.atom("_MOTIF_WM_HINTS", only_if_exists=True)
        # If does not exist, probably not supported, though should check
        # root for _NET_SUPPORTED list return assert prop != 0 pv =
        pv = self.get_prop(window, name)
        fe = self.get_frame_extents(window)
        if pv and len(pv) == 5:
            hints = array(pv.typecode, pv)
            if frame is None:
                hints[2] = 0 if hints[2] else 1
            elif frame:
                hints[2] = 1
            else:
                hints[2] = 0
        else:
            # reasonable default
            hints = array('I', [ 2, 0, 0, 0, 0 ])

        self.set_prop(window, name, name, hints)

        w = Waiter(wait)
        while w.wait():
            pv = self.get_prop(window, name)
            if pv and array(pv.typecode, pv) == hints:
                new_fe = self.get_frame_extents(window)
                # make sure frame extents changed
                # this seems to take a while once the hints change
                if new_fe != fe:
                    break


    def set_opacity(self, window, value):
        """value is a number between 0 and 1"""
        v = int(((1 << 32) - 1) * value)
        self.set_prop(window, "_NET_WM_WINDOW_OPACITY", Xatom.CARDINAL, array('I', [ v ]))

    def get_opacity(self, window):
        pv = self.get_prop(window, "_NET_WM_WINDOW_OPACITY")
        if pv:
            value = int(pv[0] / ((1 << 32) - 1))
            return value
        return 1

    @property
    def supported_properties(self):
        if self._supported_properties is None:
            self._supported_properties = self.get_prop(self.root, "_NET_SUPPORTED") or []
        return self._supported_properties


    def get_window(self, win_id):
        if isinstance(win_id, int):
            return self.display.create_resource_object('window', win_id)
        else:
            return win_id


    def get_client_window(self, window):
        win_id = window.id
        for tlw in self.toplevel_windows():
            for (_, parent, _) in self.window_tree(
                    tlw, filter=lambda w, parent, level: w.id == win_id):
                return tlw
        return None


    def toplevel_windows(self, id_only=False):
        name = self.atom("_NET_CLIENT_LIST", only_if_exists=True)
        if name in self.supported_properties:
            lst = self.get_prop(self.root, name)
            if id_only:
                return lst
            else:
                return list(map(lambda win_id: self.get_window(win_id), lst))
        else:
            print("BELGENGOC")
            if id_only:
                return list(map(lambda w: w.id, self.root.query_tree().children))
            else:
                return list(self.root.query_tree().children)


    def window_tree(self, parent=None, level=1, filter=None):
        if parent is None:
            parent = self.root
            if filter is None or filter(parent, None, 0):
                yield (parent, None, 0)
        for w in parent.query_tree().children:
            if filter is None or filter(w, parent, level):
                yield (w, parent, level)
                yield from self.window_tree(parent=w, level=level+1, filter=filter)


    def close_window(self, window):
        self.send_prop_change_event("_NET_CLOSE_WINDOW",
                                    (32, [0, 0, 0, 0, 0]),
                                    window=self.get_window(window))

    # https://specifications.freedesktop.org/wm-spec/wm-spec-1.3.html
    # window  = the respective client window
    # message_type = _NET_WM_STATE
    # format = 32
    # data.l[0] = the action, as listed below
    # data.l[1] = first property to alter
    # data.l[2] = second property to alter
    # data.l[3] = source indication
    #  other data.l[] elements = 0
    # This message allows two prop
    #
    _NET_WM_STATE_REMOVE = 0 # remove/unset property
    _NET_WM_STATE_ADD = 1 #add/set property
    _NET_WM_STATE_TOGGLE = 2 # toggle property

    def set_wm_states(self, window, names, action=None):
        if action is None:
            action = self._NET_WM_STATE_TOGGLE
        elif action is True:
            action = self._NET_WM_STATE_ADD
        elif action is False:
            action = self._NET_WM_STATE_REMOVE
        window = self.get_window(window)
        values = list()
        for name in names:
            value = self.atom("_NET_WM_STATE_%s" % name.upper())
            values.append(value)
        data = [action, *values]
        while len(data) < 5:
            data.append(0)
        self.send_prop_change_event("_NET_WM_STATE",
                                    (32, data),
                                    window=self.get_window(window))

    def set_wm_state(self, window, name, action=None):
        if action is None:
            action = self._NET_WM_STATE_TOGGLE
        elif action is True:
            action = self._NET_WM_STATE_ADD
        elif action is False:
            action = self._NET_WM_STATE_REMOVE
        window = self.get_window(window)
        value = self.atom("_NET_WM_STATE_%s" % name.upper())
        self.send_prop_change_event("_NET_WM_STATE",
                                    (32, [action, value, 0, 0, 0]),
                                    window=self.get_window(window))

    def below_window(self, window, action=None):
        self.set_wm_state(window, name="below", action=action)
    def fullscreen_window(self, window, action=None):
        self.set_wm_state(window, name="fullscreen", action=action)
    def above_window(self, window, action=None):
        self.set_wm_state(window, name="above", action=action)
    def sticky_window(self, window, action=None):
        self.set_wm_state(window, name="sticky", action=action)
    def skip_pager(self, window, action=None):
        self.set_wm_state(window, name="skip_pager", action=action)
    def skip_taskbar(self, window, action=None):
        self.set_wm_state(window, name="skip_taskbar", action=action)

    def maximize_window(self, window, horizontal=True, vertical=True, action=None):
        if horizontal:
            self.set_wm_state(window, name="maximized_horz", action=action)
        if vertical:
            self.set_wm_state(window, name="maximized_vert", action=action)

    def minimize_window(self, window):
        if isinstance(window, int):
            window = self.get_window(window)
        self.send_prop_change_event("WM_CHANGE_STATE",
                                    (32, [Xutil.IconicState, 0, 0, 0, 0]),
                                    window=self.get_window(window))

    def get_attributes(self, window):
        if isinstance(window, int):
            window = self.get_window(window)
        return window.get_attributes()

    def get_frame_extents(self, window):
        # x, y, width, height
        if isinstance(window, int):
            window = self.get_window(window)
        e = self.get_prop(window, "_NET_FRAME_EXTENTS")
        if e:
            return self.FrameExtents(*e)
        else:
            return self.FrameExtents(0, 0, 0, 0)

    def get_geometry(self, window):
        # x, y, width, height
        if isinstance(window, int):
            window = self.get_window(window)
        return window.get_geometry()

    def set_geometry(self, window, **data):
        # x, y, width, height
        if isinstance(window, int):
            window = self.get_window(window)
        if any(map(lambda v: v < 0, data.values())):
            gw = self.get_geometry(window)
            f = self.get_frame_extents(window)
            wa = self.usable_workarea()

            print("GEOMETRY: workarea %r, window %r, frame %r" % (wa, gw, f))
            if 'x' in data and data['x'] < 0:
                data['x'] = wa.width - gw.width - (f.left + f.right) + data['x'] + 1
            else:
                data['x'] += wa.x
            if 'y' in data and data['y'] < 0:
                data['y'] = wa.height - gw.height - (f.top + f.bottom) + data['y'] + 1
            else:
                data['y'] += wa.y
        window.configure(**data)

    def usable_workarea(self):
        a = self.get_prop(self.root, "_NET_WORKAREA")
        if a:
            p = self.current_desktop() * 4
            #return (x, y, width, height)
            return self.Geometry(*a[p:p+4])
        else:
            r = self.get_geometry(self.root)
            return self.Geometry(0, 0, r.width, r.height)


    def send_key(self, window, keysym, modifiers):
        if isinstance(window, int):
            window = self.get_window(window)
        keycode = self.display.keysym_to_keycode(keysym)
        event = protocol.event.KeyPress(
            time=X.CurrentTime,
            root = self.root,
            window = window,
            child = X.NONE,
            same_screen = True,
            root_x = 0,
            root_y = 0,
            event_x = 0,
            event_y = 0,
            state = modifiers.bitmap,
            detail = keycode)
        window.send_event(event, propagate=False)
        event = protocol.event.KeyRelease(
            time=X.CurrentTime,
            root = self.root,
            window = window,
            child = X.NONE,
            same_screen = True, # same screen as the root window
            root_x = 0,
            root_y = 0,
            event_x = 0,
            event_y = 0,
            state = modifiers.bitmap,
            detail = keycode)
        window.send_event(event, propagate=False)

    def show_desktop(self, action=None):
        prop_name = self.atom("_NET_SHOWING_DESKTOP")
        if action is True:
            self.send_prop_change_event(prop_name, (32, [ 1, X.CurrentTime, 0, 0, 0 ]))
        elif action is False:
            self.send_prop_change_event(prop_name, (32, [ 0, X.CurrentTime, 0, 0, 0 ]))
        else:
            pv = self.get_prop(self.root, prop_name)
            new_val = 0 if pv and pv[0] else 1
            self.send_prop_change_event(prop_name, (32, [ new_val, X.CurrentTime, 0, 0, 0 ]))


    def flush(self):
        # send all pending events
        self.display.flush()
    def sync(self):
        # flush and make sure everything is handled and processed or rejected by the server
        self.display.sync()

#     def send_key(self, emulated_key):
#         shift_mask = 0  # or Xlib.X.ShiftMask
#         window = self.display.get_input_focus()._data["focus"]
#         keysym = XK.string_to_keysym(emulated_key)
#         keycode = self.display.keysym_to_keycode(keysym)
#         event = protocol.event.KeyPress(
#             time=int(time.time()),
#             root=self.root,
#             window=window,
#             same_screen=0, child=X.NONE,
#             root_x=0, root_y=0, event_x=0, event_y=0,
#             state=shift_mask,
#             detail=keycode)
#         )
#         window.send_event(event, propagate=True)
#         event = protocol.event.KeyRelease(
#             time=int(time.time()),
#             root=self.display.screen().root,
#             window=window,
#             same_screen=0, child=X.NONE,
#             root_x=0, root_y=0, event_x=0, event_y=0,
#             state=shift_mask,
#             detail=keycode
#         )
#         window.send_event(event, propagate=True)
# Example 5
