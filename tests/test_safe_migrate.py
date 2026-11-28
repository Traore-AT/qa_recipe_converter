"""Migrations automatiques au démarrage : jamais bloquantes, sans effet si déjà appliquées."""
import importlib.util
import io
import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.management.commands import safe_migrate

pytestmark = pytest.mark.django_db


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command('safe_migrate', *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


@contextmanager
def lock(acquired):
    yield acquired


class TestSafeMigrate:
    def test_up_to_date_database_does_nothing(self):
        with mock.patch.object(safe_migrate, 'call_command') as migrate:
            out, _ = run()
        assert 'base à jour' in out
        migrate.assert_not_called()

    def test_pending_migrations_are_applied(self):
        with mock.patch.object(safe_migrate, 'pending_migrations', return_value=['m']), \
                mock.patch.object(safe_migrate, 'call_command') as migrate:
            out, _ = run()
        migrate.assert_called_once_with('migrate', database='default', interactive=False, verbosity=1)
        assert '1 à appliquer' in out and 'appliquées' in out

    def test_migrations_applied_meanwhile_by_another_instance(self):
        with mock.patch.object(safe_migrate, 'pending_migrations', side_effect=[['m'], []]), \
                mock.patch.object(safe_migrate, 'call_command') as migrate:
            out, _ = run()
        migrate.assert_not_called()
        assert 'déjà appliquées par une autre instance' in out

    def test_lock_held_elsewhere_is_skipped(self):
        with mock.patch.object(safe_migrate, 'pending_migrations', return_value=['m']), \
                mock.patch.object(safe_migrate, 'migration_lock', return_value=lock(False)), \
                mock.patch.object(safe_migrate, 'call_command') as migrate:
            out, _ = run('--lock-timeout', '1')
        migrate.assert_not_called()
        assert 'étape ignorée' in out

    def test_failure_never_raises_by_default(self):
        with mock.patch.object(safe_migrate, 'pending_migrations', return_value=['m']), \
                mock.patch.object(safe_migrate, 'call_command', side_effect=RuntimeError('colonne en double')):
            _, err = run()
        assert 'échec (colonne en double)' in err and 'démarre quand même' in err

    def test_database_unreachable_never_raises(self):
        with mock.patch.object(safe_migrate, 'pending_migrations', side_effect=OSError('connexion refusée')):
            _, err = run()
        assert 'connexion refusée' in err

    def test_strict_mode_fails(self):
        with mock.patch.object(safe_migrate, 'pending_migrations', return_value=['m']), \
                mock.patch.object(safe_migrate, 'call_command', side_effect=RuntimeError('boom')):
            with pytest.raises(CommandError, match='boom'):
                run('--strict')

    def test_lock_is_a_no_op_outside_postgresql(self):
        connection = mock.Mock(vendor='sqlite')
        with safe_migrate.migration_lock(connection, 1) as acquired:
            assert acquired is True
        connection.cursor.assert_not_called()

    def test_postgresql_lock_is_released_or_times_out(self):
        cursor = mock.MagicMock()
        connection = mock.MagicMock(vendor='postgresql')
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = (True,)
        with safe_migrate.migration_lock(connection, 1) as acquired:
            assert acquired is True
        assert cursor.execute.call_args_list[-1].args[0] == 'SELECT pg_advisory_unlock(%s)'
        cursor.reset_mock()
        cursor.fetchone.return_value = (False,)
        with mock.patch.object(safe_migrate.time, 'sleep'):
            with safe_migrate.migration_lock(connection, 0) as acquired:
                assert acquired is False
        assert all('unlock' not in c.args[0] for c in cursor.execute.call_args_list)


# ─────────────────────────────────────────────────────────────────────────────
# Démarrage Gunicorn
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def gunicorn_conf():
    path = Path(__file__).resolve().parent.parent / 'gunicorn.conf.py'
    spec = importlib.util.spec_from_file_location('gunicorn_conf_under_test', path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch('builtins.print'):
        spec.loader.exec_module(module)
    return module


class TestGunicornStartup:
    def test_runs_safe_migrate_once_at_start(self, gunicorn_conf, monkeypatch):
        monkeypatch.delenv('AUTO_MIGRATE', raising=False)
        log = mock.Mock()
        done = subprocess.CompletedProcess([], 0, stdout='Migrations : base à jour, rien à appliquer.\n', stderr='')
        with mock.patch('subprocess.run', return_value=done) as run_cmd:
            gunicorn_conf.on_starting(mock.Mock(log=log))
        command = run_cmd.call_args.args[0]
        assert command[1].endswith('manage.py') and command[2] == 'safe_migrate'
        assert run_cmd.call_args.kwargs['timeout'] == 180
        log.info.assert_any_call('Migrations : base à jour, rien à appliquer.')

    @pytest.mark.parametrize('value', ['0', 'false', 'OFF'])
    def test_can_be_disabled(self, gunicorn_conf, monkeypatch, value):
        monkeypatch.setenv('AUTO_MIGRATE', value)
        with mock.patch('subprocess.run') as run_cmd:
            gunicorn_conf.run_startup_migrations(mock.Mock())
        run_cmd.assert_not_called()

    def test_timeout_does_not_block_startup(self, gunicorn_conf, monkeypatch):
        monkeypatch.setenv('AUTO_MIGRATE_TIMEOUT', '20')
        log = mock.Mock()
        with mock.patch('subprocess.run', side_effect=subprocess.TimeoutExpired('x', 20)) as run_cmd:
            gunicorn_conf.run_startup_migrations(log)
        assert run_cmd.call_args.args[0][-1] == '10'  # le verrou expire avant le délai global
        assert 'délai de 20s dépassé' in log.warning.call_args.args[0]

    def test_errors_never_stop_the_server(self, gunicorn_conf):
        log = mock.Mock()
        with mock.patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, stdout='', stderr='échec')):
            gunicorn_conf.run_startup_migrations(log)
        assert 'code 1' in log.warning.call_args.args[0]
        with mock.patch('subprocess.run', side_effect=OSError('python introuvable')):
            gunicorn_conf.run_startup_migrations(log)
        assert 'python introuvable' in log.warning.call_args.args[0]
