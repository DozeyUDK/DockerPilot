use crate::finding::Finding;

pub fn render(findings: &[Finding]) -> String {
    if findings.is_empty() {
        return "# DozeyGuard report\n\nNo findings.\n".to_string();
    }

    let mut output = String::from(
        "# DozeyGuard report\n\n| Rule | Severity | Service | Field | Exception | Message |\n| --- | --- | --- | --- | --- | --- |\n",
    );
    for finding in findings {
        output.push_str(&format!(
            "| {} | {:?} | {} | {} | {:?} | {} |\n",
            finding.rule_id,
            finding.severity,
            finding.service,
            finding.field,
            finding.exception_status,
            finding.message
        ));
    }
    output
}
