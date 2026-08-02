use thiserror::Error;

use crate::model::ComposeDocument;

#[derive(Debug, Error)]
pub enum ParseError {
    #[error("failed to parse Compose JSON")]
    ComposeJson,
}

pub fn parse_compose_json(input: &str) -> Result<ComposeDocument, ParseError> {
    serde_json::from_str(input).map_err(|_error| ParseError::ComposeJson)
}
