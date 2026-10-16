from django.urls import path

from . import views

app_name = "poker"

urlpatterns = [
    path("", views.current, name="current"),
    path("hands/", views.hand_list, name="hands"),
    path("hands/<str:table>/<int:number>/", views.hand_detail, name="hand"),
    path("hands/<str:table>/<int:number>/recommend/", views.request_analysis, name="request_analysis"),
    path("hands/<str:table>/<int:number>/recommend/<int:pk>/reveal/", views.reveal_recommendation, name="reveal"),
]
