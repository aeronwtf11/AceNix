"""Errors with stable CLI exit codes."""


class AceNixError(Exception):
    exit_code = 1


class ValidationError(AceNixError):
    exit_code = 2


class ToolError(AceNixError):
    exit_code = 3


class ConflictError(AceNixError):
    exit_code = 4


class PermissionError(AceNixError):
    exit_code = 5


class Cancelled(AceNixError):
    exit_code = 6
