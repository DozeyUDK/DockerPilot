fn main() {
    std::process::exit(match dozeyguard::cli::run() {
        Ok(code) => code,
        Err(error) => {
            // Fail-closed fallback for unexpected errors (no input fragments).
            eprintln!("{error}");
            1
        }
    });
}
