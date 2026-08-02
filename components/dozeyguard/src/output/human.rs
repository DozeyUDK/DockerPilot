use crate::finding::{ExceptionStatus, Finding};

pub fn render(findings: &[Finding]) -> String {
    if findings.is_empty() {
        return "No findings.\n".to_string();
    }

    let mut output = String::new();
    for finding in findings {
        let exception = match finding.exception_status {
            ExceptionStatus::None => "none",
            ExceptionStatus::Active => "active",
            ExceptionStatus::Expired => "expired",
        };
        output.push_str(&format!(
            "{} {:?} service={} field={} exception={}\n  {}\n  remediation: {}\n",
            finding.rule_id,
            finding.severity,
            finding.service,
            finding.field,
            exception,
            finding.message,
            finding.remediation
        ));
    }
    output
}
