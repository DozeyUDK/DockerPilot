//! Bounded input reading for Secure Deploy compose payloads.

use std::io::Read;

use anyhow::{Context, Result};
use thiserror::Error;

/// Reader produced more than `limit` bytes. Display keeps the historical
/// input-limit wording used by the Compose JSON path.
#[derive(Debug, Error)]
#[error("input exceeds max-input-bytes limit ({actual} > {limit})")]
pub struct SizeLimitExceeded {
    pub actual: usize,
    pub limit: usize,
}

pub fn is_size_limit_exceeded(error: &anyhow::Error) -> bool {
    error
        .chain()
        .any(|cause| cause.downcast_ref::<SizeLimitExceeded>().is_some())
}

/// Read at most `max_bytes` from `reader`.
///
/// Reads up to `max_bytes + 1` via [`Read::take`] so a single extra byte proves
/// oversized input without loading an unbounded stream into memory.
pub fn read_bounded<R: Read>(reader: R, max_bytes: usize) -> Result<Vec<u8>> {
    let limit = max_bytes
        .checked_add(1)
        .ok_or_else(|| anyhow::anyhow!("max-input-bytes is too large"))?;
    let mut buffer = Vec::new();
    reader
        .take(u64::try_from(limit).unwrap_or(u64::MAX))
        .read_to_end(&mut buffer)
        .context("failed to read")?;
    if buffer.len() > max_bytes {
        return Err(SizeLimitExceeded {
            actual: buffer.len(),
            limit: max_bytes,
        }
        .into());
    }
    Ok(buffer)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{self, Cursor};

    /// Reader that counts bytes delivered and errors if asked for more than `cap`.
    struct CountingReader {
        data: Cursor<Vec<u8>>,
        read_bytes: usize,
        cap: usize,
    }

    impl CountingReader {
        fn new(data: Vec<u8>, cap: usize) -> Self {
            Self {
                data: Cursor::new(data),
                read_bytes: 0,
                cap,
            }
        }
    }

    impl Read for CountingReader {
        fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
            if self.read_bytes >= self.cap {
                return Err(io::Error::other(
                    "CountingReader: attempted to read past bound",
                ));
            }
            let room = self.cap - self.read_bytes;
            let take = buf.len().min(room);
            let n = self.data.read(&mut buf[..take])?;
            self.read_bytes += n;
            Ok(n)
        }
    }

    #[test]
    fn exact_limit_passes() {
        let data = vec![b'a'; 16];
        let out = read_bounded(Cursor::new(data.clone()), 16).unwrap();
        assert_eq!(out, data);
    }

    #[test]
    fn limit_plus_one_rejects() {
        let data = vec![b'a'; 17];
        let err = read_bounded(Cursor::new(data), 16).unwrap_err().to_string();
        assert!(err.contains("input exceeds"));
        assert!(err.contains("17 > 16"));
    }

    #[test]
    fn large_stream_stops_at_max_plus_one() {
        let data = vec![b'x'; 10_000];
        let mut reader = CountingReader::new(data, 33); // allow at most max+1
        let err = read_bounded(&mut reader, 32).unwrap_err().to_string();
        assert!(err.contains("input exceeds"));
        assert_eq!(reader.read_bytes, 33);
    }

    #[test]
    fn zero_limit_rejects_any_byte() {
        let err = read_bounded(Cursor::new(vec![b'a']), 0)
            .unwrap_err()
            .to_string();
        assert!(err.contains("input exceeds"));
    }

    #[test]
    fn zero_limit_accepts_empty() {
        let out = read_bounded(Cursor::new(Vec::<u8>::new()), 0).unwrap();
        assert!(out.is_empty());
    }

    #[test]
    fn overflow_on_max_plus_one() {
        let err = read_bounded(Cursor::new(b"x".as_slice()), usize::MAX)
            .unwrap_err()
            .to_string();
        assert!(err.contains("too large"));
    }

    #[test]
    fn size_limit_error_survives_anyhow_context() {
        let err = read_bounded(Cursor::new(vec![b'a'; 3]), 2)
            .unwrap_err()
            .context("wrapper");
        assert!(is_size_limit_exceeded(&err));
        assert!(format!("{err:#}").contains("input exceeds"));
    }
}
