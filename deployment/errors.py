"""Safe local deployment errors suitable for operator output."""


class DeploymentError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)
