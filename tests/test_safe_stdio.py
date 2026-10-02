"""Runtime-log bug: OSError [Errno 22] killing unrelated requests (2026-09-30 08:44-08:46) - a print() to a dead stdout."""
import errno
import sys

import pytest

from ishti import safe_stdio


class DeadStream:
    encoding = "utf-8"

    def write(self, text):
        raise OSError(errno.EINVAL, "Invalid argument")

    def flush(self):
        raise OSError(errno.EINVAL, "Invalid argument")


@pytest.fixture
def restore():
    out, err = sys.stdout, sys.stderr
    yield
    sys.stdout, sys.stderr = out, err


def test_print_to_a_dead_stdout_does_not_raise(restore):
    safe_stdio.install(stdout=DeadStream(), stderr=DeadStream())
    print("nowhere to go")
    print("nowhere either", file=sys.stderr)
    sys.stdout.flush()
    assert sys.stdout.encoding == "utf-8"            # the real stream's attributes still pass through


def test_other_errors_are_not_swallowed(restore):
    class Full(DeadStream):
        def write(self, text):
            raise OSError(errno.ENOSPC, "No space left on device")   # a real fault: must stay visible
    safe_stdio.install(stdout=Full())
    with pytest.raises(OSError):
        print("x")


def test_install_is_idempotent_and_writes_through_when_alive(restore):
    import io
    buf = io.StringIO()
    safe_stdio.install(stdout=buf)
    safe_stdio.install()                                # second call must not double-wrap
    assert isinstance(sys.stdout, safe_stdio.SafeStream) and not isinstance(sys.stdout._s, safe_stdio.SafeStream)
    print("hello")
    assert buf.getvalue() == "hello\n"
