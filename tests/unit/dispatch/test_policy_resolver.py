"""TDD cases for PolicyResolver based on GOVERNANCE-DISPATCH-R4 MECE Spec."""
import textwrap
from pathlib import Path
import pytest

# These imports will fail (TDD RED state) because the module is not implemented yet.
from peerhub.dispatch.policy import ConsultationDepth
from peerhub.dispatch.policy_resolver import PolicyResolver, ConfigurationError

@pytest.fixture
def mock_config_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Provides fake paths for global and workspace configs."""
    workspace_dir = tmp_path / "workspace"
    global_dir = tmp_path / "global"
    workspace_dir.mkdir()
    global_dir.mkdir()
    
    workspace_conf = workspace_dir / ".peerhub" / "dispatch-policy.toml"
    global_conf = global_dir / ".peerhub" / "dispatch-policy.toml"
    
    workspace_conf.parent.mkdir(parents=True, exist_ok=True)
    global_conf.parent.mkdir(parents=True, exist_ok=True)
    
    # We assume PolicyResolver accepts paths or we monkeypatch the defaults
    return {"workspace": workspace_conf, "global": global_conf}

def test_r01_no_config_files(mock_config_paths):
    """R-01: No config files at any layer -> RESOLVED with all built-in defaults."""
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    policy = resolver.resolve(action_name="task.create")
    assert policy.consultation_depth == ConsultationDepth.QUORUM
    assert policy.consensus.timeout_seconds == 1800
    assert policy.session.default_mode == "auto"

def test_r02_global_exists_workspace_absent(mock_config_paths):
    """R-02: Global exists, workspace absent -> RESOLVED with global overrides."""
    mock_config_paths["global"].write_text('[consensus]\ntimeout_seconds = 500\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    policy = resolver.resolve(action_name="task.create")
    assert policy.consensus.timeout_seconds == 500

def test_r03_workspace_exists_global_absent(mock_config_paths):
    """R-03: Workspace exists, global absent -> RESOLVED with workspace overrides."""
    mock_config_paths["workspace"].write_text('[consensus]\ntimeout_seconds = 600\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    policy = resolver.resolve(action_name="task.create")
    assert policy.consensus.timeout_seconds == 600

def test_r04_both_exist_workspace_overrides_global(mock_config_paths):
    """R-04: Both exist, workspace overrides global -> RESOLVED with workspace winning."""
    mock_config_paths["global"].write_text('[consensus]\ntimeout_seconds = 500\n[session]\nsoft_pressure_threshold = 40')
    mock_config_paths["workspace"].write_text('[consensus]\ntimeout_seconds = 600\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    policy = resolver.resolve(action_name="task.create")
    assert policy.consensus.timeout_seconds == 600
    assert policy.session.soft_pressure_threshold == 40  # Inherited from global

def test_r05_cli_override_trumps_both_files(mock_config_paths):
    """R-05: CLI override trumps both files -> RESOLVED with CLI winning."""
    mock_config_paths["global"].write_text('[consensus]\ntimeout_seconds = 500\n')
    mock_config_paths["workspace"].write_text('[consensus]\ntimeout_seconds = 600\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    policy = resolver.resolve(action_name="task.create", cli_overrides={"consensus": {"timeout_seconds": 999}})
    assert policy.consensus.timeout_seconds == 999

def test_r06_config_file_has_unknown_keys(mock_config_paths):
    """R-06: Config file has unknown keys -> ERROR: ConfigurationError."""
    mock_config_paths["workspace"].write_text('[consensus]\nfake_key = 123\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="fake_key"):
        resolver.resolve(action_name="task.create")

def test_r07_config_file_has_wrong_type(mock_config_paths):
    """R-07: Config file has wrong type for known key -> ERROR: type mismatch."""
    mock_config_paths["workspace"].write_text('[consensus]\ntimeout_seconds = "abc"\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="timeout_seconds"):
        resolver.resolve(action_name="task.create")

def test_r08_consultation_overrides_invalid_depth(mock_config_paths):
    """R-08: consultation.overrides maps to invalid depth -> ERROR: invalid enum value."""
    mock_config_paths["workspace"].write_text('[consultation.overrides]\n"task.create" = "invalid_depth"\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="invalid_depth"):
        resolver.resolve(action_name="task.create")

def test_r09_session_thresholds_inverted(mock_config_paths):
    """R-09: session.soft_pressure_threshold = 90, hard = 75 (inverted) -> ERROR."""
    mock_config_paths["workspace"].write_text('[session]\nsoft_pressure_threshold = 90\nhard_pressure_threshold = 75\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="soft must be < hard"):
        resolver.resolve(action_name="task.create")

def test_r10_session_thresholds_equal(mock_config_paths):
    """R-10: session.soft_pressure_threshold = 75, hard = 75 (equal) -> ERROR."""
    mock_config_paths["workspace"].write_text('[session]\nsoft_pressure_threshold = 75\nhard_pressure_threshold = 75\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="soft must be strictly < hard"):
        resolver.resolve(action_name="task.create")

def test_r11_transport_staging_dir_traversal(mock_config_paths):
    """R-11: transport.staging_dir = "../../etc" -> ERROR: Path traversal rejected."""
    mock_config_paths["workspace"].write_text('[transport]\nstaging_dir = "../../etc"\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="Path traversal"):
        resolver.resolve(action_name="task.create")

def test_r12_transport_staging_dir_absolute(mock_config_paths):
    """R-12: transport.staging_dir = "/absolute/path" -> ERROR: Absolute paths rejected."""
    mock_config_paths["workspace"].write_text('[transport]\nstaging_dir = "/absolute/path"\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="Absolute path"):
        resolver.resolve(action_name="task.create")

def test_r13_transport_max_inline_bytes_zero(mock_config_paths):
    """R-13: transport.max_inline_bytes = 0 -> ERROR: must be > 0."""
    mock_config_paths["workspace"].write_text('[transport]\nmax_inline_bytes = 0\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="must be > 0"):
        resolver.resolve(action_name="task.create")

def test_r14_transport_max_inline_bytes_negative(mock_config_paths):
    """R-14: transport.max_inline_bytes = -1 -> ERROR: must be > 0."""
    mock_config_paths["workspace"].write_text('[transport]\nmax_inline_bytes = -1\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="must be > 0"):
        resolver.resolve(action_name="task.create")

def test_r15_consensus_quorum_formula_invalid(mock_config_paths):
    """R-15: consensus.quorum_formula = "unknown_formula" -> ERROR: invalid formula."""
    mock_config_paths["workspace"].write_text('[consensus]\nquorum_formula = "unknown_formula"\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="invalid formula"):
        resolver.resolve(action_name="task.create")

def test_r16_consensus_final_call_rule_invalid(mock_config_paths):
    """R-16: consensus.final_call_rule = "invalid" -> ERROR: invalid rule."""
    mock_config_paths["workspace"].write_text('[consensus]\nfinal_call_rule = "invalid"\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="invalid rule"):
        resolver.resolve(action_name="task.create")

def test_r17_per_command_minimum_overrides_user_config(mock_config_paths):
    """R-17: Per-command minimum overrides user config -> RESOLVED: user's lower value silently raised to minimum."""
    # Action "consensus.propose" has minimum depth of QUORUM. User attempts to set to NONE.
    mock_config_paths["workspace"].write_text('[consultation.overrides]\n"consensus.propose" = "none"\n')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    
    # We pass the per_command_minimums so PolicyResolver can enforce it
    policy = resolver.resolve(
        action_name="consensus.propose",
        per_command_minimums={"consensus.propose": ConsultationDepth.QUORUM}
    )
    # The depth should be silently raised to QUORUM despite user's override to NONE
    assert policy.consultation_depth == ConsultationDepth.QUORUM

