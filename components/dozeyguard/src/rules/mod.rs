use std::path::{Component, Path, PathBuf};

use regex::Regex;

use crate::finding::{sort_findings, Finding, Severity};
use crate::model::{ComposeDocument, Mount, PublishedPort, Service};
use crate::policy::Policy;

pub fn scan(document: &ComposeDocument, policy: &Policy) -> Vec<Finding> {
    let mut findings = Vec::new();
    for (service_name, service) in &document.services {
        scan_service(service_name, service, document, policy, &mut findings);
    }
    policy.apply_exceptions(&mut findings);
    sort_findings(&mut findings);
    findings
}

fn scan_service(
    service_name: &str,
    service: &Service,
    document: &ComposeDocument,
    policy: &Policy,
    findings: &mut Vec<Finding>,
) {
    dg001_privileged(service_name, service, findings);
    dg002_docker_sock(service_name, service, findings);
    dg003_host_namespaces(service_name, service, findings);
    dg004_published_ports(service_name, service, policy, findings);
    dg005_root_bind(service_name, service, findings);
    dg006_broad_mounts(service_name, service, findings);
    dg007_cap_add(service_name, service, findings);
    dg008_unconfined(service_name, service, findings);
    dg009_root_user(service_name, service, findings);
    dg010_cap_drop_all(service_name, service, findings);
    dg011_no_new_privileges(service_name, service, findings);
    dg012_read_only(service_name, service, findings);
    dg013_image_tag(service_name, service, policy, findings);
    dg014_pids_limit(service_name, service, findings);
    dg015_memory_limit(service_name, service, findings);
    dg016_cpu_limit(service_name, service, findings);
    dg017_healthcheck(service_name, service, findings);
    dg018_log_rotation(service_name, service, findings);
    dg019_docker_group(service_name, service, findings);
    dg020_devices(service_name, service, findings);
    dg021_literal_secret(service_name, service, findings);
    dg022_restart_policy(service_name, service, findings);
    dg023_writable_binds(service_name, service, policy, findings);
    dg024_internal_public_network(service_name, service, document, policy, findings);
    dg025_docker_host(service_name, service, findings);
    dg026_cap_add_all(service_name, service, findings);
}

fn finding(
    rule_id: &'static str,
    severity: Severity,
    service: &str,
    field: &'static str,
    message: &'static str,
    remediation: &'static str,
) -> Finding {
    Finding::new(rule_id, severity, service, field, message, remediation)
}

fn service_mounts(service: &Service) -> Vec<Mount> {
    service.volumes.iter().map(Mount::from_value).collect()
}

fn dg001_privileged(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if service.privileged == Some(true) {
        findings.push(finding(
            "DG001",
            Severity::Critical,
            service_name,
            "privileged",
            "Service runs with privileged=true.",
            "Remove privileged mode and grant only specific required capabilities.",
        ));
    }
}

fn dg002_docker_sock(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if service_mounts(service).iter().any(|mount| {
        ["/var/run/docker.sock", "/run/docker.sock"]
            .iter()
            .any(|socket| {
                mount.source.as_deref() == Some(*socket) || mount.target.as_deref() == Some(*socket)
            })
    }) {
        findings.push(finding(
            "DG002",
            Severity::Critical,
            service_name,
            "volumes",
            "Service mounts the Docker socket.",
            "Remove docker.sock access; use a narrow broker outside this deployment path if needed.",
        ));
    }
}

fn dg003_host_namespaces(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    for (field, value) in [
        ("network_mode", service.network_mode.as_deref()),
        ("pid", service.pid.as_deref()),
        ("ipc", service.ipc.as_deref()),
    ] {
        if value == Some("host") {
            findings.push(finding(
                "DG003",
                Severity::Critical,
                service_name,
                field,
                "Service joins a host namespace.",
                "Use isolated container namespaces unless an approved exception exists.",
            ));
        }
    }
}

