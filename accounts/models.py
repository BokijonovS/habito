import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models


class ParentManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("An email address is required")
        email = self.normalize_email(email).lower()
        parent = self.model(email=email, **extra)
        parent.set_password(password)
        parent.save(using=self._db)
        return parent

    def create_user(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("display_name", "Admin")
        return self._create(email, password, **extra)


class Parent(AbstractBaseUser, PermissionsMixin):
    """
    A parent account. Logs in with email and password. The password hash lives
    in the `password` column that Django's auth framework manages (the spec
    calls it password_hash); it is never returned by the API.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=254, unique=True)
    display_name = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ParentManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["display_name"]

    class Meta:
        verbose_name = "parent"

    def __str__(self) -> str:
        return self.email

    @property
    def is_parent(self) -> bool:
        return True

    @property
    def is_child(self) -> bool:
        return False


class ParentDevice(models.Model):
    """One row per parent phone that allowed notifications."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent = models.ForeignKey(Parent, on_delete=models.CASCADE, related_name="devices")
    apns_token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"device {self.id} of {self.parent_id}"
