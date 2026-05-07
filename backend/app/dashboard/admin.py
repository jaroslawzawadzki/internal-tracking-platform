from django.contrib import admin
from .models import TrackerLibrary  # Importujemy Twój nowy model
# Register your models here.
@admin.register(TrackerLibrary)
class TrackerLibraryAdmin(admin.ModelAdmin):
    # Te kolumny będą widoczne na liście w panelu admina
    list_display = ('domain', 'name', 'category')
    # To pozwoli Ci wyszukiwać trackery po domenie lub nazwie
    search_fields = ('domain', 'name')