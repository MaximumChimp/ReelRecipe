from django.urls import path
from . import views
from django.views.generic import TemplateView
urlpatterns = [
    path('',views.home_view,name='home'),
    path('privacy-policy/',views.privacy_policy, name="privacy-policy"),
    path('terms-of-service/',views.terms_of_service,name="terms-of-service"),
    path(
        'ads.txt', 
        TemplateView.as_view(template_name="ads.txt", content_type="text/plain")
    ),
]