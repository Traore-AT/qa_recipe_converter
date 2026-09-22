from django.contrib import admin
from .models import ConversionJob, ExtractedUseCase


class ExtractedUseCaseInline(admin.TabularInline):
    model = ExtractedUseCase
    extra = 0
    readonly_fields = ('id', 'order', 'use_case_text', 'status')
    fields = ('order', 'use_case_text', 'description', 'is_automated', 'status')


@admin.register(ConversionJob)
class ConversionJobAdmin(admin.ModelAdmin):
    list_display = ('source_filename', 'status', 'use_cases_count', 'created_at')
    list_filter = ('status',)
    search_fields = ('source_filename',)
    readonly_fields = ('id', 'created_at', 'updated_at')
    inlines = [ExtractedUseCaseInline]


@admin.register(ExtractedUseCase)
class ExtractedUseCaseAdmin(admin.ModelAdmin):
    list_display = ('order', 'use_case_text', 'job', 'is_automated', 'status')
    list_filter = ('status', 'is_automated')
    search_fields = ('use_case_text', 'description')
