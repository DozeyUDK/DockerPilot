//! Dozeyguard JSON contract v1.
//!
//! Exit code compatibility (unchanged):
//! - `0` — no non-excepted finding at or above `--fail-on` (pass or warn)
//! - `1` — input / policy / limit / I/O error
//! - `2` — policy violation (blocking findings)
//!
//! `result.status` mapping:
//! - `pass`  — exit 0, zero findings (or only info/low below fail-on with no warnings bucket)
//! - `warn`  — exit 0, non-blocking findings present
//! - `fail`  — exit 2
//! - `error` — exit 1

use serde::Serialize;
use serde_json::{json, Value};

use crate::finding::{sort_findings_contract, ExceptionStatus, Finding, Severity};
use crate::hashutil::sha256_hex;
use crate::JSON_CONTRACT_VERSION;

#[derive(Serialize)]
struct ContractFinding {
    rule_id: String,
    severity: Severity,
    blocking: bool,
    service: String,
    path: String,
    message: String,
    remediation: String,
    exception: Option<ContractException>,
}

#[derive(Serialize)]
struct ContractException {
    status: ExceptionStatus,
}

#[derive(Serialize)]
struct ScannerInfo {
    name: &'static str,
    version: &'static str,
}

#[derive(Serialize)]
struct InputInfo {
    format: &'static str,
    sha256: String,
    bytes: usize,
}

#[derive(Serialize)]
struct PolicyInfo {
    sha256: String,
    exceptions_applied: Vec<String>,
}

#[derive(Serialize)]
struct Summary {
    services: usize,
    findings: usize,
    blocking: usize,
    warnings: usize,
    info: usize,
}

#[derive(Serialize)]
struct ResultBody {
    status: &'static str,
    exit_code: i32,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<ErrorBody>,
    result_sha256: String,
}

#[derive(Serialize)]
struct ErrorBody {
    code: &'static str,
    message: String,
}

#[derive(Serialize)]
struct JsonReport {
    contract_version: u32,
    scanner: ScannerInfo,
    input: InputInfo,
    policy: PolicyInfo,
    summary: Summary,
    findings: Vec<ContractFinding>,
    result: ResultBody,
}

fn scanner_info() -> ScannerInfo {
    ScannerInfo {
        name: "dozeyguard",
        version: env!("CARGO_PKG_VERSION"),
    }
}

fn contract_findings(findings: &[Finding], fail_on: Severity) -> Vec<ContractFinding> {
    let mut ordered = findings.to_vec();
    sort_findings_contract(&mut ordered);
    ordered
        .into_iter()
        .map(|finding| {
            let blocking = finding.blocks_at(fail_on);
            let exception = match finding.exception_status {
                ExceptionStatus::None => None,
                status => Some(ContractException { status }),
            };
            ContractFinding {
                rule_id: finding.rule_id,
                severity: finding.severity,
                blocking,
                service: finding.service,
                path: finding.field,
                message: finding.message,
                remediation: finding.remediation,
                exception,
            }
        })
        .collect()
}

fn summarize(findings: &[Finding], fail_on: Severity, services: usize) -> Summary {
    let mut blocking = 0usize;
    let mut warnings = 0usize;
    let mut info = 0usize;
    for finding in findings {
        if finding.blocks_at(fail_on) {
            blocking += 1;
        } else if finding.severity == Severity::Low {
            info += 1;
        } else if finding.severity.is_warning_class()
            || finding.exception_status == ExceptionStatus::Active
        {
            warnings += 1;
        } else {
            info += 1;
        }
    }
    Summary {
        services,
        findings: findings.len(),
        blocking,
        warnings,
        info,
    }
}

fn status_for(exit_code: i32, summary: &Summary) -> &'static str {
    match exit_code {
        0 if summary.findings == 0 => "pass",
        0 => "warn",
        2 => "fail",
        _ => "error",
    }
}

fn exceptions_applied(findings: &[Finding]) -> Vec<String> {
    let mut applied: Vec<String> = findings
        .iter()
        .filter(|finding| finding.exception_status == ExceptionStatus::Active)
        .map(|finding| format!("{}:{}", finding.service, finding.rule_id))
        .collect();
    applied.sort();
    applied.dedup();
    applied
}

struct CanonicalParts<'a> {
    contract_version: u32,
    scanner_version: &'a str,
    input_sha256: &'a str,
    input_bytes: usize,
    policy_sha256: &'a str,
    exceptions_applied: &'a [String],
    summary: &'a Summary,
    findings: &'a [ContractFinding],
    status: &'a str,
    exit_code: i32,
}

