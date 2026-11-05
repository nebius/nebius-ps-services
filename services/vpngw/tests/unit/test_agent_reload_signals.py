import fcntl
import signal

from nebius_vpngw.agent import main as agent_main


def test_reload_signals_are_coalesced_without_reentering_routing_lock(tmp_path, monkeypatch):
    handlers = {}
    completed = []
    lock_path = tmp_path / "routing.lock"
    idle_signal_sent = False

    class FakeAgent:
        def reload(self):
            with lock_path.open("w") as lock:
                # A reentrant reload would block on a second descriptor in production.
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if len(completed) == 1:
                    handlers[signal.SIGHUP](signal.SIGHUP, None)
                    handlers[signal.SIGHUP](signal.SIGHUP, None)
                completed.append(True)

    def idle(*_args):
        nonlocal idle_signal_sent
        if len(completed) == 3:
            raise KeyboardInterrupt
        if not idle_signal_sent:
            idle_signal_sent = True
            handlers[signal.SIGHUP](signal.SIGHUP, None)

    monkeypatch.setattr(agent_main, "Agent", FakeAgent)
    monkeypatch.setattr(
        agent_main.signal, "signal", lambda sig, handler: handlers.update({sig: handler})
    )
    monkeypatch.setattr(agent_main.signal, "pause", idle)
    monkeypatch.setattr(agent_main.time, "sleep", idle)
    agent_main._run_agent()
    assert len(completed) == 3
