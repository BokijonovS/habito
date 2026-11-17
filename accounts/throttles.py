from config.throttling import WindowRateThrottle


class LoginThrottle(WindowRateThrottle):
    """5 login attempts per email per 15 minutes (rate set in settings)."""

    scope = "login"

    def get_cache_key(self, request, view):
        email = request.data.get("email") if isinstance(request.data, dict) else None
        if not email or not isinstance(email, str):
            return None
        return self.cache_format % {"scope": self.scope, "ident": email.strip().lower()}
