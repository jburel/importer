from django.conf.urls import *
from importer import views

urlpatterns = patterns('django.views.generic.simple',

     # index 'home page' of the <your-app> app
     #url( r'^$', views.index, name='index' ),
     url( r'^$', views.upload, name='upload'),
     url( r'^listprojects$', views.listProjects_json, name='listProjects_json'),
     url( r'^listdatasets$', views.listDatasets_json, name='listDatasets_json'),     
 )