import configparser
import os
import re
from Xlib import X
from keybender.knox import Modifiers, KnoX, Waiter as Sleeper
from keybender.event import Event
from keybender.rctl import StreamSender
from types import GeneratorType
from collections.abc import Iterable
import functools
import shlex
import fnmatch
import subprocess
import time, datetime
import sys
import fcntl
import pickle, base64

class Step:
    def __init__(self, *args):
        self.actions = []
        self.triggers = None

    def execute(self, *args, **kwargs):
        for a in self.actions:
            a.execute(*args, **kwargs)


class Key:
    def __init__(self, knox, descr, origin=None, mods=None, keysym=None):
        """if keysym is None, itt will take everything as modifier it can, and use the
        first non-modifier one as a key. When keysym is False it will not
        permit non-modifier keys and raise an exception if found one. When it
        is True, and no keysym found so far, it will use the last key as
        keysym, even if it could be used as a modifier.
        """

        self.knox = knox
        self._net_modifiers = None
        self.keysym = None
        self.negate = False
        self.named_modifiers = None

        if mods:
            self.named_modifiers = Modifiers(self.knox, mods.all())

        if self.named_modifiers is None:
            self.named_modifiers = Modifiers(self.knox, None)

        if descr:
            descr = descr.strip()
        if not descr:
            return
        while descr.startswith("not "):
            descr = descr[4:].strip()
            self.negate = not self.negate
        l = list(filter(lambda s: len(s) > 0,
                        map(lambda s: s.strip(),
                            descr.split('+'))))
        for (i,n) in enumerate(l):
            mods = self.knox.modifiers.find(name=n)
            force_keysym = (keysym is True and i == len(l) - 1 and not self.keysym)
            if mods and not force_keysym:
                for m in mods:
                    self.named_modifiers.add(m)
                    break
            elif not self.keysym:
                self.keysym = self.knox.string_to_keysym(n)

                if not self.keysym:
                    raise Exception("Unrecognized key '%s' in %s" % (n, origin))
        if keysym is None:
            pass
        elif keysym:
            if self.keysym is None:
                # maybe use the last one as keysym...
                raise Exception("Missing non-modifier key in '%s' in %s" (descr, origin))
        else:
            if self.keysym is not None:
                raise Exception("Non-modifier key '%s' in %s"
                                % (self.knox.keysym_to_string(self.keysym), origin))


    @property
    def modifiers(self):
        if not self.negate:
            return self.named_modifiers
        elif self._net_modifiers is None:
            self._net_modifiers = ~self.named_modifiers
        return self._net_modifiers

    def __str__(self):
        l = [ str(m) for m in self.named_modifiers.all() ]
        if self.keysym:
            l.append(self.knox.keysym_to_string(self.keysym))
        if self.negate:
            return "not " + "+".join(l)
        else:
            return "+".join(l)

    def __eq__(a, b):
        return a.keysym == b.keysym and a.modifiers.bitmap == b.modifiers.bitmap

    def __hash__(self):
        return hash((a.keysym, a.modifiers.bitmap))

class Listener(Step):
    def __init__(self, config, section, name=None):
        super().__init__(self, config, section)
        self.config = config
        self.section = section
        self._name = name
        self._description = None
        for e in section:
            if e == 'triggers':
                self.triggers = Config.Parser.trigger_list(config, section, e)
            elif e == 'mask':
                pass
            elif e == 'execute':
                action_name = "action:%s" % section[e]
                self.actions.append(config.action(action_name))
            elif e == 'description':
                self._description = section[e]
            elif e == 'comment':
                pass
            else:
                raise Exception("Unrecognized entry '%s' in section '%s'"
                                % (e, section.name))

    @property
    def name(self):
        if self._name is not None:
            return self._name
        elif self.section:
            return self.section.name
        else:
            return "?"


class Start(Listener):
    pass


