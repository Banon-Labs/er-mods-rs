//! One error type for the whole builder: a message a player can act on.
//!
//! The builder runs once, on someone else's machine, against files this repo has never seen. When
//! it fails, the only useful output is a sentence naming the file and what was wrong with it, so
//! every error carries a chain of context strings rather than a type hierarchy nobody matches on.

use std::fmt;

#[derive(Debug)]
pub struct Error(String);

impl Error {
    pub fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    /// The same failure, with what was being attempted in front of it.
    pub fn context(self, context: impl fmt::Display) -> Self {
        Self(format!("{context}: {}", self.0))
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl From<std::io::Error> for Error {
    fn from(error: std::io::Error) -> Self {
        Self(error.to_string())
    }
}

impl From<std::num::TryFromIntError> for Error {
    fn from(error: std::num::TryFromIntError) -> Self {
        Self(error.to_string())
    }
}

impl From<std::array::TryFromSliceError> for Error {
    fn from(error: std::array::TryFromSliceError) -> Self {
        Self(error.to_string())
    }
}

impl From<String> for Error {
    fn from(message: String) -> Self {
        Self(message)
    }
}

impl From<&str> for Error {
    fn from(message: &str) -> Self {
        Self(message.to_owned())
    }
}

pub type Result<T> = std::result::Result<T, Error>;

/// `.context(..)` on any result whose error converts into [`Error`].
pub trait Context<T> {
    fn context(self, context: impl fmt::Display) -> Result<T>;
}

impl<T, E: Into<Error>> Context<T> for std::result::Result<T, E> {
    fn context(self, context: impl fmt::Display) -> Result<T> {
        self.map_err(|error| error.into().context(context))
    }
}
