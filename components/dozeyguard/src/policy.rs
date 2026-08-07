use std::collections::BTreeMap;

use chrono::{NaiveDate, Utc};
use serde::Deserialize;
use thiserror::Error;

use crate::finding::{ExceptionStatus, Finding};

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Policy {
    #[serde(default)]
    pub global: GlobalPolicy,
    #[serde(default)]
    pub services: BTreeMap<String, ServicePolicy>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct GlobalPolicy {
    #[serde(default)]
    pub allowed_registries: Vec<String>,
    #[serde(default)]
    pub readonly_bind_roots: Vec<String>,
    #[serde(default)]
    pub writable_bind_roots: Vec<String>,
    #[serde(default)]
    pub allowed_local_port_ranges: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct ServicePolicy {
    #[serde(default)]
    pub class: Option<String>,
    #[serde(default)]
    pub exceptions: Vec<PolicyException>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct PolicyException {
    pub rule: String,
    pub reason: String,
    pub owner: String,
    pub expires: NaiveDate,
}

#[derive(Debug, Error)]
pub enum PolicyError {
    #[error("failed to parse policy TOML")]
    Toml,
}

impl Policy {
    pub fn from_toml(input: &str) -> Result<Self, PolicyError> {
        toml::from_str(input).map_err(|_error| PolicyError::Toml)
    }

    pub fn service_class(&self, service: &str) -> Option<&str> {
        self.services
            .get(service)
            .and_then(|entry| entry.class.as_deref())
    }

    pub fn apply_exceptions(&self, findings: &mut [Finding]) {
        let today = Utc::now().date_naive();
        for finding in findings {
            let Some(service_policy) = self.services.get(&finding.service) else {
                continue;
            };
            let matching = service_policy
                .exceptions
                .iter()
                .find(|exception| exception.rule == finding.rule_id);

            if let Some(exception) = matching {
                let metadata_present =
                    !exception.reason.trim().is_empty() && !exception.owner.trim().is_empty();
                finding.exception_status = if !metadata_present {
                    ExceptionStatus::None
                } else if exception.expires >= today {
                    ExceptionStatus::Active
                } else {
                    ExceptionStatus::Expired
                };
            }
        }
    }

    pub fn port_allowed(&self, port: u16) -> bool {
        if self.global.allowed_local_port_ranges.is_empty() {
            return true;
        }

        self.global
            .allowed_local_port_ranges
            .iter()
            .filter_map(|range| range.split_once('-'))
            .filter_map(|(start, end)| Some((start.parse::<u16>().ok()?, end.parse::<u16>().ok()?)))
            .any(|(start, end)| (start..=end).contains(&port))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::finding::{ExceptionStatus, Severity};

    fn finding() -> Finding {
        Finding::new(
            "DG001",
            Severity::Critical,
            "app",
            "privileged",
            "test",
            "test",
        )
    }

    fn policy_with_exception(reason: &str, owner: &str, expires: NaiveDate) -> Policy {
        Policy {
            services: BTreeMap::from([(
                "app".to_string(),
                ServicePolicy {
                    class: None,
                    exceptions: vec![PolicyException {
                        rule: "DG001".to_string(),
                        reason: reason.to_string(),
                        owner: owner.to_string(),
                        expires,
                    }],
                },
            )]),
            ..Default::default()
        }
    }

    #[test]
    fn exception_requires_reason_and_owner() {
        let future = Utc::now().date_naive() + chrono::Duration::days(1);
        for (reason, owner) in [("", "security"), ("approved", ""), ("   ", "security")] {
            let mut findings = vec![finding()];
            policy_with_exception(reason, owner, future).apply_exceptions(&mut findings);
            assert_eq!(findings[0].exception_status, ExceptionStatus::None);
            assert!(findings[0].blocks_at(Severity::High));
        }
    }

    #[test]
    fn valid_exception_is_active_until_expiry() {
        let today = Utc::now().date_naive();
        let mut active = vec![finding()];
        policy_with_exception("approved", "security", today).apply_exceptions(&mut active);
        assert_eq!(active[0].exception_status, ExceptionStatus::Active);
        assert!(!active[0].blocks_at(Severity::High));

        let mut expired = vec![finding()];
        policy_with_exception("approved", "security", today - chrono::Duration::days(1))
            .apply_exceptions(&mut expired);
        assert_eq!(expired[0].exception_status, ExceptionStatus::Expired);
        assert!(expired[0].blocks_at(Severity::High));
    }
}
