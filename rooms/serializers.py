from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Child, Room, validate_timezone


class ChildSerializer(serializers.ModelSerializer):
    class Meta:
        model = Child
        fields = ["id", "display_name", "joined_at"]
        read_only_fields = fields


class RoomSerializer(serializers.ModelSerializer):
    """Room as the child sees it (and as embedded in /me and /join)."""

    child = ChildSerializer(read_only=True, allow_null=True)

    class Meta:
        model = Room
        fields = ["id", "name", "timezone", "child", "created_at"]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not hasattr(instance, "child"):
            data["child"] = None
        return data


class ParentRoomSerializer(RoomSerializer):
    """Room as the owner sees it: also the current room code, if any."""

    join_code = serializers.SerializerMethodField()
    join_code_expires_at = serializers.SerializerMethodField()

    class Meta(RoomSerializer.Meta):
        fields = RoomSerializer.Meta.fields + ["join_code", "join_code_expires_at"]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_join_code(self, room: Room):
        return room.join_code if room.join_code_is_valid else None

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_join_code_expires_at(self, room: Room):
        return room.join_code_expires_at if room.join_code_is_valid else None


class RoomWriteSerializer(serializers.ModelSerializer):
    name = serializers.CharField(max_length=50)
    timezone = serializers.CharField(max_length=64, validators=[validate_timezone])

    class Meta:
        model = Room
        fields = ["name", "timezone"]


class RoomCodeSerializer(serializers.Serializer):
    join_code = serializers.CharField()
    join_code_expires_at = serializers.DateTimeField()


class JoinSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=16)
    display_name = serializers.CharField(max_length=30)

    def validate_code(self, value: str) -> str:
        return value.strip().upper().replace(" ", "").replace("-", "")


class JoinResponseSerializer(serializers.Serializer):
    child_token = serializers.CharField()
    child = ChildSerializer()
    room = RoomSerializer()
