"""Keep retired Tk test interpreters on the main thread during cleanup."""
import gc


def pytest_runtest_setup():
    # Closed test windows can leave reference cycles. Collect them before the
    # next test starts workers: Tcl must be finalized on its creating thread.
    gc.collect()
