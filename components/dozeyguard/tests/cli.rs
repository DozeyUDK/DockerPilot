use std::io::Write;
use std::process::{Command, Stdio};

use tempfile::TempDir;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_dozeyguard")
}

fn fixture(name: &str) -> String {
    format!("{}/tests/fixtures/{name}", env!("CARGO_MANIFEST_DIR"))
}

#[test]
fn secure_compose_exits_zero() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("secure-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--fail-on",
            "low",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(0));
}

#[test]
fn insecure_compose_exits_two_and_reports_expected_rules() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("insecure-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(2));
    let stdout = String::from_utf8(output.stdout).unwrap();
    for rule in [
        "DG001", "DG002", "DG003", "DG004", "DG007", "DG009", "DG021",
    ] {
        assert!(stdout.contains(rule), "missing {rule} in {stdout}");
    }
    assert!(stdout.contains("\"contract_version\": 1"));
    assert!(stdout.contains("\"result_sha256\""));
    assert!(!stdout.contains("SECRET_DO_NOT_PRINT_123"));
}

#[test]
fn active_exception_is_reported_but_does_not_block() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("exception-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("exceptions.toml"),
            "--output",
            "json",
            "--fail-on",
            "critical",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(0));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("\"status\": \"active\""));
}

#[test]
fn expired_exception_still_blocks() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("expired-exception-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("exceptions.toml"),
            "--output",
            "json",
            "--fail-on",
            "critical",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(2));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("\"status\": \"expired\""));
}

#[test]
fn multi_service_order_is_deterministic() {
    let first = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("multi-service.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
        ])
        .output()
        .unwrap();
    let second = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("multi-service.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
        ])
        .output()
        .unwrap();

    assert_eq!(first.stdout, second.stdout);
    assert_eq!(first.status.code(), second.status.code());
}

#[test]
fn malformed_input_exits_one_without_input_fragment() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("malformed-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(stdout.contains("\"status\": \"error\""));
    assert!(stdout.contains("parse_error") || stdout.contains("failed to parse Compose JSON"));
    assert!(!stdout.contains("SECRET_DO_NOT_PRINT_123"));
    assert!(!stderr.contains("SECRET_DO_NOT_PRINT_123"));
}

#[test]
fn sentinel_secret_never_appears_in_stdout_or_stderr() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("secret-redaction-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "markdown",
        ])
        .output()
        .unwrap();

    let stdout = String::from_utf8(output.stdout).unwrap();
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(!stdout.contains("SECRET_DO_NOT_PRINT_123"));
    assert!(!stderr.contains("SECRET_DO_NOT_PRINT_123"));
}

#[test]
fn stdin_input_is_not_written_to_disk() {
    let temp = TempDir::new().unwrap();
    let before = std::fs::read_dir(temp.path()).unwrap().count();

    let mut child = Command::new(bin())
        .current_dir(temp.path())
        .args([
            "scan",
            "--input",
            "-",
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();

    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(
            std::fs::read(fixture("secure-compose.json"))
                .unwrap()
                .as_slice(),
        )
        .unwrap();
    let output = child.wait_with_output().unwrap();
    let after = std::fs::read_dir(temp.path()).unwrap().count();

    assert_eq!(before, after);
    assert!(
        output.status.success()
            || output.status.code() == Some(0)
            || output.status.code() == Some(2)
    );
}

#[test]
fn oversize_input_fail_closed_json() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("big.json");
    let mut payload = br#"{"services":{"a":{"image":"x"}}}"#.to_vec();
    payload.resize(64, b' ');
    std::fs::write(&path, &payload).unwrap();

    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            path.to_str().unwrap(),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--max-input-bytes",
            "16",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("input_too_large") || stdout.contains("exceeds"));
}

#[test]
fn hard_max_cannot_be_exceeded_via_cli() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("secure-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--max-input-bytes",
            "20000000",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("hard maximum") || stdout.contains("invalid_limit"));
}

#[test]
fn dg026_cap_add_all_fixture_blocks() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("cap-add-all-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(2));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("DG026"));
    assert!(stdout.contains("\"blocking\": true"));
}

#[test]
fn json_contract_contains_hashes_and_stable_keys() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("secure-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--fail-on",
            "low",
        ])
        .output()
        .unwrap();

    let stdout = String::from_utf8(output.stdout).unwrap();
    let value: serde_json::Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(value["contract_version"], 1);
    assert!(value["input"]["sha256"].as_str().unwrap().len() == 64);
    assert!(value["policy"]["sha256"].as_str().unwrap().len() == 64);
    assert!(value["result"]["result_sha256"].as_str().unwrap().len() == 64);
    assert!(value["scanner"]["name"] == "dozeyguard");
}

fn minimal_compose_payload(pad_to: usize) -> Vec<u8> {
    let mut payload = br#"{"services":{"web":{"image":"nginx:alpine"}}}"#.to_vec();
    if payload.len() < pad_to {
        payload.resize(pad_to, b' ');
    }
    payload
}

#[test]
fn file_exact_limit_accepted() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("exact.json");
    let payload = minimal_compose_payload(64);
    assert_eq!(payload.len(), 64);
    std::fs::write(&path, &payload).unwrap();

    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            path.to_str().unwrap(),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--max-input-bytes",
            "64",
            "--fail-on",
            "critical",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(0));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("\"contract_version\": 1"));
    assert!(!stdout.contains("input_too_large"));
}

#[test]
fn file_limit_plus_one_rejected() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("plusone.json");
    let payload = minimal_compose_payload(65);
    std::fs::write(&path, &payload).unwrap();

    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            path.to_str().unwrap(),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--max-input-bytes",
            "64",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("input_too_large"));
    assert!(stdout.contains("exceeds") || stdout.contains("65 > 64"));
}