fn dg004_published_ports(
    service_name: &str,
    service: &Service,
    policy: &Policy,
    findings: &mut Vec<Finding>,
) {
    for port in service.ports.iter().filter_map(PublishedPort::from_value) {
        let non_loopback_host = !matches!(port.host_ip.as_deref(), Some("127.0.0.1") | Some("::1"));
        if non_loopback_host {
            findings.push(finding(
                "DG004",
                Severity::High,
                service_name,
                "ports",
                "Published port is missing an explicit loopback host_ip or binds publicly.",
                "Bind local-only services to 127.0.0.1 or ::1.",
            ));
            continue;
        }

        let Some(published) = port.published else {
            findings.push(finding(
                "DG004",
                Severity::High,
                service_name,
                "ports",
                "Published port could not be validated against the allowed local port ranges.",
                "Use a single explicit numeric host port inside an allowed policy range.",
            ));
            continue;
        };
        if !policy.port_allowed(published) {
            findings.push(finding(
                "DG004",
                Severity::High,
                service_name,
                "ports",
                "Published port is outside the allowed local port ranges.",
                "Move the host port into an allowed policy range.",
            ));
        }
    }
}

fn dg005_root_bind(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if service_mounts(service)
        .iter()
        .any(|mount| mount.is_bind && mount.source.as_deref() == Some("/"))
    {
        findings.push(finding(
            "DG005",
            Severity::Critical,
            service_name,
            "volumes",
            "Service bind mounts the host root filesystem.",
            "Mount only the narrow directory the service needs.",
        ));
    }
}

fn dg006_broad_mounts(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    let broad_roots = ["/home", "/home/dozey", "/etc", "/var/run"];
    if service_mounts(service).iter().any(|mount| {
        mount.is_bind
            && mount.source.as_deref().is_some_and(|source| {
                broad_roots
                    .iter()
                    .any(|root| source == *root || source.starts_with(&format!("{root}/")))
            })
    }) {
        findings.push(finding(
            "DG006",
            Severity::High,
            service_name,
            "volumes",
            "Service bind mounts a broad sensitive host path.",
            "Replace broad host mounts with a narrow allowlisted directory.",
        ));
    }
}

fn dg007_cap_add(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    let dangerous = ["SYS_ADMIN", "SYS_PTRACE", "NET_ADMIN", "DAC_OVERRIDE"];
    if Service::string_list(&service.cap_add)
        .iter()
        .any(|capability| {
            let normalized = capability.trim().to_ascii_uppercase();
            // DG026 owns the ALL grant; DG007 stays limited to named dangerous caps.
            normalized != "ALL" && dangerous.contains(&normalized.as_str())
        })
    {
        findings.push(finding(
            "DG007",
            Severity::High,
            service_name,
            "cap_add",
            "Service adds a high-risk Linux capability.",
            "Remove the capability or isolate the workload behind an approved exception.",
        ));
    }
}

fn dg026_cap_add_all(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    let grants_all = Service::string_list(&service.cap_add)
        .iter()
        .any(|capability| capability.trim().eq_ignore_ascii_case("ALL"));
    if grants_all {
        findings.push(finding(
            "DG026",
            Severity::Critical,
            service_name,
            "cap_add",
            "Service grants all Linux capabilities via cap_add ALL.",
            "Use cap_drop: [ALL], then add back only reviewed individual capabilities. Exceptions need service scope, reason, owner, and expiry.",
        ));
    }
}

fn dg008_unconfined(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if Service::string_list(&service.security_opt)
        .iter()
        .any(|option| {
            option == "seccomp=unconfined"
                || option == "apparmor=unconfined"
                || option == "seccomp:unconfined"
                || option == "apparmor:unconfined"
        })
    {
        findings.push(finding(
            "DG008",
            Severity::High,
            service_name,
            "security_opt",
            "Service disables seccomp or AppArmor confinement.",
            "Use the default runtime profile or a reviewed custom profile.",
        ));
    }
}

fn dg009_root_user(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    let user = service.user_string();
    let is_root = match user.as_deref() {
        None => true,
        Some(user) => {
            user == "root" || user == "0" || user.starts_with("0:") || user.starts_with("root:")
        }
    };
    if is_root {
        findings.push(finding(
            "DG009",
            Severity::Medium,
            service_name,
            "user",
            "Service has no explicit non-root user.",
            "Set user to a non-root UID:GID.",
        ));
    }
}

fn dg010_cap_drop_all(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    let has_all = Service::string_list(&service.cap_drop)
        .iter()
        .any(|capability| capability == "ALL");
    if !has_all {
        findings.push(finding(
            "DG010",
            Severity::Medium,
            service_name,
            "cap_drop",
            "Service does not drop all Linux capabilities by default.",
            "Set cap_drop: [ALL], then add back only reviewed capabilities.",
        ));
    }
}

