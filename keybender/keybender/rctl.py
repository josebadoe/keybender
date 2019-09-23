from keybender.event import Event
import queue
import socket

class SocketMgr:
    def __init__(self, sock):
        self.sock = sock
        self.open_for_reading = True
        self.open_for_writing = True
        self.sock.setblocking(False)

    def fileno(self):
        return self.sock.fileno()
    def send(self, data):
        assert self.open_for_writing, "Send on closed socket: %r" % data
        return self.sock.send(data)
    def recv(self, n):
        assert self.open_for_reading
        return self.sock.recv(n)

    def close_rd(self):
        if self.open_for_reading:
            self.sock.setblocking(True)
            self.sock.shutdown(socket.SHUT_RD)
            self.open_for_reading = False
        if not self.open_for_writing:
            self.sock.close()
        else:
            self.sock.setblocking(False)
    def close_wr(self):
        if self.open_for_writing:
            self.sock.setblocking(True)
            self.sock.shutdown(socket.SHUT_WR)
            self.open_for_writing = False
        if not self.open_for_reading:
            self.sock.close()
        else:
            self.sock.setblocking(False)


class SocketSender:
    def __init__(self, fd, event_loop, close_after_send=False):
        self.event_loop = event_loop
        self.queue = queue.Queue()
        self.i = None
        self.data = None
        self.writer_key = None
        self.fd = fd
        self.close_after_send = close_after_send

    def __call__(self, data):
        if isinstance(data, str) or isinstance(data, bytes):
            self.queue.put([data])
        else:
            self.queue.put(data)
        if self.writer_key is None:
            self.writer_key = self.event_loop.register(
                Event.WRITEABLE, self.socket_write, fd=self.fd)


    def next_blk(self):
        if self.data:
            if isinstance(self.data, str):
                return self.data.encode('utf-8')
            else:
                return self.data
        if self.i is not None:
            try:
                self.data = next(self.i)
                return self.next_blk()
            except StopIteration:
                self.i = None
        if self.i is None and not self.queue.empty():
            self.i = iter(self.queue.get_nowait())
            return self.next_blk()
        return None

    def socket_write(self, event, event_loop):
        data = self.next_blk()
        if event.fd is None:
            raise Exception
        if data:
            print("SENDING ON SOCKET #%r: %r" % (event.fd.fileno(), data))
            sent = event.fd.send(data)
            if not sent:
                print("Connection broken, still having data in queue: %r" % data)
            else:
                self.data = data[sent:]
        else:
            # h = event_loop.find_handler(event.key)
            # h.fd = None
            event_loop.unregister(self.writer_key)
            self.writer_key = None
            if self.close_after_send:
                event.fd.close_wr()


class StreamSender:
    def __init__(self, fd, event_loop, close_after_send=False):
        self.event_loop = event_loop
        self.queue = queue.Queue()
        self.i = None
        self.data = None
        self.writer_key = None
        self.fd = fd
        self.close_after_send = close_after_send

    def __call__(self, data):
        if isinstance(data, str) or isinstance(data, bytes):
            self.queue.put([data])
        else:
            self.queue.put(data)
        if self.writer_key is None:
            self.writer_key = self.event_loop.register(
                Event.WRITEABLE, self.stream_write, fd=self.fd)


    def next_blk(self):
        if self.data:
            if isinstance(self.data, str):
                return self.data.encode('utf-8')
            else:
                return self.data
        if self.i is not None:
            try:
                self.data = next(self.i)
                return self.next_blk()
            except StopIteration:
                self.i = None
        if self.i is None and not self.queue.empty():
            self.i = iter(self.queue.get_nowait())
            return self.next_blk()
        return None


    def stream_write(self, event, event_loop):
        data = self.next_blk()
        if event.fd is None:
            raise Exception
        if data:
            try:
                print("SENDING ON STREAM #%r: %r" % (event.fd.fileno(), data))
            except ValueError as e:
                event_loop.unregister(self.writer_key)
                self.writer_key = None
                print("%r" % e)
                return
            try:
                sent = event.fd.write(data)
                event.fd.flush()
            except BrokenPipeError:
                event_loop.unregister(self.writer_key)
                sent = 0
            if not sent:
                print("Connection broken, still having data in queue: %r" % data)
            else:
                self.data = data[sent:]
        else:
            # h = event_loop.find_handler(event.key)
            # h.fd = None
            event_loop.unregister(self.writer_key)
            self.writer_key = None
            if self.close_after_send:
                event.fd.close()
