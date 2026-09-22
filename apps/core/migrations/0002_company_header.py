from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversionjob',
            name='company_name',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name="Nom de l'entreprise"),
        ),
        migrations.AddField(
            model_name='conversionjob',
            name='excel_filename',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Nom du fichier Excel'),
        ),
        migrations.AddField(
            model_name='conversionjob',
            name='company_logo',
            field=models.FileField(blank=True, null=True, upload_to='uploads/logos/', verbose_name='Logo entreprise'),
        ),
    ]