class Action(Step):
    def __new__(cls, config, section, *args, **kwargs):
        actions = []
        if cls == Action:
            for e in section:
                if e == 'select-windows':
                    # this goes first so it fills up the target list before other actions
                    actions[0:0] = [ WindowSelector ]
                elif e == 'run':
                    actions.append(ShellCommandAction)
                elif e == 'consult':
                    actions.append(ConsultCommandAction)
                elif e == 'do':
                    actions.append(AutonomousCommandAction)
                else:
                    print("Warning: Unrecognized entry '%s' in section '%s'"
                          % (e, section.name))
        if len(actions) > 1:
            # __init__ is called on the returned object...
            return MultiAction(config, section, *actions)
        elif actions:
            return actions[0](config, section)
        else:
            return object.__new__(cls)

    def __init__(self, config, section):
        raise Exception("Unrecognized action in section '%s'" % section.name)


class MultiAction(Action):
    def __init__(self, config, section, *actions):
        if getattr(self, '_initialized', False):
            # __init__ is called twice if this object is created and
            # initialized and then returned from __new__
            return
        self._initialized = True
        self.actions = []
        assert actions
        for action_class in actions:
            self.actions.append(action_class(config, section))

    def __repr__(self):
        return "MultiAct(%s)" % ' + '.join(map(repr, self.actions))

    def execute(self, *args, **kwargs):
        r = None
        for a in self.actions:
            ra = a.execute(*args, **kwargs)
            if ra is not None:
                if r is None:
                    r = list()
                r.extend(ra)
        return r


class ShellCommandAction(Action):
    def __init__(self, config, section, cmd=None):
        self.config = config
        self.section = section
        if cmd is not None:
            self.cmd = cmd.strip()
        else:
            self.cmd = self.section['run'].strip()

    def execute(self, *args, **kwargs):
        print("RUNNING %r" % self.cmd)
        os.system(self.cmd)
    def __repr__(self):
        return "run(%r)" % self.cmd

