"""Contrôle d'accès aux recettes (ConversionJob), commun à l'API et aux pages Django."""
from django.core.exceptions import PermissionDenied

from apps.teams.permissions import can_view_project, can_write_project


def check_job_access(user, job, write=False):
    """
    - recette rattachée à un projet : mêmes droits que le projet (lecture / écriture) ;
    - recette personnelle : réservée à son auteur ;
    - recette anonyme : accessible à qui détient son lien (identifiant non devinable).
    """
    if user.is_authenticated and user.is_superuser:
        return
    if job.project_id:
        allowed = can_write_project(user, job.project) if write else can_view_project(user, job.project)
        # can_write_project ignore la visibilité : un projet privé reste réservé à ses membres.
        if not allowed or not can_view_project(user, job.project):
            raise PermissionDenied("Vous n'avez pas accès à cette recette.")
    elif job.uploaded_by_id and job.uploaded_by_id != user.pk:
        raise PermissionDenied("Vous n'avez pas accès à cette recette.")
