from keybender import config
from keybender.knox import KnoX
from keybender.listener import Listener
from keybender.event import Event, EventLoop
from keybender.rctl import SocketMgr, SocketSender
import sys
import os
import argparse
import socket

"""
argument parser
open named pipe for communication with external control

on startup a script can start urxvt and this inside of it, then send the request
to the pipe to find that PID's window, then ask for removing borders, removing it from
the taskbar, setting it to always under everything else, etc...

bindkeysequence -t urxvt

then run urxvt -e runme...

"""


# argumentparser,
# decide to start at start (1st level) or at a specific waiter or even an action
# or the same but at 2nd level (with opening tk root and exit on undefined key)



class Director:
    def process_args(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("-c", "--config", metavar="FILENAME",
                            help="Configuration file",
                            dest="config", default=None, required=True)
        parser.add_argument("-s", "--socket", metavar="SOCKET",
                            help="Socket path to listen on for commands.",
                            dest="socket_path", default=None)
        parser.add_argument("-o", "--options", metavar="[SECTION:]OPTION=VALUE",
                            help=
                            "Option name and value to set in the opt section"
                            " in the configuration file.",
                            action="append",
                            dest="options", default=[])
        self.options = parser.parse_args()
        if not self.options:
            parser.print_help()
            sys.exit(1)
        self.special_options = dict()
        broken = False
        for opt_str in self.options.options:
            parts = opt_str.split('=')
            if len(parts) != 2:
                print("Bad option: %r" % opt_str, file=sys.stderr)
                broken = True
                continue
            if parts[0] in self.special_options:
                print("Repeated option name in: %r" % opt_str, file=sys.stderr)
                broken = True
                continue
            self.special_options[parts[0]] = parts[1]
        if broken:
            sys.exit(2)

    def __init__(self):
        self.process_args()
        self.knox = KnoX()
        self.event_loop = EventLoop()
        self.cfg = config.Config(self.knox,
                                 self.options.config, self.event_loop,
                                 extra_options=self.special_options)
        self.cfg.start.execute()

        if self.options.socket_path:
            if os.path.exists(self.options.socket_path):
                os.unlink(self.options.socket_path)
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket.bind(self.options.socket_path)
            self.socket.listen(0)
            self.event_loop.register(
                Event.READABLE, self.remote_control_connection, fd=self.socket)
        else:
            self.socket = None

        self.event_loop.register(Event.IDLE, self.check_config, timeout=4)

        self.ls = Listener(self.knox, self.event_loop, self.cfg.start.triggers)

    def main(self):
        while True:
            self.ls.listen()

    def remote_control_connection(self, event, event_loop):
        (conn, _) = self.socket.accept()
        print("Somebody connected on #%r" % conn.fileno())
        conn = SocketMgr(conn)
        self.event_loop.register(
            Event.READABLE, self.remote_control_msg,
            fd=conn, consultant=config.Consultant(self.cfg))


    def remote_control_msg(self, event, event_loop):
        data = event.fd.recv(1024)
        if not data:
            print("CLOSING #%r" % event.fd.fileno(), "==" * 30)
            event.fd.close_rd()
            #event.fd.close()
            self.event_loop.unregister(event.key)
        else:
            r = event.consultant.incoming(data.decode().splitlines(),
                                          responder=SocketSender(event.fd, event_loop))
    def check_config(self, event, event_loop):
        try:
            if self.cfg.changed():
                if self.ls.level > 1:
                    print("Config file changed, but cannot reload...")
                else:
                    print("Config file changed, reloading...")
                new_cfg = self.cfg.reload()
                self.ls = Listener(self.knox, self.event_loop, new_cfg.start.triggers)
                self.cfg = new_cfg
                event_loop.quit()
        except Exception as e:
            print(e, file=sys.stderr)

Director().main()
