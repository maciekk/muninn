from decimal import Decimal

import pytest

from muninn.extract import extract_price

URL = 'https://www.newegg.ca/p/N82E16820250244'


@pytest.mark.parametrize('container', ['product-price', 'product-price--x'])
@pytest.mark.parametrize('price_class', ['price-current', 'price-current_2026'])
def test_main_product_price_not_other_sellers_or_variants(container, price_class):
    html = f'''
    <div class="seller-actions"><li class="price-current">$<strong>949</strong><sup>.99</sup></li></div>
    <div class="{container}"><ul class="price">
      <div class="{price_class}">$<strong>899</strong><sup>.99</sup></div>
      <li class="price-note"><span class="price-current"></span></li>
    </ul></div>
    <table><tr><td><li class="price-current">$369.99</li></td></tr></table>
    <meta itemprop="price" content="1554.97">
    <script type="application/ld+json">{{"offers":{{"price":"1259.99"}}}}</script>
    '''
    assert extract_price(html, URL) == Decimal('899.99')


@pytest.mark.parametrize('main', ['', '<div class="product-price"><span class="price-current"></span></div>'])
def test_missing_main_price_does_not_fall_back_to_other_offers(main):
    html = main + '''
    <li class="price-current">$949.99</li>
    <meta itemprop="price" content="1554.97">
    <script type="application/ld+json">{"offers":{"price":"1259.99"}}</script>
    '''
    assert extract_price(html, URL) is None


def test_custom_selector_is_authoritative_and_preserves_cents():
    html = '<span class="chosen">$<strong>1,299</strong><sup>.99</sup></span>'
    assert extract_price(html, URL, '.chosen') == Decimal('1299.99')
    assert extract_price(html, URL, '.missing') is None
