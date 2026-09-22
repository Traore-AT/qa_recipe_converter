"""
PDF Report generation for teams — Daily Scrum & Weekly reports.
Uses WeasyPrint to render HTML templates → PDF.
"""
import logging
from datetime import date, timedelta
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from apps.core.models import ExtractedUseCase
from .models import Team, TeamMember, Project

logger = logging.getLogger(__name__)


def _get_progress_data(team: Team):
    """Aggregate progress data for all projects in the team."""
    projects = Project.objects.filter(team=team).select_related('created_by')
    total_ucs = 0
    completed_ucs = 0
    failed_ucs = 0
    project_list = []

    for p in projects:
        ucs = ExtractedUseCase.objects.filter(job__project=p)
        stats = p.stats
        total_ucs += stats['total']
        completed_ucs += stats['passed']
        failed_ucs += stats['failed']
        project_list.append({
            'name': p.name,
            'description': p.description,
            'status': p.status,
            'get_status_display': p.get_status_display(),
            'stats': stats,
            'use_cases': list(ucs.values('order', 'use_case_text', 'description', 'status', 'is_automated')[:50]),
            'members': list(p.members.select_related('user').all()),
        })

    return {
        'total_projects': len(projects),
        'active_projects': projects.filter(status='active').count(),
        'total_ucs': total_ucs,
        'completed_ucs': completed_ucs,
        'failed_ucs': failed_ucs,
        'success_rate': round(completed_ucs / total_ucs * 100, 1) if total_ucs > 0 else 0,
    }, project_list


def generate_daily_scrum(team: Team, generated_by: str) -> bytes:
    """Generate a Daily Scrum PDF report for the team."""
    stats, projects = _get_progress_data(team)
    members = TeamMember.objects.filter(team=team).select_related('user')

    html = render_to_string('reports/daily_scrum.html', {
        'team': team,
        'date': timezone.now(),
        'generated_by': generated_by,
        'stats': stats,
        'projects': projects,
        'members': members,
    })
    return HTML(string=html).write_pdf()


def generate_weekly_report(team: Team, generated_by: str) -> bytes:
    """Generate a Weekly PDF report for the team."""
    today = timezone.now()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    stats, projects = _get_progress_data(team)
    members = TeamMember.objects.filter(team=team).select_related('user')

    html = render_to_string('reports/weekly.html', {
        'team': team,
        'date': today,
        'week_start': week_start,
        'week_end': week_end,
        'generated_by': generated_by,
        'stats': stats,
        'projects': projects,
        'members': members,
    })
    return HTML(string=html).write_pdf()
