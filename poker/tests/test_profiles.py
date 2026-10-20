import pytest

from poker.state import _classify, build_profiles
from poker.models import PlayerProfile

pytestmark = pytest.mark.django_db


class TestClassify:
    """Tests for the player classification heuristic."""

    def test_few_hands_no_classification(self):
        assert _classify(1, 0.8, 0.1) == ""
        assert _classify(2, 0.9, 0.9) == ""

    def test_rock(self):
        """Tight + passive = rock."""
        assert _classify(10, 0.75, 0.2) == "rock"

    def test_maniac(self):
        """Loose + aggressive = maniac."""
        assert _classify(10, 0.30, 0.60) == "maniac"

    def test_calling_station(self):
        """Loose + passive = calling station."""
        assert _classify(10, 0.35, 0.25) == "calling station"

    def test_aggressive(self):
        """Tight + aggressive = aggressive."""
        assert _classify(10, 0.72, 0.55) == "aggressive"

    def test_loose(self):
        """Loose + neutral = loose."""
        assert _classify(10, 0.38, 0.40) == "loose"

    def test_tight(self):
        """Tight + neutral = tight."""
        assert _classify(10, 0.71, 0.40) == "tight"

    def test_no_actions(self):
        """No actions yields neutral classification."""
        assert _classify(5, 0.5, 0.5) == ""  # hands_played < 3

    def test_edge_fold_rate_tight(self):
        assert _classify(10, 0.70, 0.29) == "rock"  # tight + passive (aggr < 0.30)
        assert _classify(10, 0.69, 0.30) == ""  # below threshold, neutral

    def test_edge_fold_rate_loose(self):
        assert _classify(10, 0.40, 0.50) == ""  # exactly 0.40 is not loose
        assert _classify(10, 0.39, 0.50) == "maniac"  # loose + aggressive = maniac
        assert _classify(10, 0.39, 0.40) == "loose"  # loose + neutral


class TestBuildProfiles:
    """Integration test: build_profiles correctly aggregates and classifies."""

    def test_basic_aggregation(self, imported, client):
        from poker.models import Hand, PlayerProfile, Table

        table = Table.objects.get(name="b17")
        n = build_profiles(table.name)

        # There are multiple hands with actions from several players
        assert n > 0

        # Check that at least one profile exists
        profiles = PlayerProfile.objects.filter(table=table)
        assert profiles.count() > 0

        # Verify a known player has a profile
        names = set(p.player.name for p in profiles)
        assert "sefk" in names
        assert "Capparella" in names

    def test_classification_applied(self, imported, client):
        from poker.models import Hand, PlayerProfile, Table

        build_profiles("b17")

        for p in PlayerProfile.objects.filter(table__name="b17"):
            # If hands_played >= 3, classification should be set
            if p.hands_played >= 3:
                assert p.classification != "", f"{p.player.name} should have a classification"

    def test_classification_values(self, imported, client):
        from poker.models import Hand, PlayerProfile, Table

        build_profiles("b17")

        valid = {"", "tight", "loose", "passive", "aggressive",
                 "calling station", "rock", "maniac"}
        for p in PlayerProfile.objects.filter(table__name="b17"):
            assert p.classification in valid, f"{p.player.name}: {p.classification}"


class TestProfilePages:
    def test_legend_only_on_profile_page(self, imported, client):
        for url in ("/", "/hands/"):
            assert b"Player badges" not in client.get(url).content

    def test_badge_links_to_profile(self, imported, client):
        from django.urls import reverse

        build_profiles("b17")
        p = PlayerProfile.objects.filter(table__name="b17").exclude(classification="").first()
        if p is None:
            pytest.skip("no classified player in fixtures")
        r = client.get(reverse("poker:hand", args=["b17", 2]))
        assert b"/profiles/b17/" in r.content

    def test_profile_page(self, imported, client):
        from django.urls import reverse

        build_profiles("b17")
        r = client.get(reverse("poker:player_profile", args=["b17", "sefk"]))
        assert r.status_code == 200
        assert b"Fold rate" in r.content
        assert b"History" in r.content
        assert b"Player badges" in r.content
        assert b"Folds at least 70%" in r.content

    def test_unknown_profile_404(self, imported, client):
        assert client.get("/profiles/b17/nobody/").status_code == 404
