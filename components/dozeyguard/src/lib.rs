pub mod cli;
pub mod finding;
pub mod hashutil;
pub mod model;
pub mod output;
pub mod parser;
pub mod policy;
pub mod rules;

/// JSON report contract version emitted by `--output json`.
pub const JSON_CONTRACT_VERSION: u32 = 1;

/// Default CLI input size limit (2 MiB).
pub const DEFAULT_MAX_INPUT_BYTES: usize = 2 * 1024 * 1024;

/// Hard compile-time maximum input size (16 MiB). CLI cannot exceed this.
pub const HARD_MAX_INPUT_BYTES: usize = 16 * 1024 * 1024;
