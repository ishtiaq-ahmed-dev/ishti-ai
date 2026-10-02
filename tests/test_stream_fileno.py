"""Under pythonw ISHTI's stdout/stderr are log streams: they must still give child processes a real descriptor
(Playwright's driver died with `UnsupportedOperation: fileno` and 'open Chrome profile 9' failed)."""
import logging
import subprocess
import sys

from ishti import lifecycle


def test_log_stream_has_a_descriptor_children_can_use():
    st = lifecycle._Stream(logging.ERROR, None)
    assert st.fileno() > 2
    r = subprocess.run([sys.executable, "-c", "import sys; print('ok'); sys.stderr.write('x')"], stdout=subprocess.PIPE, stderr=st)
    assert r.returncode == 0 and r.stdout.strip() == b"ok"
