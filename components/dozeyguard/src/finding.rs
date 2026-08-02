use clap::ValueEnum;
use serde::Serialize;
use std::cmp::Ordering;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Low,
    Medium,
    High,
    Critical,
}

impl Severity {
    pub fn rank(self) -> u8 {
        match self {
            Self::Low => 1,
            Self::Medium => 2,
            Self::High => 3,
            Self::Critical => 4,
        }
    }

    /// Findings at or above High are treated as warning-class for summary buckets
    /// when they do not block (e.g. active exception). Critical/High blocking → blocking.
    pub fn is_warning_class(self) -> bool {
        matches!(self, Self::Medium | Self::High | Self::Critical)
    }
}

impl Ord for Severity {
    fn cmp(&self, other: &Self) -> Ordering {
        self.rank().cmp(&other.rank())
    }
}

impl PartialOrd for Severity {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ExceptionStatus {
    None,
    Active,
    Expired,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct Finding {
    pub rule_id: String,
    pub severity: Severity,
    pub service: String,
    pub field: String,
    pub message: String,
    pub remediation: String,
    pub exception_status: ExceptionStatus,
}

impl Finding {
    pub fn new(
        rule_id: &'static str,
        severity: Severity,
        service: &str,
        field: &'static str,
        message: &'static str,
        remediation: &'static str,
    ) -> Self {
        Self {
            rule_id: rule_id.to_string(),
            severity,
            service: service.to_string(),
            field: field.to_string(),
            message: message.to_string(),
            remediation: remediation.to_string(),
            exception_status: ExceptionStatus::None,
        }
    }

    pub fn blocks_at(&self, fail_on: Severity) -> bool {
        self.exception_status != ExceptionStatus::Active && self.severity >= fail_on
    }
}

/// Human/markdown ordering: severity desc, then service, rule, field.
pub fn sort_findings(findings: &mut [Finding]) {
    findings.sort_by(|left, right| {
        right
            .severity
            .cmp(&left.severity)
            .then_with(|| left.service.cmp(&right.service))
            .then_with(|| left.rule_id.cmp(&right.rule_id))
            .then_with(|| left.field.cmp(&right.field))
    });
}

/// JSON contract v1 ordering: service → rule_id → path(field) → message.
pub fn sort_findings_contract(findings: &mut [Finding]) {
    findings.sort_by(|left, right| {
        left.service
            .cmp(&right.service)
            .then_with(|| left.rule_id.cmp(&right.rule_id))
            .then_with(|| left.field.cmp(&right.field))
            .then_with(|| left.message.cmp(&right.message))
    });
}

pub fn scan_exit_code(findings: &[Finding], fail_on: Severity) -> i32 {
    if findings.iter().any(|finding| finding.blocks_at(fail_on)) {
        2
    } else {
        0
    }
}
