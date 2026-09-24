"""Shared password requirements for administrator setup and recovery."""

def valid_admin_password(password):
    if len(password)<12:
        return False
    lowered=password.lower()
    return not ("password" in lowered or "123456" in lowered or len(set(password))<4)
