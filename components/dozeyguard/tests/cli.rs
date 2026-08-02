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
