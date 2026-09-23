import _thread
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from peerhub.cli import main
from peerhub.dispatch.process import ProcessSupervisor


def test_cli_ask_invokes_cancellation_ladder_on_keyboard_interrupt():
    """Prove that Ctrl-C triggers begin_cancellation() on the live ProcessSupervisor."""
    mock_supervisor = MagicMock(spec=ProcessSupervisor)
    
    dispatch_started = threading.Event()
    dispatch_cancelled = threading.Event()
    
    def fake_begin_cancellation(*args, **kwargs):
        dispatch_cancelled.set()
        
    mock_supervisor.begin_cancellation.side_effect = fake_begin_cancellation
    
    def fake_execute_direct_ask(
        request,
        *,
        clock,
        ids,
        authenticated_subject,
        cancellation_hook=None,
    ):
        if cancellation_hook is not None:
            cancellation_hook(mock_supervisor)
            
        dispatch_started.set()
        
        # Wait up to 5 seconds to be cancelled
        dispatch_cancelled.wait(5.0)
        
        # Simulate returning a fake result
        from peerhub.application.direct_ask import DirectAskResult
        from peerhub.dispatch.contract import RequestState
        return DirectAskResult(
            command_id="cmd-1",
            attempt_id="att-1",
            peer_kind="ag",
            profile_id="ag.standard",
            response_text=None,
            request_state=RequestState.SUCCEEDED_VERIFIED,
            error_code=None,
            execution_certainty=None,
        )

    with patch("peerhub.cli.execute_direct_ask", side_effect=fake_execute_direct_ask):
        
        def trigger_interrupt():
            # Wait for the background thread to actually start and hook the supervisor
            dispatch_started.wait(timeout=5.0)
            # Give the main thread a tiny moment to enter its wait loop
            time.sleep(0.1)
            # Raise KeyboardInterrupt in the main thread (where main() is running)
            _thread.interrupt_main()
            
        trigger_thread = threading.Thread(target=trigger_interrupt, daemon=True)
        trigger_thread.start()
        
        # Run main on the main thread
        exit_code = main(["ask", "ag", "hello", "--capability-tier", "WORKTREE_WRITE"])
        
        # Verify it exited with 130
        assert exit_code == 130
        
        # Verify the supervisor got cancelled
        mock_supervisor.begin_cancellation.assert_called_once()
        assert dispatch_cancelled.is_set()
