"""
Email utilities for Teams app — welcome, invitation, sprint notifications.
"""
import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def send_welcome_email(user):
    subject = "Bienvenue sur QA Recipe Converter"
    html_body = render_to_string("emails/welcome.html", {
        "user": user,
        "login_url": f"{settings.SITE_URL}{reverse('web-login')}",
        "site_url": settings.SITE_URL,
    })
    text_body = f"""
Bonjour {user.first_name or user.username},

Votre compte a été créé avec succès sur QA Recipe Converter.

Connectez-vous dès maintenant : {settings.SITE_URL}{reverse('web-login')}

À très bientôt sur la plateforme !
"""
    _send_email(subject, text_body, html_body, [user.email])


def send_invitation_email(invitation):
    team = invitation.team
    invited_by = invitation.invited_by
    accept_url = f"{settings.SITE_URL}/invitation/{invitation.token}/"
    reject_url = f"{settings.SITE_URL}/invitation/{invitation.token}/"

    projects = team.projects.all()
    projects_count = projects.count()
    members_count = team.members.count()
    projects_names = [p.name for p in projects[:5]]
    projects_list = ", ".join(projects_names)
    if projects_count > 5:
        projects_list += f" et {projects_count - 5} autre(s)"

    role_label = dict(team.members.model.Role.choices).get(invitation.role, invitation.role)

    subject = f"Invitation à rejoindre l'équipe {team.name}"
    html_body = render_to_string("emails/invitation.html", {
        "invited_by_name": invited_by.get_full_name() or invited_by.username,
        "team_name": team.name,
        "role_label": role_label,
        "projects_count": projects_count,
        "members_count": members_count,
        "projects_list": projects_list,
        "accept_url": accept_url,
        "reject_url": reject_url,
        "expires_at": timezone.localtime(invitation.expires_at).strftime("%d/%m/%Y à %H:%M"),
        "site_url": settings.SITE_URL,
    })
    text_body = f"""
Bonjour,

{invited_by.get_full_name() or invited_by.username} vous invite à rejoindre l'équipe "{team.name}" sur QA Recipe Converter.

Rôle proposé : {role_label}
Projets : {projects_list}
Membres : {members_count}

Accepter : {accept_url}
Refuser : {reject_url}

Cette invitation expire le {timezone.localtime(invitation.expires_at).strftime("%d/%m/%Y à %H:%M")}.
"""
    _send_email(subject, text_body, html_body, [invitation.email])


def send_sprint_assignment_email(assignment, sprint, assigned_by):
    member = assignment.assigned_to
    use_case = assignment.use_case
    project = sprint.project
    subject = f"[{project.name}] Nouveau cas assigné — Sprint {sprint.name}"
    board_url = f"{settings.SITE_URL}/teams/{project.team.slug}/projects/{project.slug}/sprints/{sprint.id}"
    html_body = render_to_string("emails/sprint_assignment.html", {
        "member_name": member.user.get_full_name() or member.user.username,
        "sprint_name": sprint.name,
        "project_name": project.name,
        "team_name": project.team.name,
        "assigned_by_name": assigned_by.get_full_name() or assigned_by.username,
        "use_case_order": use_case.order,
        "use_case_text": use_case.use_case_text,
        "use_case_description": use_case.description,
        "board_url": board_url,
        "site_url": settings.SITE_URL,
    })
    text_body = f"""
Bonjour {member.user.get_full_name() or member.user.username},

{assigned_by.get_full_name() or assigned_by.username} vous a assigné un cas de test dans le sprint "{sprint.name}" du projet "{project.name}".

Cas #{use_case.order} — {use_case.use_case_text or use_case.description[:50]}

Accédez au tableau Kanban : {board_url}
"""
    _send_email(subject, text_body, html_body, [member.user.email])


def send_member_completed_email(member, sprint, project):
    subject = f"[{project.name}] {member.user.username} a terminé son sprint {sprint.name}"
    project_url = f"{settings.SITE_URL}/teams/{project.team.slug}/projects/{project.slug}"
    html_body = render_to_string("emails/member_completed.html", {
        "member_name": member.user.get_full_name() or member.user.username,
        "sprint_name": sprint.name,
        "project_name": project.name,
        "team_name": project.team.name,
        "project_url": project_url,
        "site_url": settings.SITE_URL,
    })
    text_body = f"""
Bonjour,

{member.user.get_full_name() or member.user.username} a terminé tous les cas qui lui étaient assignés dans le sprint "{sprint.name}" du projet "{project.name}".

Accédez au projet : {project_url}
"""
    leads = project.members.filter(role='lead').select_related('user')
    lead_emails = [pm.user.email for pm in leads if pm.user.email]
    if lead_emails:
        _send_email(subject, text_body, html_body, lead_emails)


def send_password_reset_email(user):
    from django.contrib.auth.tokens import PasswordResetTokenGenerator
    from django.utils.http import urlsafe_base64_encode
    from django.utils.encoding import force_bytes

    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = PasswordResetTokenGenerator().make_token(user)
    reset_url = f"{settings.SITE_URL}/reset-password/{uidb64}/{token}/"

    subject = "Réinitialisation de votre mot de passe - QA Recipe Converter"
    html_body = render_to_string("emails/password_reset.html", {
        "user": user,
        "reset_url": reset_url,
        "site_url": settings.SITE_URL,
        "valid_hours": 24,
    })
    text_body = f"""
Bonjour {user.first_name or user.username},

Vous avez demandé la réinitialisation de votre mot de passe sur QA Recipe Converter.

Pour définir un nouveau mot de passe, cliquez sur le lien suivant :
{reset_url}

Si vous n'êtes pas à l'origine de cette demande, ignorez simplement cet email.

Ce lien expirera dans 24 heures.
"""
    _send_email(subject, text_body, html_body, [user.email])


def _send_email(subject, text_body, html_body, to_emails):
    try:
        msg = EmailMultiAlternatives(subject, text_body, settings.DEFAULT_FROM_EMAIL, to_emails)
        msg.attach_alternative(html_body, "text/html")
        msg.send()
        logger.info("Email sent to %s: %s", ", ".join(to_emails), subject)
    except Exception as e:
        logger.warning("Failed to send email to %s: %s", ", ".join(to_emails), e)