class Consultant:

    def call_toggle_fn(self, fn, w):
        actions = {
            '+': KnoX._NET_WM_STATE_ADD,
            '-': KnoX._NET_WM_STATE_REMOVE,
            '!': KnoX._NET_WM_STATE_TOGGLE,
        }
        if w and w[0] in actions:
            action = actions[w[0]]
            w = w[1:]
        else:
            action = None
        return fn(int(w), action=action)

    def toggle_frame(self, w):
        frame_states = {
            '+': True,
            '-': False,
            '!': None
        }
        if w and w[0] in frame_states:
            return self.config.knox.toggle_frame(int(w[1:]), frame=frame_states[w[0]])
        else:
            return self.config.knox.toggle_frame(int(w))

    def __init__(self, config):
        self.config = config
        self.commands = {
            'select-windows': self.select_windows,
            'close': lambda w: self.config.knox.close_window(int(w)),
            'minimize': lambda w: self.config.knox.minimize_window(int(w)),
            'frame': self.toggle_frame,
            'raise': lambda w: self.config.knox.raise_window(int(w)),
            'activate': lambda w: self.config.knox.active_window(int(w)),
            'focus': lambda w: self.config.knox.set_focused_window(int(w)),
            'below': functools.partial(
                self.call_toggle_fn,
                self.config.knox.below_window),
            'fullscreen': functools.partial(
                self.call_toggle_fn,
                self.config.knox.fullscreen_window),
            'sticky': functools.partial(
                self.call_toggle_fn,
                self.config.knox.sticky_window),
            'skip_pager': functools.partial(
                self.call_toggle_fn,
                self.config.knox.skip_pager),
            'skip_taskbar': functools.partial(
                self.call_toggle_fn,
                self.config.knox.skip_taskbar),
            'maximize': functools.partial(
                self.call_toggle_fn,
                self.config.knox.maximize_window),
            'geometry': self.geometry,
            'save_state': self.save_state,
            'restore_state': self.restore_state,
            'action': self.call_action,
            'key': self.send_keys,
            'send_keys': self.send_keys,
            'desktop': self.show_desktop,
        }

    def incoming(self, lines, responder=None):
        cnt = 0
        if responder is None:
            responder = lambda x: x
        for s in lines:
            cnt += 1
            found=False
            print("Incoming: %r" % s)
            if s == 'bye':
                responder([ "bye\n" ])
                return 0

            for k in self.commands.keys():
                prefix = k + ":"
                if s.startswith(prefix) or s == k:
                    a = s[len(prefix):]
                    r = self.commands[k](a.strip())
                    if isinstance(r, str):
                        responder([r + "\n"])
                    elif isinstance(r, Iterable):
                        responder(r)
                    elif r is None or r is True:
                        self.config.knox.flush()
                        responder("OK\n")
                    elif r is False:
                        responder("Failed\n")
                    found = True
                    break
            if not found:
                print("Bad command from external process: %r" % s)
        return cnt

    def call_action(self, s):
        a = self.config.action("action:" + s)
        a.execute()

    def geometry(self, s):
        # TODO: WxH, pero tambien *Wx*H para multiplos (float) del workarea
        # size. accepto ! after numbers (position and size) for using screen
        # space instead of workarea
        m = re.match(r'^\s*(?P<win_id>\d+)\s*'
                     r'((?P<w_op>[*]\s*)?(?P<width>[.\d]+)\s*(?P<sz_selector_w>[!])?'
                     r'\s*x\s*'
                     r'((?P<h_op>[*]\s*)?(?P<height>[.\d]+)\s*(?P<sz_selector_h>[!])?))?'
                     r'('
                     r'\s*(?P<x_sign>[+-]\s*)(?P<x>\d+)\s*(?P<sz_selector_x>[!])?'
                     r'\s*(?P<y_sign>[+-]\s*)(?P<y>\d+)\s*(?P<sz_selector_y>[!])?'
                     r')?\s*$', s)
        if not m:
            raise Exception("Syntax error in geometry string: %r'" % s)
        win_id = int(m['win_id'])
        args = dict()

        wa = self.config.knox.usable_workarea()
        ra = self.config.knox.get_geometry(self.config.knox.root)
        w = self.config.knox.get_geometry(win_id)
        f = self.config.knox.get_frame_extents(win_id)

        if m['width']:
            sz = ra if m['sz_selector_w'] and m['sz_selector_w'] == '!' else wa
            if m['w_op'] and m['w_op'][0] == '*':
                args['width'] = int(sz.width * float(m['width']))
            else:
                args['width'] = int(float(m['width']))
        if m['height']:
            sz = ra if m['sz_selector_h'] and m['sz_selector_h'] == '!' else wa
            if m['h_op'] and m['h_op'][0] == '*':
                args['height'] = int(sz.height * float(m['height']))
            else:
                args['height'] = int(float(m['height']))

        if m['x']:
            sz = ra if m['sz_selector_x'] and m['sz_selector_x'] == '!' else wa
            if m['x_sign'] and m['x_sign'].startswith('-'):
                args['x'] = sz.width - (args.get('width', w.width) + int(m['x']) + f.left + f.right)
            else:
                args['x'] = sz.x + int(m['x'])
        if m['y']:
            sz = ra if m['sz_selector_y'] and m['sz_selector_y'] == '!' else wa
            if m['y_sign'] and m['y_sign'].startswith('-'):
                args['y'] = sz.height - (args.get('height', w.height) + int(m['y']) - f.top + f.bottom)
            else:
                args['y'] = sz.y + int(m['y'])

        self.config.knox.set_geometry(win_id, **args)

    def show_desktop(self, s):
        if s == '-':
            self.config.knox.show_desktop(action=False)
        elif s == '+':
            self.config.knox.show_desktop(action=True)
        elif s == '!':
            self.config.knox.show_desktop(action=None)
        else:
            raise Exception("Syntax error in desktop command: %r" % s)


    def select_windows(self, s):
        prefix="select-windows:"
        full_msg = prefix + s
        (_, name, args, timeout) = Config.Parser.section_reference(
            self.config, "remote control message", prefix, full_msg, timeout=True)
        print("Selecting %r" % name)
        if not name:
            return
        section_name = "match-window:%s" % name
        if args:
            section_backup = self.config.save_section(section_name)
            for (entry_name, value) in args.items():
                self.config.config.set(section_name, entry_name, value)
        else:
            section_backup = None
        ws = WindowSelector.Worker(
            parent=None,
            finder=self.config.window_finder(section_name, use_cache=False),
            destination=None)
        first = True
        if timeout:
            started = datetime.datetime.now()
        r = ""
        while timeout or first:
            first = False
            r = ws.execute()
            if r or not timeout:
                break
            now = datetime.datetime.now()
            if (now - started).total_seconds() > timeout:
                break
            print("WAITING FOR %s" % (full_msg))
            time.sleep(0.1)
        if section_backup is not None:
            self.config.restore_section(section_name, section_backup)
        return "select-windows:%s %s" % (name, r or "")


    def save_state(self, *args):
        state = self.config.knox.save_state()
        return "save_state %s" % base64.b64encode(pickle.dumps(state)).decode()

    def restore_state(self, s):
        if not s:
            return
        state = pickle.loads(base64.b64decode(s))
        self.config.knox.restore_state(state)


    def send_keys(self, s):
        ps = s.split(maxsplit=1)
        if len(ps) != 2:
            print("Syntax error in key command, missing window id: %r" % s)
            return False
        window_id = int(ps[0])
        descr_lst = list()
        for v in shlex.shlex(ps[1], posix=True):
            if (v == '+' and descr_lst) or (descr_lst and descr_lst[-1][-1] == '+'):
                descr_lst[-1] += v
            else:
                descr_lst.append(v)
        for descr in descr_lst:
            k = Key(self.config.knox, descr, origin="incoming command key:%r" % s)
            self.config.knox.send_key(window_id, k.keysym, k.modifiers)
        self.config.knox.flush()