fn dg011_no_new_privileges(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    let has_nnp = Service::string_list(&service.security_opt)
        .iter()
        .any(|option| option == "no-new-privileges:true");
    if !has_nnp {
        findings.push(finding(
            "DG011",
            Severity::Medium,
            service_name,
            "security_opt",
            "Service does not set no-new-privileges.",
            "Add security_opt: [no-new-privileges:true].",
        ));
    }
}

fn dg012_read_only(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if service.read_only != Some(true) {
        findings.push(finding(
            "DG012",
            Severity::Medium,
            service_name,
            "read_only",
            "Service root filesystem is writable.",
            "Set read_only: true and move writable paths to explicit volumes.",
        ));
    }
}

fn dg013_image_tag(
    service_name: &str,
    service: &Service,
    policy: &Policy,
    findings: &mut Vec<Finding>,
) {
    let Some(image) = service.image.as_deref() else {
        findings.push(finding(
            "DG013",
            Severity::High,
            service_name,
            "image",
            "Service image is missing.",
            "Pin the service to an approved registry image with tag or digest.",
        ));
        return;
    };

    if image_uses_latest_or_no_tag(image) {
        findings.push(finding(
            "DG013",
            Severity::High,
            service_name,
            "image",
            "Service image uses latest or has no explicit tag/digest.",
            "Pin image to an immutable digest or explicit non-latest tag.",
        ));
    }

    if !policy.global.allowed_registries.is_empty() {
        let registry = image_registry(image);
        if !policy
            .global
            .allowed_registries
            .iter()
            .any(|allowed| allowed == &registry)
        {
            findings.push(finding(
                "DG013",
                Severity::High,
                service_name,
                "image",
                "Service image registry is not allowlisted by policy.",
                "Use an image from an allowlisted registry.",
            ));
        }
    }
}

fn dg014_pids_limit(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if service.pids_limit.is_none() {
        findings.push(finding(
            "DG014",
            Severity::Medium,
            service_name,
            "pids_limit",
            "Service does not set pids_limit.",
            "Set a finite pids_limit for the workload.",
        ));
    }
}

fn dg015_memory_limit(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if service.mem_limit.is_none() && service.deploy_limit("memory").is_none() {
        findings.push(finding(
            "DG015",
            Severity::Medium,
            service_name,
            "mem_limit",
            "Service does not set a memory limit.",
            "Set mem_limit or deploy.resources.limits.memory.",
        ));
    }
}

fn dg016_cpu_limit(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if service.cpus.is_none()
        && service.cpu_quota.is_none()
        && service.deploy_limit("cpus").is_none()
    {
        findings.push(finding(
            "DG016",
            Severity::Low,
            service_name,
            "cpus",
            "Service does not set a CPU limit.",
            "Set cpus, cpu_quota, or deploy.resources.limits.cpus.",
        ));
    }
}

fn dg017_healthcheck(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if !service.healthcheck_enabled() {
        findings.push(finding(
            "DG017",
            Severity::Medium,
            service_name,
            "healthcheck",
            "Service has no enabled healthcheck.",
            "Add a healthcheck appropriate for the service readiness signal.",
        ));
    }
}

fn dg018_log_rotation(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    let has_rotation = service
        .logging
        .as_ref()
        .and_then(|logging| {
            logging
                .get("driver")
                .and_then(|driver| driver.as_str())
                .zip(logging.get("options"))
        })
        .is_some_and(|(driver, options)| {
            driver == "json-file"
                && options.get("max-size").is_some()
                && options.get("max-file").is_some()
        });

    if !has_rotation {
        findings.push(finding(
            "DG018",
            Severity::Low,
            service_name,
            "logging",
            "Service does not configure json-file log rotation.",
            "Set logging.driver=json-file with max-size and max-file options.",
        ));
    }
}

fn dg019_docker_group(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if Service::string_list(&service.group_add)
        .iter()
        .any(|group| group == "docker" || group == "138")
    {
        findings.push(finding(
            "DG019",
            Severity::High,
            service_name,
            "group_add",
            "Service joins the Docker group.",
            "Remove docker group membership from the container.",
        ));
    }
}

