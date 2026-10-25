from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _user_data(user):
    return {
        "username": user.get_username(),
        "email": user.email or "",
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
    }


class TokenLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = request.data.get("username", "")
        password = request.data.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is None:
            return Response({"detail": "Invalid username or password."}, status=status.HTTP_400_BAD_REQUEST)

        if "email" in request.data:
            email = str(request.data.get("email") or "").strip()
            try:
                validate_email(email)
            except DjangoValidationError:
                return Response({"detail": "Enter a valid email address."}, status=status.HTTP_400_BAD_REQUEST)
            if user.email != email:
                user.email = email
                user.save(update_fields=["email"])

        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": _user_data(user)})


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_user_data(request.user))
