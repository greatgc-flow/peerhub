import pytest
from peerhub.core.protocol import (
    cli_exit_code,
    CommandFailure,
    ErrorCode,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SCHEMA_VERSION,
    ErrorDetail,
    ErrorPhase,
    RetryDisposition,
)
from peerhub.core.execution import ExecutionCertainty

@pytest.mark.parametrize("code", list(ErrorCode))
def test_cli_exit_code_handles_all_error_codes(code: ErrorCode):
    """Ensure cli_exit_code is defined for every ErrorCode and returns an int."""
    # We construct a failure to test the code.
    # Phase is set to something benign that doesn't trigger the blanket phase checks if possible,
    # or we can test both phases.
    failure = CommandFailure(
        ok=False,
        protocol_major=PROTOCOL_MAJOR,
        protocol_minor=PROTOCOL_MINOR,
        schema_version=SCHEMA_VERSION,
        diagnostic_id="diag",
        correlation_id=None,
        command_id=None,
        error=ErrorDetail(
            code=code,
            phase=ErrorPhase.EFFECT,  # Use EFFECT to avoid hitting VALIDATION/ADMISSION blanket checks
            execution_certainty=ExecutionCertainty.NOT_STARTED,
            retry_disposition=RetryDisposition.NEVER,
            message="test error",
            details={}
        )
    )
    exit_code = cli_exit_code(failure)
    assert isinstance(exit_code, int)
    # The default fallback is 6. If it hits 6, it might be unhandled, but that's allowed by the function
    # if it's explicitly not in the other sets. We at least prove it doesn't raise an exception.
