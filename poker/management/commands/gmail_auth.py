from django.core.management.base import BaseCommand

from poker import gmail


class Command(BaseCommand):
    help = "One-time Gmail sign-in: opens a browser for consent and stores the token in secrets/."

    def handle(self, *args, **opts):
        creds = gmail.load_credentials(interactive=True)
        profile = gmail.build_service(creds).users().getProfile(userId="me").execute()
        self.stdout.write(
            f"Authorized as {profile['emailAddress']} ({profile['messagesTotal']} messages)"
        )
