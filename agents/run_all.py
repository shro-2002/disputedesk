"""Run @evidence, @counsel and @submitter in one process (`make agents`). Ctrl-C stops all three."""

import logging
import signal
import threading

from agents import counsel_agent, evidence, submitter

log = logging.getLogger("agents")


def main() -> None:
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    threads = [threading.Thread(target=m.main, args=(stop,), name=m.__name__, daemon=True)
               for m in (evidence, counsel_agent, submitter)]
    for thread in threads:
        thread.start()
    log.info("DisputeDesk agents running: @evidence (webhook :8098), @counsel, @submitter")
    stop.wait()


if __name__ == "__main__":
    main()