#[test]
fn large_file_rejected_without_full_load() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("huge.json");
    // Several MiB — unbounded fs::read would allocate all of it before the limit check.
    let mut payload = minimal_compose_payload(48);
    payload.resize(4 * 1024 * 1024, b' ');
    std::fs::write(&path, &payload).unwrap();

    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            path.to_str().unwrap(),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--max-input-bytes",
            "1024",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("input_too_large"));
}

#[test]
fn stdin_exact_limit_accepted() {
    let payload = minimal_compose_payload(64);
    let mut child = Command::new(bin())
        .args([
            "scan",
            "--input",
            "-",
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--max-input-bytes",
            "64",
            "--fail-on",
            "critical",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.as_mut().unwrap().write_all(&payload).unwrap();
    let output = child.wait_with_output().unwrap();
    assert_eq!(output.status.code(), Some(0));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(!stdout.contains("input_too_large"));
}

#[test]
fn stdin_limit_plus_one_rejected() {
    let payload = minimal_compose_payload(65);
    let mut child = Command::new(bin())
        .args([
            "scan",
            "--input",
            "-",
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--max-input-bytes",
            "64",
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.as_mut().unwrap().write_all(&payload).unwrap();
    let output = child.wait_with_output().unwrap();
    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("input_too_large"));
}

#[test]
fn zero_max_input_bytes_rejected() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("secure-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--max-input-bytes",
            "0",
        ])
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("invalid_limit") || stdout.contains("greater than zero"));
}

#[test]
fn secure_fixture_json_sha_stable_under_bounded_input() {
    let output = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("secure-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--fail-on",
            "low",
        ])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(0));
    let first: serde_json::Value =
        serde_json::from_str(&String::from_utf8(output.stdout).unwrap()).unwrap();

    let output2 = Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("secure-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            &fixture("policy.toml"),
            "--output",
            "json",
            "--fail-on",
            "low",
        ])
        .output()
        .unwrap();
    let second: serde_json::Value =
        serde_json::from_str(&String::from_utf8(output2.stdout).unwrap()).unwrap();
    assert_eq!(
        first["result"]["result_sha256"],
        second["result"]["result_sha256"]
    );
}

const POLICY_SENTINEL: &str = "POLICY_SECRET_DO_NOT_PRINT_987";

fn scan_json_with_policy(policy: &std::path::Path) -> std::process::Output {
    Command::new(bin())
        .args([
            "scan",
            "--input",
            &fixture("secure-compose.json"),
            "--input-format",
            "compose-json",
            "--policy",
            policy.to_str().unwrap(),
            "--output",
            "json",
        ])
        .output()
        .unwrap()
}

#[test]
fn missing_policy_is_policy_error_without_path_contents() {
    let output = scan_json_with_policy(std::path::Path::new(
        "/nonexistent/dozeyguard-missing-policy.toml",
    ));
    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    let value: serde_json::Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(value["contract_version"], 1);
    assert_eq!(value["result"]["status"], "error");
    assert_eq!(value["result"]["error"]["code"], "policy_error");
    assert_eq!(value["policy"]["sha256"], "");
    let message = value["result"]["error"]["message"].as_str().unwrap();
    assert!(message.contains("failed to read policy file"));
    assert!(!message.contains("input exceeds"));
}

#[test]
fn invalid_utf8_policy_is_policy_error_without_content_leak() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("invalid-utf8.toml");
    let mut bytes = POLICY_SENTINEL.as_bytes().to_vec();
    bytes.extend_from_slice(&[0xff, 0xfe, 0xfd]);
    std::fs::write(&path, &bytes).unwrap();

    let output = scan_json_with_policy(&path);
    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    let stderr = String::from_utf8(output.stderr).unwrap();
    let value: serde_json::Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(value["contract_version"], 1);
    assert_eq!(value["result"]["error"]["code"], "policy_error");
    assert!(value["result"]["error"]["message"]
        .as_str()
        .unwrap()
        .contains("UTF-8"));
    assert!(!stdout.contains(POLICY_SENTINEL));
    assert!(!stderr.contains(POLICY_SENTINEL));
    assert_eq!(
        value["policy"]["sha256"].as_str().unwrap().len(),
        64,
        "in-limit invalid UTF-8 policy is hashed, not inlined"
    );
}

#[test]
fn oversized_policy_is_policy_error_without_content_or_partial_hash() {
    let temp = TempDir::new().unwrap();
    let path = temp.path().join("huge-policy.toml");
    let mut payload = format!("# {POLICY_SENTINEL}\n").into_bytes();
    payload.resize(1024 * 1024 + 1, b'x');
    std::fs::write(&path, &payload).unwrap();

    let output = scan_json_with_policy(&path);
    assert_eq!(output.status.code(), Some(1));
    let stdout = String::from_utf8(output.stdout).unwrap();
    let stderr = String::from_utf8(output.stderr).unwrap();
    let value: serde_json::Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(value["contract_version"], 1);
    assert_eq!(value["result"]["status"], "error");
    assert_eq!(value["result"]["error"]["code"], "policy_error");
    let message = value["result"]["error"]["message"].as_str().unwrap();
    assert!(message.contains("policy file exceeds"));
    assert!(!message.contains("input exceeds"));
    assert!(!message.contains("input_too_large"));
    assert_eq!(
        value["policy"]["sha256"], "",
        "oversized policy must not be hashed from a truncated prefix"
    );
    assert!(!stdout.contains(POLICY_SENTINEL));
    assert!(!stderr.contains(POLICY_SENTINEL));
    assert!(!stdout.contains(&"x".repeat(64)));
}
