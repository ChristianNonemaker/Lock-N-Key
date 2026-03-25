"""
Tests for team name normalization and odds math.
"""

from dk_ncaab.etl.normalize import normalize_team_name, american_to_implied, remove_vig


class TestNormalize:
    def test_basic_lowercase(self):
        assert normalize_team_name("Duke") == "duke"

    def test_strip_punctuation(self):
        assert normalize_team_name("N.C. State") == "nc state"

    def test_expand_st_to_state(self):
        assert normalize_team_name("Ohio St") == "ohio state"

    def test_expand_mt(self):
        assert normalize_team_name("Mt. St. Mary's") == "mount state marys"

    def test_ampersand(self):
        assert normalize_team_name("Texas A&M") == "texas a and m"

    def test_directional(self):
        assert normalize_team_name("SE Missouri") == "southeastern missouri"
        assert normalize_team_name("NW State") == "northwestern state"

    def test_unicode(self):
        assert normalize_team_name("São Paulo") == "sao paulo"

    def test_extra_whitespace(self):
        assert normalize_team_name("  Kansas   Jayhawks  ") == "kansas jayhawks"

    def test_idempotent(self):
        raw = "Gonzaga Bulldogs"
        assert normalize_team_name(normalize_team_name(raw)) == normalize_team_name(raw)


class TestAmericanToImplied:
    def test_negative_odds(self):
        # -110 → 110/210 ≈ 0.5238
        result = american_to_implied(-110)
        assert abs(result - 0.5238) < 0.001

    def test_positive_odds(self):
        # +150 → 100/250 = 0.4
        result = american_to_implied(150)
        assert abs(result - 0.4) < 0.001

    def test_heavy_favorite(self):
        # -300 → 300/400 = 0.75
        result = american_to_implied(-300)
        assert abs(result - 0.75) < 0.001

    def test_even_money(self):
        # +100 → 100/200 = 0.5
        result = american_to_implied(100)
        assert abs(result - 0.5) < 0.001


class TestRemoveVig:
    def test_standard_vig(self):
        # -110/-110 → each implied ~0.5238, total ~1.0476
        p_a = american_to_implied(-110)
        p_b = american_to_implied(-110)
        fair_a, fair_b = remove_vig(p_a, p_b)
        assert abs(fair_a - 0.5) < 0.001
        assert abs(fair_b - 0.5) < 0.001
        assert abs(fair_a + fair_b - 1.0) < 0.001

    def test_asymmetric(self):
        p_a = american_to_implied(-200)  # 0.6667
        p_b = american_to_implied(170)   # 0.3704
        fair_a, fair_b = remove_vig(p_a, p_b)
        assert abs(fair_a + fair_b - 1.0) < 0.001
        assert fair_a > fair_b
