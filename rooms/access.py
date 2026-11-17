"""Who may reach which room. A room the caller does not belong to is a 404."""

from rest_framework import status

from config.exceptions import ApiError

from .models import Room


def room_not_found() -> ApiError:
    return ApiError("not_found", "Room not found.", status.HTTP_404_NOT_FOUND)


def get_room_for(user, room_id) -> Room:
    """The room with this id if the caller belongs to it, else a 404 error."""
    if getattr(user, "is_parent", False):
        room = Room.objects.filter(pk=room_id, parent=user).first()
    elif getattr(user, "is_child", False):
        room = user.room if str(user.room_id) == str(room_id) else None
    else:
        room = None
    if room is None:
        raise room_not_found()
    return room
