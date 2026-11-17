import hashlib
import secrets
import uuid
import zoneinfo
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# No 0/O, 1/I/L so a code read aloud or written down cannot be misread.
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 6
JOIN_CODE_LIFETIME = timedelta(hours=24)

CHILD_TOKEN_BYTES = 32


def validate_timezone(value: str) -> None:
    try:
        zoneinfo.ZoneInfo(value)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, IsADirectoryError):
        raise ValidationError(f"'{value}' is not a known IANA timezone.")


def generate_join_code() -> str:
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))


def generate_child_token() -> str:
    return secrets.token_hex(CHILD_TOKEN_BYTES)


def hash_child_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Room(models.Model):
    """Links one parent and one child. Tasks live inside it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rooms")
    name = models.CharField(max_length=50)
    timezone = models.CharField(max_length=64, validators=[validate_timezone])
    join_code = models.CharField(max_length=JOIN_CODE_LENGTH, null=True, blank=True)
    join_code_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["join_code"],
                condition=models.Q(join_code__isnull=False),
                name="room_join_code_unique",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def tzinfo(self) -> zoneinfo.ZoneInfo:
        return zoneinfo.ZoneInfo(self.timezone)

    def issue_join_code(self) -> str:
        """Set a fresh single-use code valid for 24 hours. Caller saves."""
        for _ in range(20):
            code = generate_join_code()
            if not Room.objects.filter(join_code=code).exclude(pk=self.pk).exists():
                break
        else:  # pragma: no cover - astronomically unlikely
            raise RuntimeError("could not find a free join code")
        self.join_code = code
        self.join_code_expires_at = timezone.now() + JOIN_CODE_LIFETIME
        return code

    def clear_join_code(self) -> None:
        self.join_code = None
        self.join_code_expires_at = None

    @property
    def join_code_is_valid(self) -> bool:
        return bool(self.join_code) and self.join_code_expires_at is not None and self.join_code_expires_at > timezone.now()


class Child(models.Model):
    """The child device joined to a room. Holds only a nickname."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.OneToOneField(Room, on_delete=models.CASCADE, related_name="child")
    display_name = models.CharField(max_length=30)
    device_token_hash = models.CharField(max_length=64, unique=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.display_name} in {self.room_id}"

    # Lets views treat a child like request.user without it being a Django user.
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    @property
    def is_parent(self) -> bool:
        return False

    @property
    def is_child(self) -> bool:
        return True
