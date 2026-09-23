"""Public errors never expose local storage paths or tracebacks."""


def error_body(code, message, *, details=None, retryable=False, related_run_id=None):
    return dict(code=code, message=message, details=details or [],
                retryable=retryable, related_run_id=related_run_id)


class ApiProblem(Exception):
    def __init__(self, status, code, message, **kwargs):
        self.status = status
        self.body = error_body(code, message, **kwargs)
        super().__init__(message)


def detail(message, *, file=None, field=None, code="INVALID_DATA", row=None):
    return dict(file=file, field=field, row=row, code=code, message=message)
