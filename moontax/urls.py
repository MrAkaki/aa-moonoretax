"""URL routes for moontax. Mounted under ``^moontax/`` by :mod:`moontax.auth_hooks`."""

from django.urls import path

from moontax import views

app_name = "moontax"

urlpatterns = [
    # User dashboard
    path("", views.index, name="index"),
    # Notification preferences
    path("notifications/", views.notification_settings, name="notifications"),
    # Staff
    path("staff/", views.staff, name="staff"),
    path("staff/invoice/<int:invoice_id>/action/", views.staff_action, name="staff_action"),
    # Admin
    path("admin/", views.admin_config, name="admin"),
    path("admin/ore-rate/<int:rate_id>/delete/", views.ore_rate_delete, name="ore_rate_delete"),
    path("admin/token/mining/setup/", views.token_setup_mining, name="token_setup_mining"),
    path("admin/token/payment/setup/", views.token_setup_payment, name="token_setup_payment"),
    path("admin/token/<str:role>/remove/", views.token_remove, name="token_remove"),
]
