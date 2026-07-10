"""Unit tests for the OSM POI builder's pure logic (taxonomy, naming, filters).

No PBFs, no osmium runtime — classification and row-shaping only. The taxonomy
*content* is a reviewed data table, not asserted wholesale; these tests pin the
mechanics: key priority, junk fallthrough, the exclusion floor, selective keys,
multi-values, name/summary fallbacks, and filter-expression derivation.
"""

from __future__ import annotations

import argparse

import pytest

from tools.build_osm_pois import (
    BASEMAP_BBOX,
    EXCLUDED_VALUES,
    JUNK_VALUES,
    REFINERS,
    TAXONOMY,
    build_summary,
    classify,
    display_name,
    filter_expressions,
    filtered_path,
    humanize,
    parse_bbox,
)


class TestClassify:
    def test_simple_amenity(self) -> None:
        assert classify({'amenity': 'cafe'}) == ('amenity=cafe', 'food_drink', 3)

    def test_key_priority_tourism_beats_amenity(self) -> None:
        result = classify({'amenity': 'restaurant', 'tourism': 'hotel'})
        assert result is not None
        assert result[:2] == ('tourism=hotel', 'lodging')

    def test_key_level_default_applies(self) -> None:
        result = classify({'shop': 'surfboard'})
        assert result == ('shop=surfboard', *TAXONOMY['shop']['*'])

    def test_selective_key_miss_classifies_nothing(self) -> None:
        assert classify({'natural': 'tree'}) is None

    def test_selective_key_hit(self) -> None:
        assert classify({'natural': 'peak'}) == ('natural=peak', 'outdoors', 2)

    def test_excluded_value_drops_without_fallthrough(self) -> None:
        assert classify({'amenity': 'parking_space'}) is None
        assert classify({'amenity': 'parking_space', 'shop': 'bakery'}) is None

    def test_junk_value_falls_through_to_next_key(self) -> None:
        result = classify({'tourism': 'yes', 'amenity': 'fuel'})
        assert result is not None
        assert result[0] == 'amenity=fuel'

    def test_multi_value_uses_first_entry(self) -> None:
        result = classify({'amenity': 'restaurant;cafe'})
        assert result is not None
        assert result[:2] == ('amenity=restaurant', 'food_drink')

    def test_no_taxonomy_key_present(self) -> None:
        assert classify({'building': 'yes', 'height': '12'}) is None

    def test_micro_furniture_is_rank_5(self) -> None:
        result = classify({'amenity': 'bench'})
        assert result is not None
        assert result[2] == 5

    def test_international_aerodrome_is_rank_1(self) -> None:
        tags = {'aeroway': 'aerodrome', 'aerodrome': 'international', 'iata': 'DEN'}
        assert classify(tags) == ('aeroway=aerodrome', 'transport', 1)
        tags = {'aeroway': 'aerodrome', 'aerodrome:type': 'international'}
        assert classify(tags) == ('aeroway=aerodrome', 'transport', 1)

    def test_iata_aerodrome_promotes_to_rank_2(self) -> None:
        tags = {'aeroway': 'aerodrome', 'name': 'Telluride Regional', 'iata': 'TEX'}
        assert classify(tags) == ('aeroway=aerodrome', 'transport', 2)

    def test_plain_aerodrome_stays_minor(self) -> None:
        result = classify({'aeroway': 'aerodrome', 'name': 'Mile High RC Field'})
        assert result == ('aeroway=aerodrome', 'transport', 3)

    def test_named_lake_classifies(self) -> None:
        tags = {'natural': 'water', 'water': 'lake', 'name': 'Grand Lake'}
        assert classify(tags) == ('water=lake', 'outdoors', 2)

    def test_unnamed_lake_drops(self) -> None:
        assert classify({'natural': 'water', 'water': 'lake'}) is None
        assert classify({'natural': 'water', 'water': 'reservoir', 'name': ' '}) is None


class TestTaxonomyTable:
    def test_ranks_are_1_to_5(self) -> None:
        for key, table in TAXONOMY.items():
            for value, (category, rank) in table.items():
                assert 1 <= rank <= 5, f'{key}={value} rank {rank}'
                assert category, f'{key}={value} has empty category'

    def test_excluded_values_target_real_keys(self) -> None:
        assert set(EXCLUDED_VALUES) <= set(TAXONOMY)

    def test_junk_values_never_in_taxonomy(self) -> None:
        for key, table in TAXONOMY.items():
            assert not JUNK_VALUES & set(table), f'junk value listed under {key}'

    def test_refiners_target_real_kinds(self) -> None:
        for kind in REFINERS:
            key, _, value = kind.partition('=')
            assert value in TAXONOMY.get(key, {}), f'refiner on unlisted kind {kind}'


class TestFilterExpressions:
    def test_wholesale_key(self) -> None:
        assert 'nwr/amenity' in filter_expressions()

    def test_selective_key_lists_only_its_values(self) -> None:
        expr = next(e for e in filter_expressions() if e.startswith('nwr/natural='))
        values = set(expr.removeprefix('nwr/natural=').split(','))
        assert values == set(TAXONOMY['natural'])
        assert 'tree' not in values

    def test_one_expression_per_key(self) -> None:
        assert len(filter_expressions()) == len(TAXONOMY)

    def test_water_filters_on_companion_key_only(self) -> None:
        expr = next(e for e in filter_expressions() if e.startswith('nwr/water='))
        assert set(expr.removeprefix('nwr/water=').split(',')) == set(TAXONOMY['water'])
        assert 'nwr/natural=water' not in filter_expressions()


class TestNaming:
    def test_humanize(self) -> None:
        assert humanize('fast_food') == 'Fast food'

    def test_name_tag_wins(self) -> None:
        assert display_name({'name': 'Moe’s', 'brand': 'Chain'}, 'cafe') == 'Moe’s'

    def test_brand_fallback(self) -> None:
        assert display_name({'brand': 'Shell'}, 'fuel') == 'Shell'

    def test_humanized_value_fallback(self) -> None:
        assert display_name({}, 'drinking_water') == 'Drinking water'

    def test_blank_name_falls_through(self) -> None:
        assert display_name({'name': '  ', 'operator': 'NPS'}, 'toilets') == 'NPS'

    def test_summary_kind_cuisine_brand(self) -> None:
        tags = {'cuisine': 'burger;fries', 'brand': 'Five Guys'}
        assert build_summary(tags, 'amenity=fast_food', 'Store #12') == (
            'Fast food · Burger, fries · Five Guys'
        )

    def test_summary_skips_brand_equal_to_name(self) -> None:
        assert build_summary({'brand': 'Shell'}, 'amenity=fuel', 'Shell') == 'Fuel'


class TestCli:
    def test_parse_bbox_roundtrip(self) -> None:
        assert parse_bbox('-168,7,-52,72') == BASEMAP_BBOX

    def test_parse_bbox_rejects_bad_arity(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            parse_bbox('1,2,3')

    def test_filtered_path_stable_and_taxonomy_hashed(self, tmp_path) -> None:
        src = tmp_path / 'colorado-latest.osm.pbf'
        first = filtered_path(src)
        assert first == filtered_path(src)
        assert first.name.startswith('colorado-latest.pois-')
        assert first.name.endswith('.osm.pbf')
