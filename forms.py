from django import forms
from django.forms import DateTimeField, DateTimeInput, EmailField, \
						 CharField, TextInput, Textarea, ChoiceField,\
						 Select, FileField
import datetime

class UploadForm(forms.Form):  
	SCOPES = (('', 'Select microscope',),('confocal', 'Confocal',), ('nstorm', 'nSTORM',))
	LEVELS = (('', 'Severity',),('low', 'Low',), ('medium', 'Medium',),('high', 'High',))
	microscope = ChoiceField(required=True,choices=SCOPES,\
							 widget=Select(attrs={'class':'form-control',\
							 					   'selected': 'selected'}))
	severity = ChoiceField(required=True,choices=LEVELS,\
							 widget=Select(attrs={'class':'form-control',\
							 					   'selected': 'selected'}))
	date = DateTimeField(initial=datetime.date.today,required=True,\
		widget=DateTimeInput(attrs={'class':'form-control','style': 'display:none;'}))
	user = CharField(widget=TextInput(attrs={'class':'form-control','placeholder':'Name'}),required=True)
	email = EmailField(widget=TextInput(attrs={'class':'form-control','placeholder':'Email'}),required=True)
	comment = CharField(widget=Textarea(attrs={'class':'form-control','placeholder':'Comment'}),required=True) 
	file = FileField() 

	def save(self, temp_file, uploaded_file):  
		print 'File "%s" would presumably be saved to disk now.' % uploaded_file  
		pass
       
class FeedbackForm(forms.Form):
	name = CharField(initial='Your name',required=True)
	email = EmailField(required=True)
	comment = CharField(widget=Textarea,required=True)
      
