from django.db import migrations

RENAMES = {"calling station": "caller", "aggressive": "agro"}


def forwards(apps, schema_editor):
    PlayerProfile = apps.get_model("poker", "PlayerProfile")
    for old, new in RENAMES.items():
        PlayerProfile.objects.filter(classification=old).update(classification=new)


def backwards(apps, schema_editor):
    PlayerProfile = apps.get_model("poker", "PlayerProfile")
    for old, new in RENAMES.items():
        PlayerProfile.objects.filter(classification=new).update(classification=old)


class Migration(migrations.Migration):
    dependencies = [("poker", "0008_add_playerprofile")]
    operations = [migrations.RunPython(forwards, backwards)]
