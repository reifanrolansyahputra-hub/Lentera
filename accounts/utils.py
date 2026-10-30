from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

HOME = {"murid": "/murid/", "guru": "/guru/", "admin": "/admin/"}


def get_role(user):
    """'murid' | 'guru' | 'admin' | None. Tiga role: murid, guru, dan admin."""
    if not user.is_authenticated:
        return None
    profile = getattr(user, "profile", None)
    return profile.role if profile else None


def role_required(*roles):
    def deco(view):
        @wraps(view)
        @login_required
        def wrapped(request, *a, **kw):
            role = get_role(request.user)
            return view(request, *a, **kw) if role in roles else redirect(HOME.get(role, "/login/"))
        return wrapped
    return deco
