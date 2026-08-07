use std::collections::{BTreeMap, BTreeSet};

use serde::Deserialize;
use serde_json::Value;

#[derive(Clone, Debug, Default, Deserialize)]
pub struct ComposeDocument {
    #[serde(default)]
    pub services: BTreeMap<String, Service>,
    #[serde(default)]
    pub networks: BTreeMap<String, Network>,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Network {
    #[serde(default)]
    pub external: ExternalFlag,
    #[serde(default)]
    pub name: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(untagged)]
pub enum ExternalFlag {
    Bool(bool),
    Object(BTreeMap<String, Value>),
}

impl Default for ExternalFlag {
    fn default() -> Self {
        Self::Bool(false)
    }
}

impl ExternalFlag {
    pub fn is_external(&self) -> bool {
        match self {
            Self::Bool(value) => *value,
            Self::Object(_) => true,
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Service {
    #[serde(default)]
    pub image: Option<String>,
    #[serde(default)]
    pub privileged: Option<bool>,
    #[serde(default)]
    pub network_mode: Option<String>,
    #[serde(default)]
    pub pid: Option<String>,
    #[serde(default)]
    pub ipc: Option<String>,
    #[serde(default)]
    pub user: Option<Value>,
    #[serde(default)]
    pub read_only: Option<bool>,
    #[serde(default)]
    pub pids_limit: Option<Value>,
    #[serde(default)]
    pub mem_limit: Option<Value>,
    #[serde(default)]
    pub cpus: Option<Value>,
    #[serde(default)]
    pub cpu_quota: Option<Value>,
    #[serde(default)]
    pub restart: Option<String>,
    #[serde(default)]
    pub cap_add: Option<Value>,
    #[serde(default)]
    pub cap_drop: Option<Value>,
    #[serde(default)]
    pub security_opt: Option<Value>,
    #[serde(default)]
    pub group_add: Option<Value>,
    #[serde(default)]
    pub volumes: Vec<Value>,
    #[serde(default)]
    pub ports: Vec<Value>,
    #[serde(default)]
    pub devices: Vec<Value>,
    #[serde(default)]
    pub device_cgroup_rules: Vec<Value>,
    #[serde(default)]
    pub environment: Option<Value>,
    #[serde(default)]
    pub healthcheck: Option<Value>,
    #[serde(default)]
    pub logging: Option<Value>,
    #[serde(default)]
    pub networks: Option<Value>,
    #[serde(default)]
    pub deploy: Option<Value>,
}

impl Service {
    pub fn string_list(value: &Option<Value>) -> Vec<String> {
        match value {
            Some(Value::String(item)) => vec![item.clone()],
            Some(Value::Array(items)) => items
                .iter()
                .filter_map(|item| match item {
                    Value::String(text) => Some(text.clone()),
                    Value::Number(number) => Some(number.to_string()),
                    _ => None,
                })
                .collect(),
            _ => Vec::new(),
        }
    }

    pub fn user_string(&self) -> Option<String> {
        value_to_string(self.user.as_ref())
    }

    pub fn deploy_limit(&self, key: &str) -> Option<&Value> {
        self.deploy
            .as_ref()?
            .get("resources")?
            .get("limits")?
            .get(key)
    }

    pub fn healthcheck_enabled(&self) -> bool {
        match &self.healthcheck {
            Some(Value::Object(map)) => {
                !map.get("disable").and_then(Value::as_bool).unwrap_or(false)
            }
            Some(_) => true,
            None => false,
        }
    }

    pub fn network_names(&self) -> BTreeSet<String> {
        match &self.networks {
            Some(Value::Array(items)) => items
                .iter()
                .filter_map(Value::as_str)
                .map(ToOwned::to_owned)
                .collect(),
            Some(Value::Object(map)) => map.keys().cloned().collect(),
            Some(Value::String(name)) => BTreeSet::from([name.clone()]),
            _ => BTreeSet::new(),
        }
    }

    pub fn environment_entries(&self) -> Vec<EnvironmentEntry> {
        match &self.environment {
            Some(Value::Object(map)) => map
                .iter()
                .map(|(key, value)| EnvironmentEntry {
                    key: key.clone(),
                    has_literal_value: env_value_is_literal(value),
                })
                .collect(),
            Some(Value::Array(items)) => items
                .iter()
                .filter_map(Value::as_str)
                .map(parse_env_list_item)
                .collect(),
            _ => Vec::new(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EnvironmentEntry {
    pub key: String,
    pub has_literal_value: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Mount {
    pub source: Option<String>,
    pub target: Option<String>,
    pub read_only: bool,
    pub is_bind: bool,
}

impl Mount {
    pub fn from_value(value: &Value) -> Self {
        match value {
            Value::String(text) => parse_mount_string(text),
            Value::Object(map) => {
                let read_only = map
                    .get("read_only")
                    .or_else(|| map.get("readonly"))
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                Self {
                    source: map
                        .get("source")
                        .or_else(|| map.get("src"))
                        .and_then(Value::as_str)
                        .map(ToOwned::to_owned),
                    target: map
                        .get("target")
                        .or_else(|| map.get("dst"))
                        .or_else(|| map.get("destination"))
                        .and_then(Value::as_str)
                        .map(ToOwned::to_owned),
                    read_only,
                    is_bind: map
                        .get("type")
                        .and_then(Value::as_str)
                        .map(|kind| kind == "bind")
                        .unwrap_or(true),
                }
            }
            _ => Self {
                source: None,
                target: None,
                read_only: false,
                is_bind: false,
            },
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PublishedPort {
    pub host_ip: Option<String>,
    pub published: Option<u16>,
}

impl PublishedPort {
    pub fn from_value(value: &Value) -> Option<Self> {
        match value {
            Value::String(text) => parse_port_string(text),
            Value::Object(map) => Some(Self {
                host_ip: map
                    .get("host_ip")
                    .or_else(|| map.get("host_ipaddr"))
                    .and_then(Value::as_str)
                    .map(ToOwned::to_owned),
                published: map
                    .get("published")
                    .or_else(|| map.get("published_port"))
                    .and_then(value_to_u16),
            }),
            _ => None,
        }
    }
}

pub fn value_to_string(value: Option<&Value>) -> Option<String> {
    match value {
        Some(Value::String(text)) => Some(text.clone()),
        Some(Value::Number(number)) => Some(number.to_string()),
        _ => None,
    }
}

fn env_value_is_literal(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::String(text) => !text.is_empty() && !text.starts_with("${"),
        Value::Bool(_) | Value::Number(_) => true,
        Value::Array(_) | Value::Object(_) => false,
    }
}

fn parse_env_list_item(item: &str) -> EnvironmentEntry {
    if let Some((key, value)) = item.split_once('=') {
        return EnvironmentEntry {
            key: key.to_string(),
            has_literal_value: !value.is_empty() && !value.starts_with("${"),
        };
    }

    EnvironmentEntry {
        key: item.to_string(),
        has_literal_value: false,
    }
}

fn parse_mount_string(text: &str) -> Mount {
    let parts: Vec<&str> = text.split(':').collect();
    let read_only = parts.iter().skip(2).any(|part| {
        part.split(',')
            .any(|option| option == "ro" || option == "readonly")
    });

    Mount {
        source: parts.first().map(|part| (*part).to_string()),
        target: parts.get(1).map(|part| (*part).to_string()),
        read_only,
        is_bind: parts
            .first()
            .map(|source| {
                source.starts_with('/') || source.starts_with("./") || source.starts_with("../")
            })
            .unwrap_or(false),
    }
}

fn parse_port_string(text: &str) -> Option<PublishedPort> {
    let without_protocol = text.split('/').next().unwrap_or(text);

    if let Some(ipv6) = without_protocol.strip_prefix('[') {
        let (host_ip, remainder) = ipv6.split_once("]:")?;
        let (published, _target) = remainder.split_once(':')?;
        return Some(PublishedPort {
            host_ip: Some(host_ip.to_string()),
            published: published.parse().ok(),
        });
    }

    let parts: Vec<&str> = without_protocol.split(':').collect();
    match parts.as_slice() {
        [published, _target] => Some(PublishedPort {
            host_ip: None,
            published: published.parse().ok(),
        }),
        [host_ip, published, _target] => Some(PublishedPort {
            host_ip: Some((*host_ip).to_string()),
            published: published.parse().ok(),
        }),
        _ => None,
    }
}

fn value_to_u16(value: &Value) -> Option<u16> {
    match value {
        Value::Number(number) => number
            .as_u64()
            .and_then(|number| u16::try_from(number).ok()),
        Value::String(text) => text.parse().ok(),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_port_string_supports_ipv6_bindings() {
        let public = parse_port_string("[::]:8080:80").expect("public IPv6 binding");
        assert_eq!(public.host_ip.as_deref(), Some("::"));
        assert_eq!(public.published, Some(8080));

        let loopback =
            parse_port_string("[::1]:18080:80/tcp").expect("loopback IPv6 binding");
        assert_eq!(loopback.host_ip.as_deref(), Some("::1"));
        assert_eq!(loopback.published, Some(18080));
    }
}
