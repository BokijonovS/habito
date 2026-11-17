from django.urls import path

from . import views

urlpatterns = [
    path("rooms/<uuid:room_id>/tasks", views.RoomTaskListView.as_view(), name="room-tasks"),
    path("rooms/<uuid:room_id>/today", views.TodayView.as_view(), name="room-today"),
    path("rooms/<uuid:room_id>/completed", views.CompletedView.as_view(), name="room-completed"),
    path("rooms/<uuid:room_id>/history", views.HistoryView.as_view(), name="room-history"),
    path("tasks/<uuid:task_id>", views.TaskDetailView.as_view(), name="task-detail"),
    path("tasks/<uuid:task_id>/done", views.TaskDoneView.as_view(), name="task-done"),
]
