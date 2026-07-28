"""Tests for the NWS ImageServer client's pure request builders + parser.

The catalog parser encodes the P0 finding that filtering ``idp_subset`` yields
one raster per cycle: it dedupes, sorts newest-first, and defensively drops any
other subset that slips through.
"""

import pytest

from weather import source


def test_catalog_params_filters_subset():
    params = source.catalog_params('CONUS')
    assert params['where'] == "idp_subset='CONUS'"
    assert 'idp_validtime' in params['outFields']
    assert params['orderByFields'] == 'idp_validtime DESC'
    assert params['f'] == 'json'


def test_export_params_shape():
    params = source.export_params((-1.0, -2.0, 3.0, 4.0), (800, 600), 1785267488000)
    assert params['bboxSR'] == '3857'
    assert params['imageSR'] == '3857'
    assert params['size'] == '800,600'
    assert params['format'] == 'png32'
    assert params['transparent'] == 'true'
    assert params['time'] == '1785267488000'
    assert params['f'] == 'image'
    # bbox is the four bounds, comma-joined.
    assert params['bbox'].count(',') == 3


def _feature(validtime, subset='CONUS'):
    return {'attributes': {'idp_validtime': validtime, 'idp_subset': subset}}


def test_parse_frames_sorts_desc_and_dedupes():
    payload = {'features': [_feature(100), _feature(300), _feature(200), _feature(300)]}
    assert source.parse_frames(payload, 'CONUS') == [300, 200, 100]


def test_parse_frames_drops_other_subsets():
    payload = {'features': [_feature(100, 'CONUS'), _feature(200, 'CARIB')]}
    assert source.parse_frames(payload, 'CONUS') == [100]


def test_parse_frames_empty():
    assert source.parse_frames({'features': []}, 'CONUS') == []


def test_parse_frames_raises_on_error():
    with pytest.raises(source.SourceError):
        source.parse_frames({'error': {'code': 400, 'message': 'bad'}}, 'CONUS')
