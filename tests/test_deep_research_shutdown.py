"""Runtime-log bug: "web_search: RuntimeError: cannot schedule new futures after interpreter shutdown".
A pool that refuses new work (the interpreter is exiting) must degrade to inline execution, not fail the request."""
import concurrent.futures

from orchestrator import deep_research as dr


class DeadPool:
    def submit(self, *a, **k):
        raise RuntimeError("cannot schedule new futures after interpreter shutdown")


def test_submit_runs_inline_when_the_pool_refuses_work():
    fut = dr._submit(DeadPool(), lambda x, y=0: x + y, 2, y=3)
    assert fut.result() == 5


def test_submit_keeps_the_callers_exception():
    def boom():
        raise ValueError("bad page")
    fut = dr._submit(DeadPool(), boom)
    try:
        fut.result()
        assert False, "the exception must reach the caller"
    except ValueError as exc:
        assert "bad page" in str(exc)


def test_submit_uses_the_pool_normally():
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        assert dr._submit(pool, pow, 2, 5).result() == 32