def test_r18_config_file_empty(mock_config_paths):
    """R-18: Config file is valid TOML but empty {} -> RESOLVED with all defaults."""
    mock_config_paths["workspace"].write_text('')
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    policy = resolver.resolve(action_name="task.create")
    assert policy.consensus.timeout_seconds == 1800  # assuming 1800 is default from toml

def test_r19_config_file_encoded_utf16(mock_config_paths):
    """R-19: Config file encoded in UTF-16 -> ERROR: TOML spec requires UTF-8."""
    mock_config_paths["workspace"].write_bytes('[consensus]\ntimeout_seconds = 500\n'.encode('utf-16'))
    
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    with pytest.raises(ConfigurationError, match="UTF-8"):
        resolver.resolve(action_name="task.create")

def test_r20_concurrent_resolution_calls(mock_config_paths):
    """R-20: Concurrent resolution calls -> Each independently resolves."""
    resolver = PolicyResolver(
        workspace_config=mock_config_paths["workspace"],
        global_config=mock_config_paths["global"]
    )
    
    # Simulate concurrent calls resolving differently based on different CLI overrides
    policy1 = resolver.resolve(action_name="task.create", cli_overrides={"consensus": {"timeout_seconds": 100}})
    policy2 = resolver.resolve(action_name="task.create", cli_overrides={"consensus": {"timeout_seconds": 200}})
    
    assert policy1.consensus.timeout_seconds == 100
    assert policy2.consensus.timeout_seconds == 200
