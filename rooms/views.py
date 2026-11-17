from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsParent, IsParentOrChild
from config.exceptions import ApiError

from .access import get_room_for
from .models import Child, Room, generate_child_token, hash_child_token
from .serializers import (
    ChildSerializer,
    JoinResponseSerializer,
    JoinSerializer,
    ParentRoomSerializer,
    RoomCodeSerializer,
    RoomSerializer,
    RoomWriteSerializer,
)
from .throttles import JoinThrottle


def _room_for_caller(user, room: Room) -> dict:
    serializer = ParentRoomSerializer if getattr(user, "is_parent", False) else RoomSerializer
    return serializer(room).data


class RoomListView(APIView):
    permission_classes = [IsParent]

    @extend_schema(responses={200: ParentRoomSerializer(many=True)}, tags=["rooms"])
    def get(self, request):
        rooms = Room.objects.filter(parent=request.user).select_related("child")
        return Response(ParentRoomSerializer(rooms, many=True).data)

    @extend_schema(request=RoomWriteSerializer, responses={201: ParentRoomSerializer}, tags=["rooms"])
    def post(self, request):
        serializer = RoomWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            room = Room(parent=request.user, **serializer.validated_data)
            room.issue_join_code()
            room.save()
        return Response(ParentRoomSerializer(room).data, status=status.HTTP_201_CREATED)


class RoomDetailView(APIView):
    permission_classes = [IsParentOrChild]

    @extend_schema(responses={200: ParentRoomSerializer}, tags=["rooms"])
    def get(self, request, room_id):
        room = get_room_for(request.user, room_id)
        return Response(_room_for_caller(request.user, room))

    @extend_schema(request=RoomWriteSerializer, responses={200: ParentRoomSerializer}, tags=["rooms"])
    def patch(self, request, room_id):
        self._parent_only(request)
        room = get_room_for(request.user, room_id)
        serializer = RoomWriteSerializer(room, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ParentRoomSerializer(room).data)

    @extend_schema(responses={204: None}, tags=["rooms"])
    def delete(self, request, room_id):
        self._parent_only(request)
        room = get_room_for(request.user, room_id)
        room.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _parent_only(request):
        if not getattr(request.user, "is_parent", False):
            raise ApiError("forbidden", "Only the parent can do this.", status.HTTP_403_FORBIDDEN)


class RoomCodeView(APIView):
    """New room code. Signs out the current child device, if any."""

    permission_classes = [IsParent]

    @extend_schema(request=None, responses={201: RoomCodeSerializer}, tags=["rooms"])
    def post(self, request, room_id):
        room = get_room_for(request.user, room_id)
        with transaction.atomic():
            Child.objects.filter(room=room).delete()
            room.issue_join_code()
            room.save(update_fields=["join_code", "join_code_expires_at"])
        return Response(
            {"join_code": room.join_code, "join_code_expires_at": room.join_code_expires_at},
            status=status.HTTP_201_CREATED,
        )


class RoomChildView(APIView):
    permission_classes = [IsParent]

    @extend_schema(responses={204: None}, tags=["rooms"])
    def delete(self, request, room_id):
        room = get_room_for(request.user, room_id)
        deleted, _ = Child.objects.filter(room=room).delete()
        if not deleted:
            raise ApiError("not_found", "No child has joined this room.", status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class JoinView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [JoinThrottle]

    @extend_schema(request=JoinSerializer, responses={201: JoinResponseSerializer}, tags=["rooms"])
    def post(self, request):
        serializer = JoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"]
        display_name = serializer.validated_data["display_name"].strip()

        with transaction.atomic():
            room = Room.objects.select_for_update().filter(join_code=code).first()
            if room is None:
                raise ApiError("invalid_code", "That room code is not valid.", status.HTTP_404_NOT_FOUND)
            if not room.join_code_is_valid:
                raise ApiError("code_expired", "That room code has expired. Ask for a new one.", status.HTTP_409_CONFLICT)
            if Child.objects.filter(room=room).exists():
                raise ApiError("code_used", "That room code has already been used.", status.HTTP_409_CONFLICT)

            token = generate_child_token()
            child = Child.objects.create(room=room, display_name=display_name, device_token_hash=hash_child_token(token))
            room.clear_join_code()
            room.save(update_fields=["join_code", "join_code_expires_at"])

        room.child = child
        return Response(
            {"child_token": token, "child": ChildSerializer(child).data, "room": RoomSerializer(room).data},
            status=status.HTTP_201_CREATED,
        )

