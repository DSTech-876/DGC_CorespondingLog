def notifications(request):
    """Expose the signed-in user's unread notifications to every template.

    Powers the notification bell in the sidebar/topbar without each view
    needing to fetch it explicitly.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    qs = user.notifications.all()
    return {
        "nav_notifications": qs[:6],
        "nav_notifications_unread_count": qs.filter(is_read=False).count(),
    }