fn dg020_devices(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if !service.devices.is_empty() || !service.device_cgroup_rules.is_empty() {
        findings.push(finding(
            "DG020",
            Severity::Critical,
            service_name,
            "devices",
            "Service exposes host devices or device cgroup rules.",
            "Remove device access or add a time-bound policy exception.",
        ));
    }
}

fn dg021_literal_secret(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    let sensitive =
        Regex::new(r"(?i)(TOKEN|PASSWORD|SECRET|API_KEY|PRIVATE_KEY)").expect("valid regex");
    for entry in service.environment_entries() {
        if sensitive.is_match(&entry.key) && entry.has_literal_value {
            findings.push(Finding {
                rule_id: "DG021".to_string(),
                severity: Severity::High,
                service: service_name.to_string(),
                field: format!("environment.{}", entry.key),
                message: "Sensitive environment key contains a literal value.".to_string(),
                remediation: "Move the value to a Docker secret or runtime secret manager."
                    .to_string(),
                exception_status: crate::finding::ExceptionStatus::None,
            });
        }
    }
}

fn dg022_restart_policy(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    let missing_or_always = match service.restart.as_deref() {
        None => true,
        Some(restart) => restart == "always",
    };
    if missing_or_always {
        findings.push(finding(
            "DG022",
            Severity::Medium,
            service_name,
            "restart",
            "Service restart policy is missing or set to always.",
            "Use restart: unless-stopped unless the workload has a stronger reviewed need.",
        ));
    }
}

fn dg023_writable_binds(
    service_name: &str,
    service: &Service,
    policy: &Policy,
    findings: &mut Vec<Finding>,
) {
    for mount in service_mounts(service) {
        if !mount.is_bind {
            continue;
        }
        let Some(source) = mount.source.as_deref() else {
            continue;
        };
        let roots = if mount.read_only {
            &policy.global.readonly_bind_roots
        } else {
            &policy.global.writable_bind_roots
        };
        if !path_under_any(source, roots) {
            findings.push(finding(
                "DG023",
                Severity::High,
                service_name,
                "volumes",
                "Service has a bind mount outside the matching read-only or writable allowlisted roots.",
                "Move the bind under the matching policy root or add an approved root reference through Secure Deploy.",
            ));
        }
    }
}

fn dg024_internal_public_network(
    service_name: &str,
    service: &Service,
    document: &ComposeDocument,
    policy: &Policy,
    findings: &mut Vec<Finding>,
) {
    if policy.service_class(service_name) != Some("internal") {
        return;
    }

    for network_name in service.network_names() {
        let top_level = document.networks.get(&network_name);
        let effective_name = top_level
            .and_then(|network| network.name.as_deref())
            .unwrap_or(&network_name);
        let public_by_name = effective_name.contains("public")
            || effective_name.contains("external")
            || effective_name.contains("proxy")
            || effective_name.contains("web");
        let public_by_external = top_level
            .map(|network| network.external.is_external())
            .unwrap_or(false);
        if public_by_name || public_by_external {
            findings.push(finding(
                "DG024",
                Severity::High,
                service_name,
                "networks",
                "Internal service is attached to an external or public network.",
                "Attach internal services only to private project networks.",
            ));
        }
    }
}

fn dg025_docker_host(service_name: &str, service: &Service, findings: &mut Vec<Finding>) {
    if service
        .environment_entries()
        .iter()
        .any(|entry| entry.key == "DOCKER_HOST")
    {
        findings.push(finding(
            "DG025",
            Severity::Critical,
            service_name,
            "environment.DOCKER_HOST",
            "Service configures an alternate Docker host endpoint.",
            "Remove Docker host access from deployable services.",
        ));
    }
}

fn image_uses_latest_or_no_tag(image: &str) -> bool {
    if let Some((reference, digest)) = image.rsplit_once('@') {
        return reference.is_empty() || reference.contains('@') || !valid_sha256_digest(digest);
    }

    let last_segment = image.rsplit('/').next().unwrap_or(image);
    !last_segment.contains(':') || last_segment.ends_with(":latest")
}

