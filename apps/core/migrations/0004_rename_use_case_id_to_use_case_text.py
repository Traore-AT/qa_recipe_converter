# Generated manually
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_conversionjob_project_conversionjob_uploaded_by'),
    ]

    operations = [
        migrations.RenameField(
            model_name='extractedusecase',
            old_name='use_case_id',
            new_name='use_case_text',
        ),
    ]
