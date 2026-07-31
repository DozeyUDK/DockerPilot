"""Broker error codes."""

from __future__ import annotations


class BrokerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ProtocolError(BrokerError):
    pass


class VerificationError(BrokerError):
    pass