fn valid_sha256_digest(digest: &str) -> bool {
    let Some(hex) = digest.strip_prefix("sha256:") else {
        return false;
    };
    hex.len() == 64 && hex.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn image_registry(image: &str) -> String {
    let first_segment = image.split('/').next().unwrap_or_default();
    if first_segment.contains('.') || first_segment.contains(':') || first_segment == "localhost" {
        first_segment.to_string()
    } else {
        "docker.io".to_string()
    }
}

fn path_under_any(path: &str, roots: &[String]) -> bool {
    let Some(path) = normalize_absolute_path(path) else {
        return false;
    };
    roots.iter().any(|root| {
        normalize_absolute_path(root)
            .as_ref()
            .is_some_and(|root| path == *root || path.starts_with(root))
    })
}

fn normalize_absolute_path(path: &str) -> Option<PathBuf> {
    let source = Path::new(path);
    if !source.is_absolute() {
        return None;
    }

    let mut normalized = PathBuf::from("/");
    for component in source.components() {
        match component {
            Component::RootDir => {}
            Component::CurDir => {}
            Component::Normal(part) => normalized.push(part),
            Component::ParentDir => return None,
            Component::Prefix(_) => return None,
        }
    }
    Some(normalized)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::policy::{GlobalPolicy, Policy};

    fn scan_service_json(service: serde_json::Value) -> Vec<Finding> {
        let document = ComposeDocument {
            services: [("app".to_string(), serde_json::from_value(service).unwrap())].into(),
            networks: Default::default(),
        };
        scan(&document, &Policy::default())
    }

    fn has(findings: &[Finding], rule: &str) -> bool {
        findings.iter().any(|finding| finding.rule_id == rule)
    }

    #[test]
    fn dg001_detects_privileged() {
        assert!(has(
            &scan_service_json(json!({"privileged": true})),
            "DG001"
        ));
    }

    #[test]
    fn dg002_detects_docker_socket_aliases() {
        for socket in ["/var/run/docker.sock", "/run/docker.sock"] {
            assert!(
                has(
                    &scan_service_json(json!({"volumes": [format!("{socket}:{socket}")]})),
                    "DG002"
                ),
                "expected DG002 for {socket}"
            );
        }
    }

    #[test]
    fn dg003_detects_host_namespaces() {
        assert!(has(
            &scan_service_json(json!({"network_mode": "host"})),
            "DG003"
        ));
    }

    #[test]
    fn dg004_requires_explicit_loopback_binding() {
        for port in [
            "8080:80",
            "0.0.0.0:8080:80",
            "192.168.1.10:8080:80",
            "10.0.0.5:8080:80",
            "[::]:8080:80",
        ] {
            assert!(
                has(&scan_service_json(json!({"ports": [port]})), "DG004"),
                "expected DG004 for {port}"
            );
        }

        for port in ["127.0.0.1:8080:80", "[::1]:8080:80"] {
            assert!(
                !has(&scan_service_json(json!({"ports": [port]})), "DG004"),
                "did not expect DG004 for {port}"
            );
        }

        for port in ["127.0.0.1:8000-8005:80-85", "[::1]:8000-8005:80-85"] {
            assert!(
                has(&scan_service_json(json!({"ports": [port]})), "DG004"),
                "expected fail-closed DG004 for unvalidated range {port}"
            );
        }
    }

    #[test]
    fn dg005_detects_root_bind() {
        assert!(has(
            &scan_service_json(json!({"volumes": ["/:/host:ro"]})),
            "DG005"
        ));
    }

    #[test]
    fn dg006_detects_broad_mount() {
        assert!(has(
            &scan_service_json(json!({"volumes": ["/etc:/etc:ro"]})),
            "DG006"
        ));
    }

    #[test]
    fn dg007_detects_dangerous_capability() {
        assert!(has(
            &scan_service_json(json!({"cap_add": ["SYS_ADMIN"]})),
            "DG007"
        ));
    }

    #[test]
    fn dg008_detects_unconfined_profile() {
        assert!(has(
            &scan_service_json(json!({"security_opt": ["seccomp=unconfined"]})),
            "DG008"
        ));
    }

    #[test]
    fn dg009_detects_root_user() {
        assert!(has(&scan_service_json(json!({"user": "0:0"})), "DG009"));
    }

    #[test]
    fn dg010_detects_missing_cap_drop_all() {
        assert!(has(&scan_service_json(json!({})), "DG010"));
    }

    #[test]
    fn dg011_detects_missing_no_new_privileges() {
        assert!(has(&scan_service_json(json!({})), "DG011"));
    }

    #[test]
    fn dg012_detects_writable_rootfs() {
        assert!(has(
            &scan_service_json(json!({"read_only": false})),
            "DG012"
        ));
    }

    #[test]
    fn dg013_detects_latest_and_invalid_digests() {
        for image in [
            "nginx:latest",
            "nginx@garbage",
            "nginx@sha256:nothex",
            "nginx@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "nginx@@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ] {
            assert!(
                has(&scan_service_json(json!({"image": image})), "DG013"),
                "expected DG013 for {image}"
            );
        }

        let pinned = format!("nginx@sha256:{}", "a".repeat(64));
        assert!(!has(&scan_service_json(json!({"image": pinned})), "DG013"));
    }

    #[test]
    fn dg014_detects_missing_pids_limit() {
        assert!(has(&scan_service_json(json!({})), "DG014"));
    }

    #[test]
    fn dg015_detects_missing_memory_limit() {
        assert!(has(&scan_service_json(json!({})), "DG015"));
    }

    #[test]
    fn dg016_detects_missing_cpu_limit() {
        assert!(has(&scan_service_json(json!({})), "DG016"));
    }

    #[test]
    fn dg017_detects_missing_healthcheck() {
        assert!(has(&scan_service_json(json!({})), "DG017"));
    }

    #[test]
    fn dg018_detects_missing_log_rotation() {
        assert!(has(&scan_service_json(json!({})), "DG018"));
    }

    #[test]
    fn dg019_detects_docker_group() {
        assert!(has(
            &scan_service_json(json!({"group_add": ["docker"]})),
            "DG019"
        ));
    }

    #[test]
    fn dg020_detects_devices() {
        assert!(has(
            &scan_service_json(json!({"devices": ["/dev/kvm:/dev/kvm"]})),
            "DG020"
        ));
    }

    #[test]
    fn dg021_detects_literal_secret_without_value_leak() {
        for value in ["SECRET_DO_NOT_PRINT_123", "${API_KEY:-fallback-value}"] {
            let findings = scan_service_json(json!({"environment": {"API_KEY": value}}));
            let finding = findings
                .iter()
                .find(|finding| finding.rule_id == "DG021")
                .expect("literal secret must be detected");
            assert!(finding.field.contains("API_KEY"));
            assert!(!format!("{finding:?}").contains(value));
        }

        let referenced = scan_service_json(json!({"environment": {"API_KEY": "${API_KEY}"}}));
        assert!(!has(&referenced, "DG021"));
    }

    #[test]
    fn dg022_detects_always_restart() {
        assert!(has(
            &scan_service_json(json!({"restart": "always"})),
            "DG022"
        ));
    }

    #[test]
    fn dg023_detects_writable_bind_outside_allowlist() {
        let document = ComposeDocument {
            services: [(
                "app".to_string(),
                serde_json::from_value(json!({"volumes": ["/tmp/app:/data"]})).unwrap(),
            )]
            .into(),
            networks: Default::default(),
        };
        let policy = Policy {
            global: GlobalPolicy {
                writable_bind_roots: vec!["/srv/apps".to_string()],
                ..Default::default()
            },
            ..Default::default()
        };
        assert!(has(&scan(&document, &policy), "DG023"));
    }

    #[test]
    fn dg023_allows_writable_bind_under_normalized_root() {
        let document = ComposeDocument {
            services: [(
                "app".to_string(),
                serde_json::from_value(json!({"volumes": ["/srv//apps/./app:/data"]})).unwrap(),
            )]
            .into(),
            networks: Default::default(),
        };
        let policy = Policy {
            global: GlobalPolicy {
                writable_bind_roots: vec!["/srv/apps".to_string()],
                ..Default::default()
            },
            ..Default::default()
        };
        assert!(!has(&scan(&document, &policy), "DG023"));
    }

    #[test]
    fn dg023_rejects_traversal_before_containment() {
        let policy = Policy {
            global: GlobalPolicy {
                writable_bind_roots: vec!["/allowed/root".to_string()],
                ..Default::default()
            },
            ..Default::default()
        };
        for source in [
            "/allowed/root/../../../etc",
            "/allowed/root/../root/app",
            "/allowed/root/./../root/app",
        ] {
            let document = ComposeDocument {
                services: [(
                    "app".to_string(),
                    serde_json::from_value(json!({"volumes": [format!("{source}:/data")]}))
                        .unwrap(),
                )]
                .into(),
                networks: Default::default(),
            };
            assert!(has(&scan(&document, &policy), "DG023"), "{source}");
        }
    }

    #[test]
    fn dg023_distinguishes_sibling_prefixes() {
        let document = ComposeDocument {
            services: [(
                "app".to_string(),
                serde_json::from_value(json!({"volumes": ["/root2/app:/data"]})).unwrap(),
            )]
            .into(),
            networks: Default::default(),
        };
        let policy = Policy {
            global: GlobalPolicy {
                writable_bind_roots: vec!["/root".to_string()],
                ..Default::default()
            },
            ..Default::default()
        };
        assert!(has(&scan(&document, &policy), "DG023"));
    }

    #[test]
    fn dg023_checks_readonly_binds_against_readonly_roots() {
        let policy = Policy {
            global: GlobalPolicy {
                readonly_bind_roots: vec!["/srv/readonly".to_string()],
                writable_bind_roots: vec!["/srv/writable".to_string()],
                ..Default::default()
            },
            ..Default::default()
        };

        let allowed = ComposeDocument {
            services: [(
                "app".to_string(),
                serde_json::from_value(json!({"volumes": ["/srv/readonly/config:/config:ro"]}))
                    .unwrap(),
            )]
            .into(),
            networks: Default::default(),
        };
        assert!(!has(&scan(&allowed, &policy), "DG023"));

        for source in ["/root/.ssh", "/var/lib/docker", "/srv/writable/config"] {
            let document = ComposeDocument {
                services: [(
                    "app".to_string(),
                    serde_json::from_value(json!({"volumes": [format!("{source}:/config:ro")]}))
                        .unwrap(),
                )]
                .into(),
                networks: Default::default(),
            };
            assert!(has(&scan(&document, &policy), "DG023"), "{source}");
        }
    }

    #[test]
    fn dg023_approved_root_ref_does_not_bypass_bind_policy() {
        let policy = Policy {
            global: GlobalPolicy {
                readonly_bind_roots: vec!["/srv/readonly".to_string()],
                ..Default::default()
            },
            ..Default::default()
        };
        let document: ComposeDocument = serde_json::from_value(json!({
            "services": {
                "app": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": "/var/lib/docker",
                            "target": "/host-docker",
                            "read_only": true,
                            "approved_root_ref": "docker"
                        }
                    ]
                }
            }
        }))
        .unwrap();

        assert!(has(&scan(&document, &policy), "DG023"));
    }

    #[test]
    fn dg024_detects_internal_service_on_public_network() {
        let policy = Policy::from_toml(
            r#"
            [services.app]
            class = "internal"
            "#,
        )
        .unwrap();
        let document: ComposeDocument = serde_json::from_value(json!({
            "services": {"app": {"networks": ["public"]}},
            "networks": {"public": {"external": true}}
        }))
        .unwrap();
        assert!(has(&scan(&document, &policy), "DG024"));
    }

    #[test]
    fn dg025_detects_docker_host_without_value_leak() {
        for environment in [
            json!({"DOCKER_HOST": "tcp://127.0.0.1:2375"}),
            json!({"DOCKER_HOST": "${DOCKER_HOST}"}),
            json!({"DOCKER_HOST": null}),
        ] {
            let findings = scan_service_json(json!({"environment": environment}));
            let finding = findings
                .iter()
                .find(|finding| finding.rule_id == "DG025")
                .expect("DOCKER_HOST must always be rejected");
            assert!(!format!("{finding:?}").contains("2375"));
        }

        let inherited = scan_service_json(json!({"environment": ["DOCKER_HOST"]}));
        assert!(has(&inherited, "DG025"));
    }

    #[test]
    fn dg026_detects_cap_add_all_variants() {
        for value in [
            json!({"cap_add": ["ALL"]}),
            json!({"cap_add": ["all"]}),
            json!({"cap_add": ["All"]}),
            json!({"cap_add": "ALL"}),
            json!({"cap_add": ["NET_ADMIN", "ALL"]}),
            json!({"cap_add": [" ALL "]}),
        ] {
            assert!(
                has(&scan_service_json(value.clone()), "DG026"),
                "expected DG026 for {value}"
            );
        }
        let only_sys = scan_service_json(json!({"cap_add": ["SYS_ADMIN"]}));
        assert!(has(&only_sys, "DG007"));
        assert!(!has(&only_sys, "DG026"));
    }
}
