from config.throttling import WindowRateThrottle


class JoinThrottle(WindowRateThrottle):
    """10 join attempts per IP address per 15 minutes (rate set in settings)."""

    scope = "join"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
