class DomainError(Exception):
    def __init__(self, status: int, message: str, code: str = "error"):
        self.status, self.message, self.code = status, message, code