class ConsultCommandAction(Action):
    def __init__(self, config, section):
        self.config = config
        self.section = section

    def __repr__(self):
        return "consult(%r)" % self.section.get('consult', '?', raw=True).strip()

    def call_action(self, s):
        a = self.config.action("action:" + s)
        a.execute()

    def incoming_command(self, event, event_loop):
        n = event.consultant.incoming(
            map(lambda l: l.decode().strip(),
                event.child.stdout.readlines()))

        if n == 0:
            exit_code = event.child.wait()
            if exit_code:
                print("Child process exited with %r: %r" % (exit_code, event.command))
            event_loop.unregister(event.key)


    def execute(self, *args, **kwargs):
        cmd = self.section['consult'].strip()
        print("CONSULTING %r" % cmd)
        child = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=sys.stderr)

        print("GOT STREAMS #%r for talking and #%r for listening"
              % (child.stdin.fileno(), child.stdout.fileno()))
        fl = fcntl.fcntl(child.stdout, fcntl.F_GETFL)
        fcntl.fcntl(child.stdout, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        fl = fcntl.fcntl(child.stdin, fcntl.F_GETFL)
        fcntl.fcntl(child.stdin, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        k = self.config.event_loop.register(
            Event.READABLE, self.control_message,
            fd=child.stdout,
            child=child,
            command=cmd,
            consultant=Consultant(self.config))

    def control_message(self, event, event_loop):
        data = event.fd.read()
        if not data:
            print("CLOSING incoming #%r and outgoing #%r"
                  % (event.child.stdout.fileno(), event.child.stdin.fileno()),
                  "==" * 20)
            event.child.stdout.close()
            event.child.stdin.close()
            event_loop.unregister(event.key)
        else:
            event.consultant.incoming(
                data.decode('utf-8').splitlines(),
                responder=StreamSender(event.child.stdin, event_loop))


class AutonomousCommandAction(Action):
    def __init__(self, config, section):
        self.config = config
        self.section = section

    def __repr__(self):
        return "do(%r)" % self.section.get('do', '?', raw=True).strip()

    def execute(self, *args, **kwargs):
        consultant = Consultant(self.config)
        commands = self.section['do']
        n = 0
        for cmd in commands.split(';'):
            n += 1
            cmd = cmd.strip()
            consultant.incoming([ cmd ],
                                responder=functools.partial(self.chatter, cmd))

    def chatter(self, cmd, whatever):
        print("Response to do %r: %r" % (cmd, whatever))



class WindowSelector(Action):

    class Worker:
        def __init__(self, parent, finder, destination):
            self.parent = parent
            self.finder = finder
            self.destination = destination
        def execute(self, *args, **kwargs):
            v = " ".join(map(str, self.finder(*args, **kwargs)))
            if self.destination is not None:
                self.parent.section[self.destination] = v
            else:
                return v

    def __init__(self, config, section):
        self.config = config
        self.section = section
        self.workers = []
        self.waits = dict()
        e = 'select-windows'
        for s in section[e].split(';'):
            v = s.split()
            if len(v) == 3 and v[1] == 'into':
                self.workers.append(
                    self.Worker(self, self.config.window_finder("match-window:%s" % v[0]), v[2]))
            else:
                raise Exception(
                    "Wrong in entry '%s' in section '%s'. Should be '<section-name> into <entry-name>' but it's '%s'"
                    % (e, section.name, s))
        self.process_waits()

    def process_waits(self):
        if 'wait' not in self.section:
            return
        for s in self.section['wait'].split(';'):
            m = re.match(
                r'^(?P<time>\d+)s\s+for\s+(?P<list>{name_chars}+)\s*$'
                .format(name_chars=Config.name_chars), s)
            if not m:
                raise Exception("Syntax error in entry '%s' in section '%s', in %r"
                                % ('wait', section.name, s))
            self.waits[m['list']] = int(m['time'])

    def execute(self, *args, **kwargs):
        waits = dict(**self.waits)
        s = Sleeper(0)
        while s.wait():
            s.timeout = max(waits.values() or [ 0 ])
            for w in self.workers:
                w.execute(*args, **kwargs)
            for (name, patience) in list(waits.items()):
                if not self.section[name]:
                    print("Entry %s still empty" % name, datetime.datetime.now())
                    now = datetime.datetime.now()
                else:
                    del waits[name]
                    if not waits:
                        break
        if waits:
            print("Nothing good came for %s" % ",".join(waits.keys()))


class Expression:
    operators = "()!|&"
    def __init__(self, txt, translator):
        self.translator = translator
        lst = self.cleanup(shlex.shlex(txt, posix=True, punctuation_chars=self.operators))
        sofat = None
        (expr, rest) = self.compile(lst, sofar=None, expect_value=True)
        if rest:
            raise Exception("Syntax error, still having this: %r" % (rest,))
        self.expr = expr

    def __call__(self, *args, **kwargs):
        return self.expr(*args, **kwargs)

    def __repr__(self):
        return ("Expr(%r)" % self.expr)

    def cleanup(self, token_groups):
        r = []
        for tg in token_groups:
            if all(map(lambda c: c in self.operators, tg)):
                r.extend(list(tg))
            else:
                r.append(tg)
        return r

    priorities = {
        '|': 20,
        '&': 40,
        '!': 99,
    }

    def compile(self, lst, sofar=None, priority=0, expect_value=True):
        if not lst and not expect_value:
            return (sofar, lst)
        elif not lst:
            raise Exception("Missing value on the end of epression")

        if lst[0] in self.priorities:
            new_priority = self.priorities[lst[0]]
        else:
            new_priority = None

        if lst[0] == '(' and expect_value:
            (expr, rest) = self.compile(lst[1:], sofar=None, priority=0, expect_value=True)
            if not (rest and rest[0] == ')'):
                raise Exception("Missing closing parenthesis")
            return (expr, rest[1:])
        elif lst[0] == ')' and not expect_value:
            return (sofar, lst)
        elif lst[0] == '!' and expect_value:
            (expr, rest) = self.compile(
                lst[1:], sofar=None, priority=new_priority, expect_value=True)
            r_expr = self.translator.compile_op(lst[0], expr)
            return (r_expr, rest)
        elif lst[0] in '&|' and not expect_value:
            if priority > new_priority:
                return (sofar, lst)
            (expr, rest) = self.compile(
                lst[1:], sofar=None, priority=new_priority, expect_value=True)
            r_expr = self.translator.compile_op(lst[0], sofar, expr)
            return self.compile(
                rest, sofar=r_expr, priority=priority, expect_value=False)
        elif lst[0] not in self.operators:
            l_expr = self.translator.compile_token(lst[0])
            (r_expr, rest) = self.compile(
                lst[1:], sofar=l_expr, priority = priority, expect_value=False)
            return (r_expr, rest)

        raise Exception("Whatsgoinon, expect_value=%r, sofar=%r, lst=%r, priority=%r, new_priority=%r" %(expect_value, sofar, lst, priority, new_priority))


    class And:
        def __init__(self, a, b):
            self.a = a
            self.b = b
        def __call__(self, *args, **kwargs):
            return self.a(*args, **kwargs) and self.b(*args, **kwargs)
        def __repr__(self):
            return ("And(%r, %r)" % (self.a, self.b))

    class Or:
        def __init__(self, a, b):
            self.a = a
            self.b = b
        def __repr__(self):
            return ("Or(%r, %r)" % (self.a, self.b))
        def __call__(self, *args, **kwargs):
            return self.a(*args, **kwargs) or self.b(*args, **kwargs)

    class Not:
        def __init__(self, v):
            self.v = v
        def __repr__(self):
            return ("Not(%r)" % (self.v))
        def __call__(self, *args, **kwargs):
            return not self.v(*args, **kwargs)




class TokenMatch:
    def __init__(self, token):
        self.token = token
    def __repr__(self):
        return ("Token:%r" % self.token)


class WindowFinder:
    class MatchAttr:
        class StringCompare:
            def __init__(self, wanted):
                self.wanted = wanted
            def __call__(self, value):
                if isinstance(self.wanted, str) and isinstance(value, str):
                    return fnmatch.fnmatch(value, self.wanted)
                else:
                    return self.wanted == value
            def __repr__(self):
                return repr(self.wanted)

        class Translator:
            def compile_token(self, s):
                return WindowFinder.MatchAttr.StringCompare(s)
            def compile_op(self, op, *args):
                ops = {
                    "|": Expression.Or,
                    "&": Expression.And,
                    "!": Expression.Not
                }
                return ops[op](*args)

        def __init__(self, section, entry, getter):
            self.getter = getter
            self.expression = Expression(section[entry], self.Translator())


        def __call__(self, window):
            value = self.getter(window)
            return self.expression(value)

    def get_name(self, window):
        return self.config.knox.get_wm_name(window)
    def get_class(self, window):
        cls = window.get_wm_class()
        return cls[-1] if cls else None
    def get_instance(self, window):
        cls = window.get_wm_class()
        return cls[0] if cls else None

    def get_pid(self, window):
        return str(self.config.knox.get_wm_pid(window))

    class MatchAll:
        def __init__(self, finder, *matchers):
            self.matchers = list(matchers)
        def __call__(self, window):
            for m in self.matchers:
                if not m(window):
                    return False
            return True

    class MatchAny:
        def __init__(self, finder, *matchers):
            self.matchers = matchers
        def __call__(self, window):
            for m in self.matchers:
                if m(window):
                    return True
            return False


    def __init__(self, config, section):
        self.config = config
        self.section = section
        self.config.config.BOOLEAN_STATES['any'] = None
        self.config.config.BOOLEAN_STATES['?'] = None
        self.match = self.MatchAll(self)
        self.toplevel = True
        self.focused = None

        getters = {
            'title': self.get_name,
            'name': self.get_name,
            'class': self.get_class,
            'instance': self.get_instance,
            'pid': self.get_pid
        }

        if 'title' in section and 'name' in section:
            raise Exception(
                "title and name refer to the same property in section '%s'"
                % (section.name))

        for e in section:
            if e == 'match':
                if section[e] == 'any':
                    self.match = self.MatchAny(self, *self.match.matchers)
                elif section[e] == 'all':
                    self.match = self.MatchAll(self, *self.match.matchers)
                else:
                    raise Exception(
                        "Unrecognized value in entry '%s' in section '%s'"
                        % (e, section.name))
            elif e in getters:
                self.match.matchers.append(
                    self.MatchAttr(section, e, getters[e]))
            elif e == 'focused':
                self.focused = section.getboolean(e)
            elif e == 'toplevel':
                self.toplevel = section.getboolean(e)
                # if section[e] in ['0', 'no', 'false']:
                #     self.toplevel = False
                # elif section[e] in ['1', 'yes', 'true']:
                #     self.toplevel = True
                # elif section[e] in ['any']:
                #     self.toplevel = None
                # else:
                #     raise Exception(
                #         "Unrecognized value in entry '%s' in section '%s'"
                #         % (e, section.name))
            else:
                raise Exception("Unrecognized entry '%s' in section '%s'"
                                % (e, section.name))

    def get_focused_window(self):
        n = "Focused Window"
        if self.x_state and n in self.x_state:
            if self.x_state[n] is not None:
                f = set([self.x_state[n]])
            else:
                f = set()
        else:
            f = set([self.config.knox.get_focused_window()])
        return f


    def __call__(self, *args, x_state=None, **kwargs):
        self.x_state = x_state

        if self.toplevel is True and self.focused is True:
            wls = (
                set(self.config.knox.toplevel_windows(id_only=True) or [])
                & self.get_focused_window())
        elif self.toplevel is True and self.focused is False:
            wls = (
                set(self.config.knox.toplevel_windows(id_only=True))
                - self.get_focused_window())
        elif self.toplevel is True: # and self.focused is None
            wls = set(self.config.knox.toplevel_windows(id_only=True) or [])
        elif self.toplevel is False and self.focused is True:
            wls = (
                self.get_focused_window()
                - set(self.config.knox.toplevel_windows(id_only=True) or []))
        else:
            wls = set()
            for (window, _, _) in self.config.knox.window_tree():
                wls.add(window.id)
            if self.toplevel is False:
                wls -= set(self.config.knox.toplevel_windows(id_only=True) or [])
            if self.focused is True:
                wls &= self.get_focused_window()
            elif self.focused is False:
                wls -= self.get_focused_window()

        if self.x_state and "Ignore" in self.x_state:
            wls -= self.x_state["Ignore"]

        lst = []
        for win_id in wls:
            if self.match(self.config.knox.get_window(win_id)):
                lst.append(win_id)
        return lst


class TriggerList():
    def __init__(self, config, section, entry, triggers=None):
        self.triggers = []
        self.section = section
        self.entry = entry
        if triggers is not None:
            self.triggers = triggers
        # else:
        #     for w in Config.Parser.waiter_list(config, section, entry):
        #         self.add(w)

    def add(self, trigger):
        for t in self.triggers:
            if t.key == trigger.key:
                raise Exception(
                    "Multiple waiters (%s and %s) in entry '%s' in section '%s' waiting for the same trigger: %s"
                    % (w.section.name, waiter.section.name,
                       self.entry, self.section.name, t.key))
        self.triggers.append(trigger)

    def __iter__(self):
        return iter(self.triggers)


class Trigger(Step):
    def __init__(self, trigger, mask, action=None, waiter=None):
        super().__init__()
        self.key = trigger
        self.mask = mask
        # self.waiter = waiter
        if action:
            self.actions.append(action)
        self.waiter = waiter
    def __repr__(self):
        return ("Trigger(%s)<w:%r,x:%r>" % (self.key, self.waiter, self.actions))

class Waiter(Listener):
    pass


class Config:
    name_chars=r'[-\w$]'

    def __init__(self, knox, filename, event_loop, extra_options=None):
        self.knox = knox
        self.event_loop = event_loop
        self.config = configparser.ConfigParser(
            interpolation=configparser.ExtendedInterpolation())
        self.config.add_section("env")
        for (name, value) in os.environ.items():
            self.config["env"][name] = value
        self.extra_options = dict()
        for (name, value) in extra_options.items():
            parts = name.split(':', maxsplit=1)
            if len(parts) == 1:
                self.extra_options[("cfg", parts[0])] = value
            else:
                self.extra_options[(parts[0], parts[1])] = value

        self.config.read(filename)
        self.add_extra_options(self.extra_options)
        self.add_extra_options(os.environ, section="env")

        self.waiters = dict()
        self.actions = dict()
        self.finders = dict()
        self.start = Start(self, self.config['start'])
        self.config_file = filename
        self.config_id = os.stat(filename).st_mtime

    def add_extra_options(self, options, section=None):
        for (option_name, value) in options.items():
            if isinstance(option_name, tuple):
                (section_name, option_name) = option_name
            else:
                section_name = section
            if not self.config.has_section(section_name):
                self.config.add_section(section_name)
            if not self.config.has_option(section_name, option_name):
                self.config[section_name][option_name] = value

    def changed(self):
        return not (os.stat(self.config_file).st_mtime == self.config_id)

    def reload(self):
        return Config(self.knox, self.config_file, self.event_loop)


    def waiter(self, name):
        if name in self.waiters:
            return self.waiters[name]
        else:
            w = Waiter(self, self.config[name])
            self.waiters[name] = w
            return w

    def action(self, name):
        if name in self.actions:
            return self.actions[name]
        elif name in self.config:
            a = Action(self, self.config[name])
            self.actions[name] = a
            return a
        else:
            raise Exception("Action %r not found" % name)

    def window_finder(self, name, use_cache=True):
        if name in self.finders and use_cache:
            return self.finders[name]
        else:
            f = WindowFinder(self, self.config[name])
            self.finders[name] = f
            return f

    def save_section(self, section):
        return list(map(lambda e: (e, self.config[section][e]), self.config[section].keys()))

    def restore_section(self, section, bkp):
        cur_keys = set(self.config[section].keys())
        orig_keys = set(map(lambda e: e[0], bkp))
        for (e, v) in bkp:
            self.config[section][e] = v
        for k in cur_keys - orig_keys:
            del self.config[section][k]


    class Parser:
        @classmethod
        def waiter_list(cls, config, section, entry):
            waiters = []
            for w in section[entry].split(','):
                w = w.strip()
                if not w:
                    raise Exception("Syntax error in entry '%s' in section '%s'"
                                    % (entry, section.name))
                waiter_name = "waiter:%s" % w
                waiters.append(config.waiter(waiter_name))
            return waiters

        @classmethod
        def trigger_list(cls, config, section, entry):
            triggers = TriggerList(config, section, entry)
            mask = Key(config.knox, section.get('mask', ''),
                       origin="entry 'mask' in section '%s'" % section.name)
            for t in section[entry].split(';'):
                t = t.strip()
                if not t:
                    continue
                ps = t.split('::')
                if len(ps) != 2:
                    raise Exception("Syntax error in entry '%s' in section '%ss'"
                                    % (entry, section.name))
                key_descr, step = [ s.strip() for s in ps ]

                key = Key(config.knox, key_descr, keysym=True,
                          origin="entry '%s' in section '%s'" % (entry, section.name))
                if step.startswith("run:"):
                    a = ShellCommandAction(config, section=None, cmd=step[4:])
                    triggers.add(
                        Trigger(trigger=key, mask=mask,
                               action=a))
                elif step.startswith("action:"):
                    action_name = "action:%s" % step[7:].strip()
                    triggers.add(
                        Trigger(trigger=key, mask=mask,
                               action=config.action(action_name)))
                elif step.startswith("waiter:"):
                    waiter_name = "waiter:%s" % step[7:].strip()
                    triggers.add(
                        Trigger(trigger=key, mask=mask,
                                waiter=config.waiter(waiter_name)))
                else:
                    raise Exception("WTF: %r" % step)
            return triggers


        @classmethod
        def action_list(cls, config, section, entry):
            actions = []
            for a in map(lambda s: s.strip(), section[entry].split(';')):
                if not a:
                    continue
                (type_name, name, args) = cls.section_reference(
                    config, section.name, entry, a)


        @classmethod
        def section_reference(cls, config, section_name, entry_name, ref, timeout=False):
            type_name = None
            name = None
            args = dict()
            if timeout:
                arg_re = (
                    r"(\s+with\s+(?P<args>(.(?!waiting))+))?"
                    r"(\s+waiting\s+(?P<timeout>\d+)s?\s*)?$")
            else:
                arg_re = (
                    r"(\s+with\s+(?P<args>.*)$")
            m = re.match(
                (r"^(\s*(?P<type_name>{name_chars}+)\s*:)?"
                 r"\s*(?P<name>{name_chars}+)"
                 + arg_re)
                .format(name_chars=Config.name_chars), ref)
            if not m:
                raise Exception(
                    "Syntax error in section options in %r in entry '%s', section '%s'"
                    % (ref, entry_name, section_name))

            type_name = m['type_name']
            section_name = m['name']
            # shlex.split stops (infinite loop?) when called with None
            if m['args'] is not None:
                for (i, p) in enumerate(shlex.split(m['args'])):
                    am = re.match(
                        r'^\s*(?P<name>{name_chars}+)(=(?P<value>.*))?$'
                        .format(name_chars=Config.name_chars), p)
                    if am:
                        args[am['name']] = am['value']
                    else:
                        raise Exception(
                            "Syntax error in section options in %r in entry '%s', section '%s'"
                            % (p, entry_name, section_name))
            if timeout:
                if m['timeout']:
                    timeout = int(m['timeout'])
                else:
                    timeout = None
                return (type_name, section_name, args, timeout)
            else:
                return (type_name, section_name, args)
