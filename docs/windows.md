# Windows PowerShell Operations Guide

Guidance, tested patterns, and operational recipes for running PeerHub under Windows PowerShell (Windows PowerShell 5.1 and PowerShell 7+).

## Parallel Asks

When executing multiple tasks concurrently, **each parallel job must use a distinct `--stream` and distinct `--request-id`**.

> [!IMPORTANT]
> A stream maintains single-caller claim exclusivity. Running multiple concurrent asks on the same stream results in `ask_claim_held` contention. Always assign dedicated streams (e.g. `par-mod-a`, `par-mod-b`) for parallel tasks.

### Using `Start-Job` (Windows PowerShell 5.1 & PowerShell 7+)

`Start-Job` creates isolated background processes. Use this when running on Windows PowerShell 5.1 or when complete process isolation is desired:

```powershell
# Launch parallel jobs with distinct streams and request IDs
$jobA = Start-Job -ScriptBlock {
    peerhub ask cc "Review module A" --stream job-mod-a --request-id req-a-001 --json
}

$jobB = Start-Job -ScriptBlock {
    peerhub ask cx "Review module B" --stream job-mod-b --request-id req-b-001 --json
}

# Wait for completion and receive JSON results
$results = Wait-Job $jobA, $jobB | Receive-Job
```

### Using `ForEach-Object -Parallel` (PowerShell 7+)

PowerShell 7 provides high-performance thread-based parallelism via `ForEach-Object -Parallel`. Note the use of `$using:` to reference variables from the caller scope:

```powershell
$tasks = @(
    @{ Peer = "cc"; Stream = "par-task-1"; ReqId = "req-p1-001"; Prompt = "Analyze schema" },
    @{ Peer = "cx"; Stream = "par-task-2"; ReqId = "req-p2-001"; Prompt = "Audit security" }
)

$dbPath = "C:/PeerHub/core.db"

$tasks | ForEach-Object -Parallel {
    $task = $_
    $db = $using:dbPath
    peerhub --db $db ask $task.Peer $task.Prompt --stream $task.Stream --request-id $task.ReqId --json
} -ThrottleLimit 4
```

Example commands executed by parallel workers:
```powershell
peerhub ask cc "Analyze schema" --stream par-task-1 --request-id req-p1-001 --json
peerhub ask cx "Audit security" --stream par-task-2 --request-id req-p2-001 --json
```

## Quoting Rules & Complex Prompts

PowerShell parses quotes and special characters before passing arguments to native CLI programs.

### Literal Single Quoting

To pass strings containing double quotes or special characters without PowerShell expanding them, wrap the argument in single quotes:

```powershell
peerhub ask cc 'Verify that JSON contains {"status": "ok"} and no error field' --stream quote-test --request-id req-q-001
```

### Transporting Prompts via `--query-file`

For long, multiline, or heavily-quoted prompts (such as code reviews, markdown excerpts, or SQL queries), avoid shell escaping altogether by staging the prompt into a UTF-8 file and passing `--query-file`:

```powershell
# Write UTF-8 prompt to file
Set-Content -Path "query.txt" -Value "Review this function: def foo(bar): return bar * 2" -Encoding utf8

# Pass prompt file directly to peerhub ask
peerhub ask cc --query-file query.txt --stream review-session --request-id req-qf-001
```

## Non-ASCII and Unicode Paths

When running PeerHub in workspaces or database locations containing non-ASCII characters (e.g. Korean, Chinese, Japanese, or accented European characters):

1. **Quote paths**: Always enclose file and database paths in quotes:
   ```powershell
   peerhub --db "D:/작업공간/core.db" ask cx "Summarize workspace" -w "D:/작업공간/프로젝트" --stream unicode-stream --request-id req-uni-001
   ```

2. **Configure Console UTF-8 Encoding**: If output or error messages display replacement characters (`?`), ensure your PowerShell session is configured for UTF-8 input/output:
   ```powershell
   [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
   $OutputEncoding = [System.Text.UTF8Encoding]::new()
   ```

3. **Verify Diagnostics**:
   ```powershell
   peerhub diag health
   ```

## Background Services & Unverified Recipes

> [!WARNING]
> **UNVERIFIED:** Registering `peerhub monitor` as a Windows Scheduled Task or Windows Service (e.g. via NSSM) has not been verified end-to-end. On Windows, running `peerhub monitor` in a dedicated terminal window is the recommended and verified approach.

> [!WARNING]
> **UNVERIFIED:** Running PeerHub over PowerShell Remoting (WinRM `Enter-PSSession` or `Invoke-Command`) with Kerberos hop delegation is unverified; use local execution or key-based OpenSSH as documented in [docs/remote-ask.md](remote-ask.md).
