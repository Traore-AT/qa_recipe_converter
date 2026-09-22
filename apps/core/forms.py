from django import forms
from django.conf import settings
import os

from .word_validation import WordFileValidationError, validate_word_upload


class UploadForm(forms.Form):
    # ── Fichiers ────────────────────────────────────────────────────────────
    word_file = forms.FileField(
        label='Fichier Word (.docx/.doc)',
        help_text='Fichier Word contenant les tableaux de cas de tests',
        widget=forms.ClearableFileInput(attrs={'accept': '.docx,.doc'}),
    )
    excel_template = forms.FileField(
        label='Template Excel (optionnel)',
        required=False,
        help_text='Template Excel personnalisé (.xlsx)',
        widget=forms.ClearableFileInput(attrs={'accept': '.xlsx'}),
    )

    # ── En-tête entreprise ───────────────────────────────────────────────────
    company_name = forms.CharField(
        label="Nom de l'entreprise",
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'placeholder': 'Ex : AT-TechGN, Accenture…'}),
    )
    excel_filename = forms.CharField(
        label='Nom du fichier Excel',
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={'placeholder': 'Ex : recette_sprint3 (sans .xlsx)'}),
    )
    company_logo = forms.FileField(
        label='Logo entreprise',
        required=False,
        help_text='PNG, JPG ou SVG — Affiché en haut à droite de l\'Excel',
        widget=forms.ClearableFileInput(attrs={'accept': '.png,.jpg,.jpeg,.svg,.gif'}),
    )

    # ── Validation ───────────────────────────────────────────────────────────
    def clean_word_file(self):
        f = self.cleaned_data['word_file']
        try:
            validate_word_upload(f)
        except WordFileValidationError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return f

    def clean_excel_template(self):
        f = self.cleaned_data.get('excel_template')
        if f:
            ext = os.path.splitext(f.name)[1].lower()
            if ext not in settings.ALLOWED_EXCEL_EXTENSIONS:
                raise forms.ValidationError(
                    f"Format non supporté. Formats acceptés : {', '.join(settings.ALLOWED_EXCEL_EXTENSIONS)}"
                )
        return f

    def clean_company_logo(self):
        f = self.cleaned_data.get('company_logo')
        if f:
            ext = os.path.splitext(f.name)[1].lower()
            allowed = ['.png', '.jpg', '.jpeg', '.gif', '.svg']
            if ext not in allowed:
                raise forms.ValidationError("Logo : formats acceptés PNG, JPG, GIF, SVG.")
            if f.size > 5 * 1024 * 1024:  # 5 MB
                raise forms.ValidationError("Logo : taille maximale 5 MB.")
        return f
