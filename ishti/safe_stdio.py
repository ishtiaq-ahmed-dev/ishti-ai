"""print() must never take a request down.

Runtime log, 2026-09-30 08:44-08:46: three unrelated requests (chat, dispatcher, shopping) died within two minutes with
`OSError: [Errno 22] Invalid argument`. On Windows that is what a write to a stdout/stderr whose console or pipe went away
raises (the backend is started hidden by the launcher and outlives the console that started it). The request itself was
fine; only a log line could not be printed. `install()` wraps sys.stdout / sys.stderr so a dead stream drops the text
instead of raising; everything else about the stream (encoding, reconfigure(), isatty()...) passes straight through.
Installed when ishti.skills is first imported (every request path does), except under pytest."""
import errno
import sys

_DEAD = {errno.EINVAL, errno.EPIPE, errno.EBADF, getattr(errno, "ENOTCONN", -1)}


class SafeStream:
    def __init__(self, stream):
        self._s = stream

    def write(self, text):
        try:
            return self._s.write(text)
        except (OSError, ValueError) as exc:  # dead pipe / closed console / "I/O operation on closed file"
            if isinstance(exc, OSError) and exc.errno not in _DEAD and exc.errno is not None:
                raise
            return len(text) if isinstance(text, str) else 0

    def flush(self):
        try:
            return self._s.flush()
        except (OSError, ValueError) as exc:
            if isinstance(exc, OSError) and exc.errno not in _DEAD and exc.errno is not None:
                raise

    def __getattr__(self, name):  # encoding, reconfigure, isatty, fileno ... are the real stream's
        return getattr(self._s, name)


def install(stdout=None, stderr=None):
    """Wrap the process's std streams once (idempotent). Pass streams only to test with fakes."""
    for attr, given in (("stdout", stdout), ("stderr", stderr)):
        s = given if given is not None else getattr(sys, attr, None)
        if s is not None and not isinstance(s, SafeStream):
            setattr(sys, attr, SafeStream(s))


if __name__ == "__main__":
    class Dead:
        def write(self, t):
            raise OSError(errno.EINVAL, "Invalid argument")

        def flush(self):
            raise OSError(errno.EINVAL, "Invalid argument")
        encoding = "utf-8"
    old = sys.stdout
    try:
        install(stdout=Dead())
        print("this line has nowhere to go")
        sys.stdout.flush()
        assert sys.stdout.encoding == "utf-8"
    finally:
        sys.stdout = old
    print("[self-check] safe_stdio OK - a dead stdout drops text instead of raising")