/// Canonical bytes for `result_sha256` (no timestamp; excludes `result_sha256` itself).
fn canonical_result_bytes(parts: CanonicalParts<'_>) -> Vec<u8> {
    let value = json!({
        "contract_version": parts.contract_version,
        "scanner": {"name": "dozeyguard", "version": parts.scanner_version},
        "input": {
            "format": "compose-json",
            "sha256": parts.input_sha256,
            "bytes": parts.input_bytes
        },
        "policy": {
            "sha256": parts.policy_sha256,
            "exceptions_applied": parts.exceptions_applied
        },
        "summary": {
            "services": parts.summary.services,
            "findings": parts.summary.findings,
            "blocking": parts.summary.blocking,
            "warnings": parts.summary.warnings,
            "info": parts.summary.info
        },
        "findings": parts.findings,
        "result": {
            "status": parts.status,
            "exit_code": parts.exit_code
        }
    });
    serde_json::to_vec(&canonicalize_value(&value)).expect("canonical json")
}

fn canonicalize_value(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = serde_json::Map::new();
            for key in keys {
                out.insert(key.clone(), canonicalize_value(&map[key]));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(canonicalize_value).collect()),
        other => other.clone(),
    }
}

pub fn render_scan_report(
    findings: &[Finding],
    fail_on: Severity,
    services: usize,
    input_bytes: &[u8],
    policy_bytes: &[u8],
) -> anyhow::Result<String> {
    let exit_code = crate::finding::scan_exit_code(findings, fail_on);
    let input_sha = sha256_hex(input_bytes);
    let policy_sha = sha256_hex(policy_bytes);
    let applied = exceptions_applied(findings);
    let summary = summarize(findings, fail_on, services);
    let contract_findings = contract_findings(findings, fail_on);
    let status = status_for(exit_code, &summary);
    let canonical = canonical_result_bytes(CanonicalParts {
        contract_version: JSON_CONTRACT_VERSION,
        scanner_version: env!("CARGO_PKG_VERSION"),
        input_sha256: &input_sha,
        input_bytes: input_bytes.len(),
        policy_sha256: &policy_sha,
        exceptions_applied: &applied,
        summary: &summary,
        findings: &contract_findings,
        status,
        exit_code,
    });
    let result_sha = sha256_hex(&canonical);

    let report = JsonReport {
        contract_version: JSON_CONTRACT_VERSION,
        scanner: scanner_info(),
        input: InputInfo {
            format: "compose-json",
            sha256: input_sha,
            bytes: input_bytes.len(),
        },
        policy: PolicyInfo {
            sha256: policy_sha,
            exceptions_applied: applied,
        },
        summary,
        findings: contract_findings,
        result: ResultBody {
            status,
            exit_code,
            error: None,
            result_sha256: result_sha,
        },
    };

    Ok(format!("{}\n", serde_json::to_string_pretty(&report)?))
}

pub fn render_error_report(
    code: &'static str,
    message: impl Into<String>,
    input_bytes: Option<&[u8]>,
    policy_bytes: Option<&[u8]>,
) -> anyhow::Result<String> {
    let message = message.into();
    let input_sha = input_bytes.map(sha256_hex).unwrap_or_default();
    let input_len = input_bytes.map(|b| b.len()).unwrap_or(0);
    let policy_sha = policy_bytes.map(sha256_hex).unwrap_or_default();
    let summary = Summary {
        services: 0,
        findings: 0,
        blocking: 0,
        warnings: 0,
        info: 0,
    };
    let findings: Vec<ContractFinding> = Vec::new();
    let status = "error";
    let exit_code = 1i32;
    let applied: Vec<String> = Vec::new();
    let canonical = canonical_result_bytes(CanonicalParts {
        contract_version: JSON_CONTRACT_VERSION,
        scanner_version: env!("CARGO_PKG_VERSION"),
        input_sha256: &input_sha,
        input_bytes: input_len,
        policy_sha256: &policy_sha,
        exceptions_applied: &applied,
        summary: &summary,
        findings: &findings,
        status,
        exit_code,
    });
    // Error message is intentionally excluded from result_sha256 so transient wording
    // does not affect digest stability for identical empty scan context.
    let result_sha = sha256_hex(&canonical);

    let report = JsonReport {
        contract_version: JSON_CONTRACT_VERSION,
        scanner: scanner_info(),
        input: InputInfo {
            format: "compose-json",
            sha256: input_sha,
            bytes: input_len,
        },
        policy: PolicyInfo {
            sha256: policy_sha,
            exceptions_applied: applied,
        },
        summary,
        findings,
        result: ResultBody {
            status,
            exit_code,
            error: Some(ErrorBody { code, message }),
            result_sha256: result_sha,
        },
    };

    Ok(format!("{}\n", serde_json::to_string_pretty(&report)?))
}

/// Back-compat helper used by unit tests that only need findings rendering.
pub fn render(findings: &[Finding]) -> anyhow::Result<String> {
    render_scan_report(findings, Severity::High, 0, b"", b"")
}
