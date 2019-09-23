import math, functools
import itertools
from collections import namedtuple
from collections.abc import Iterable
import select
from types import GeneratorType

class Event:
    READABLE = 'readable'
    WRITEABLE = 'writeable'
    IDLE = 'idle'

    def __init__(self, name, **kwargs):
        self.name = name
        for (k, v) in kwargs.items():
            setattr(self, k, v)
        self._keys = set(kwargs.keys())

    def handles(self, e):
        return callable(getattr(self, 'handler', None)) and self == e

    def __eq__(a, b):
        """compare two events, but only the common subset if its attributes"""
        if not isinstance(b, Event) or a.name != b.name:
            return False
        for k in set(a._keys) & set(b._keys):
            if getattr(a, k) != getattr(b, k):
                return False
        return True

    def __contains__(self, name):
        return name in self._data

    def copy(self):
        data = dict()
        for k in self._keys:
            data[k] = getattr(self, k)
        return Event(self.name, **data)

    def update(self, b):
        for k in b._keys:
            setattr(self, k, getattr(b, k))
        return self



class EventLoop:
    def __init__(self):
        self.key = 1
        self.registry = dict()

    def register(self, event_name, handler=None, **data):
        h = Event(event_name, handler=handler, key=self.key, **data)
        k = h.key
        self.registry[k] = h
        self.key += 1
        return k

    def find_handler(self, k, **data):
        if k in self.registry:
            return self.registry[k]
        elif data:
            name = k
            event = Event(name, **data)
            for (k, eh) in list(self.registry.items()):
                if eh == data:
                    return self.registry[k]
        return None

    def unregister(self, k, **data):
        eh = self.find_handler(k, **data)
        if eh is not None:
            del self.registry[eh.key]

    def __iter__(self):
        self.buffered_results = []
        return self

    def quit(self):
        self._quit = True

    def handle(self, name, **data):
        event = Event(name, **data)
        for event_handler in self.registry.values():
            if event_handler.handles(event):
                eh = event.copy().update(event_handler)
                return (True, eh.handler(eh, self))
        return (False, None)

    def process(self):
        """Call this in a for loop for values returned from handlers.
        """
        self._quit = False
        while not self._quit:
            idle_handlers = list()
            readers = list()
            writers = list()
            for eh in self.registry.values():
                if eh.name == eh.IDLE:
                    idle_handlers.append(eh)
                elif eh.name == eh.READABLE:
                    readers.append(eh)
                elif eh.name == eh.WRITEABLE:
                    writers.append(eh)

            timeout = functools.reduce(
                math.gcd,
                filter(None, map(lambda eh: eh.timeout, idle_handlers)), 0)
            rl = list(filter(lambda fd: fd.fileno() > 0, map(lambda eh: eh.fd, readers)))
            wl = list(filter(lambda fd: fd.fileno() > 0, map(lambda eh: eh.fd, writers)))

            (r_rl, r_wl, _) = select.select(rl, wl, [], timeout or None)
            handled = False
            for fd in r_rl:
                (h, r) = self.handle(Event.READABLE, fd=fd)
                if isinstance(r, GeneratorType):
                    yield from r
                elif r is not None:
                    yield r
                handled = handled or h
            for fd in r_wl:
                (h, r) = self.handle(Event.WRITEABLE, fd=fd)
                if isinstance(r, GeneratorType):
                    yield from r
                elif r is not None:
                    yield r
                handled = handled or h
            if not handled:
                (h, r) = self.handle(Event.IDLE)
                if isinstance(r, GeneratorType):
                    yield from r
                elif r is not None:
                    yield r
