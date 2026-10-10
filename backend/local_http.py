"""Bound loopback handlers; no model routing or provider configuration."""
import threading
from http.server import ThreadingHTTPServer


class LimitedServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args)
    def process_request(self, connection, address):
        if not self.slots.acquire(False):
            try: connection.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally: self.shutdown_request(connection)
            return
        try: super().process_request(connection, address)
        except Exception:
            self.slots.release(); raise
    def process_request_thread(self, *args):
        try: super().process_request_thread(*args)
        finally: self.slots.release()
