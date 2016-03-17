from django.conf.urls import *
from incident import views

urlpatterns = patterns('django.views.generic.simple',

     # index 'home page' of the <your-app> app
     #url( r'^$', views.index, name='index' ),
     url( r'^$', views.report, name='report'),
     url( r'^save',views.save_incident, name='save'),
    url( r'^delete',views.delete_incident, name='delete_incident'),
 )