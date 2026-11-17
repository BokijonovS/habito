from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from config.exceptions import ApiError

from .models import ParentDevice
from .permissions import IsParent, IsParentOrChild
from .serializers import (
    AuthResponseSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeResponseSerializer,
    ParentDeviceSerializer,
    ParentSerializer,
    RefreshSerializer,
    RegisterSerializer,
    TokenPairSerializer,
    issue_tokens,
)
from .throttles import LoginThrottle


class RegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=RegisterSerializer, responses={201: AuthResponseSerializer}, tags=["auth"])
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        parent = serializer.save()
        return Response(
            {"parent": ParentSerializer(parent).data, **issue_tokens(parent)},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    @extend_schema(request=LoginSerializer, responses={200: AuthResponseSerializer}, tags=["auth"])
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            raise ApiError("invalid_credentials", "Email or password is wrong.", status.HTTP_401_UNAUTHORIZED)
        parent = serializer.validated_data["parent"]
        return Response({"parent": ParentSerializer(parent).data, **issue_tokens(parent)})


class RefreshView(APIView):
    """
    Trades a refresh token for a new access token and a new refresh token.
    The old refresh token stops working. No access token is needed, since the
    app calls this exactly when the access token has expired.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=RefreshSerializer, responses={200: TokenPairSerializer}, tags=["auth"])
    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            old = RefreshToken(serializer.validated_data["refresh"])
            old.verify()
            parent_id = old["user_id"]
        except (TokenError, KeyError):
            raise ApiError("invalid_token", "Refresh token is invalid or expired.", status.HTTP_401_UNAUTHORIZED)

        from .models import Parent  # local import keeps module import order simple

        parent = Parent.objects.filter(pk=parent_id, is_active=True).first()
        if parent is None:
            raise ApiError("invalid_token", "Refresh token is invalid or expired.", status.HTTP_401_UNAUTHORIZED)

        with transaction.atomic():
            old.blacklist()
            tokens = issue_tokens(parent)
        return Response(tokens)


class LogoutView(APIView):
    permission_classes = [IsParent]

    @extend_schema(request=LogoutSerializer, responses={204: None}, tags=["auth"])
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            if token["user_id"] != str(request.user.id):
                raise TokenError("not yours")
            token.blacklist()
        except (TokenError, KeyError):
            raise ApiError("invalid_token", "Refresh token is invalid or expired.", status.HTTP_400_BAD_REQUEST)

        apns_token = serializer.validated_data.get("apns_token")
        if apns_token:
            ParentDevice.objects.filter(parent=request.user, apns_token=apns_token).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AccountView(APIView):
    permission_classes = [IsParent]

    @extend_schema(responses={204: None}, tags=["auth"])
    def delete(self, request):
        # Cascades: devices, rooms, children, tasks, completions. Token rows are
        # removed explicitly because simplejwt keeps them with a null user.
        with transaction.atomic():
            OutstandingToken.objects.filter(user=request.user).delete()
            request.user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsParentOrChild]

    @extend_schema(responses={200: MeResponseSerializer}, tags=["auth"])
    def get(self, request):
        user = request.user
        if user.is_parent:
            return Response({"role": "parent", "parent": ParentSerializer(user).data})

        from rooms.serializers import ChildSerializer, RoomSerializer

        return Response(
            {
                "role": "child",
                "child": ChildSerializer(user).data,
                "room": RoomSerializer(user.room).data,
            }
        )


class DeviceListView(APIView):
    permission_classes = [IsParent]

    @extend_schema(request=ParentDeviceSerializer, responses={201: ParentDeviceSerializer}, tags=["devices"])
    def post(self, request):
        serializer = ParentDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        apns_token = serializer.validated_data["apns_token"]
        with transaction.atomic():
            device = ParentDevice.objects.filter(apns_token=apns_token).first()
            if device is None:
                device = ParentDevice.objects.create(parent=request.user, apns_token=apns_token)
                created = True
            else:
                created = False
                if device.parent_id != request.user.id:
                    # The phone changed hands (another parent logged in on it).
                    device.parent = request.user
                    device.save(update_fields=["parent"])
        return Response(
            ParentDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DeviceDetailView(APIView):
    permission_classes = [IsParent]

    @extend_schema(responses={204: None}, tags=["devices"])
    def delete(self, request, device_id):
        deleted, _ = ParentDevice.objects.filter(parent=request.user, pk=device_id).delete()
        if not deleted:
            raise ApiError("not_found", "Device not found.", status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
