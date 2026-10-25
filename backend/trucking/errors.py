"""Exception types that map cleanly onto HTTP responses."""


class ApiError(Exception):
    status = 500


class ValidationError(ApiError):
    status = 400


class ExternalServiceError(ApiError):
    status = 502


class RoutingError(ExternalServiceError):
    pass


class WeatherError(ExternalServiceError):
    pass
