from django.urls import path

from . import views

urlpatterns = [
    path("rooms", views.RoomListView.as_view(), name="room-list"),
    path("rooms/<uuid:room_id>", views.RoomDetailView.as_view(), name="room-detail"),
    path("rooms/<uuid:room_id>/code", views.RoomCodeView.as_view(), name="room-code"),
    path("rooms/<uuid:room_id>/child", views.RoomChildView.as_view(), name="room-child"),
    path("join", views.JoinView.as_view(), name="join"),
]
