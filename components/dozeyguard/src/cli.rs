use std::fs;
use std::io;
use std::path::PathBuf;

use anyhow::{bail, Context};
use clap::{Parser, Subcommand, ValueEnum};

use crate::finding::{scan_exit_code, Severity};
use crate::input::read_bounded;
use crate::output;
use crate::parser;
use crate::policy::Policy;
use crate::rules;
use crate::{DEFAULT_MAX_INPUT_BYTES, HARD_MAX_INPUT_BYTES, JSON_CONTRACT_VERSION};

#[derive(Debug, Parser)]
#[command(
    name = "dozeyguard",
    version,
    about = "Static Docker Compose policy gate"
)]
pub struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
    Scan(ScanArgs),
}

#[derive(Debug, Parser)]
struct ScanArgs {
    #[arg(long)]
    input: String,

    #[arg(long, value_enum)]
    input_format: InputFormat,

    #[arg(long)]
    policy: PathBuf,

    #[arg(long, value_enum, default_value_t = OutputFormat::Human)]
    output: OutputFormat,

    #[arg(long, value_enum, default_value_t = Severity::High)]
    fail_on: Severity,

    /// Maximum accepted Compose JSON size in bytes (default 2 MiB, hard max 16 MiB).
    #[arg(long, default_value_t = DEFAULT_MAX_INPUT_BYTES)]
    max_input_bytes: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum InputFormat {
    ComposeJson,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum OutputFormat {
    Human,
    Json,
    Markdown,
}

pub fn run() -> anyhow::Result<i32> {
    run_with(Cli::parse())
}

fn run_with(cli: Cli) -> anyhow::Result<i32> {
    match cli.command {
        Commands::Scan(args) => scan(args),
    }
}

fn emit_error(
    args: &ScanArgs,
    code: &'static str,
    message: String,
    input: Option<&[u8]>,
    policy: Option<&[u8]>,
) -> i32 {
    if args.output == OutputFormat::Json {
        match output::json::render_error_report(code, message, input, policy) {
            Ok(rendered) => {
                print!("{rendered}");
            }
            Err(error) => {
                // Fail-closed: never include input fragments.
                eprintln!("failed to render JSON error envelope: {error}");
            }
        }
    } else {
        eprintln!("{message}");
    }
    1
}

fn scan(args: ScanArgs) -> anyhow::Result<i32> {
    if args.max_input_bytes == 0 {
        return Ok(emit_error(
            &args,
            "invalid_limit",
            "max-input-bytes must be greater than zero".to_string(),
            None,
            None,
        ));
    }
    if args.max_input_bytes > HARD_MAX_INPUT_BYTES {
        return Ok(emit_error(
            &args,
            "invalid_limit",
            format!(
                "max-input-bytes {} exceeds hard maximum {}",
                args.max_input_bytes, HARD_MAX_INPUT_BYTES
            ),
            None,
            None,
        ));
    }

    let input = match read_input(&args.input, args.max_input_bytes) {
        Ok(bytes) => bytes,
        Err(error) => {
            let message = format!("{error:#}");
            let code = if error
                .chain()
                .any(|cause| cause.to_string().contains("input exceeds"))
            {
                "input_too_large"
            } else {
                "input_error"
            };
            return Ok(emit_error(&args, code, message, None, None));
        }
    };

    let policy_text = match fs::read_to_string(&args.policy) {
        Ok(text) => text,
        Err(error) => {
            return Ok(emit_error(
                &args,
                "policy_error",
                format!("failed to read policy file: {error}"),
                Some(&input),
                None,
            ));
        }
    };
    let policy_bytes = policy_text.as_bytes();

    let policy = match Policy::from_toml(&policy_text) {
        Ok(policy) => policy,
        Err(_) => {
            return Ok(emit_error(
                &args,
                "policy_error",
                "failed to parse policy TOML".to_string(),
                Some(&input),
                Some(policy_bytes),
            ));
        }
    };

    let input_text = match std::str::from_utf8(&input) {
        Ok(text) => text,
        Err(_) => {
            return Ok(emit_error(
                &args,
                "input_error",
                "input is not valid UTF-8".to_string(),
                Some(&input),
                Some(policy_bytes),
            ));
        }
    };

    let document = match args.input_format {
        InputFormat::ComposeJson => match parser::parse_compose_json(input_text) {
            Ok(document) => document,
            Err(_) => {
                return Ok(emit_error(
                    &args,
                    "parse_error",
                    "failed to parse Compose JSON".to_string(),
                    Some(&input),
                    Some(policy_bytes),
                ));
            }
        },
    };

    let findings = rules::scan(&document, &policy);
    let services = document.services.len();

    let rendered = match args.output {
        OutputFormat::Human => output::human::render(&findings),
        OutputFormat::Json => output::json::render_scan_report(
            &findings,
            args.fail_on,
            services,
            &input,
            policy_bytes,
        )?,
        OutputFormat::Markdown => output::markdown::render(&findings),
    };

    print!("{rendered}");
    let _ = JSON_CONTRACT_VERSION;
    Ok(scan_exit_code(&findings, args.fail_on))
}

fn read_input(input: &str, max_bytes: usize) -> anyhow::Result<Vec<u8>> {
    if input == "-" {
        return read_bounded(io::stdin().lock(), max_bytes).context("failed to read stdin");
    }

    // Fast precheck for regular files. Final authority is the bounded reader
    // (covers TOCTOU growth and non-regular paths where metadata is unreliable).
    if let Ok(meta) = fs::metadata(input) {
        if meta.is_file() {
            let len = meta.len();
            let max = u64::try_from(max_bytes).unwrap_or(u64::MAX);
            if len > max {
                bail!(
                    "input exceeds max-input-bytes limit ({} > {})",
                    len,
                    max_bytes
                );
            }
        }
    }

    let file = fs::File::open(input).context("failed to read input file")?;
    read_bounded(file, max_bytes).context("failed to read input file")
}
