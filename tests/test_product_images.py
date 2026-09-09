from __future__ import annotations

import pytest

from cd_monitor.core.product_images import (
    is_usable_product_image,
    normalize_product_image_url,
)


@pytest.mark.parametrize(
    "image_url",
    [
        "https://imgoss.mokaki.cn/ossdoorzo/web/img_bg_wmj.png",
        "https://imgoss.mokaki.cn/sigimage/icon/searchList_mercari.png!compress",
        "https://sig-image.oss-cn-beijing.aliyuncs.com/store/headDefaultMercari.png",
        "https://img.alicdn.com/imgextra/i4/example-2-tps-2-2.png",
        "https://image02.doorzo.net/imgrakutencojp/hmvjapan/cabinet/meyasu.gif",
        "https://imgoss.mokaki.cn/ossimg/65cca9650d72043406a3b17c5e36a619.png!compress",
        "https://imghk02.doorzo.net/tshopr10sjp/renet3/cabinet/item_photo/img_noimage.jpg?fitin=600:600",
    ],
)
def test_wameiji_site_chrome_is_not_a_usable_product_image(image_url: str) -> None:
    assert not is_usable_product_image(image_url)


def test_real_marketplace_image_remains_usable() -> None:
    assert is_usable_product_image("https://static.mercdn.net/item/detail/orig/photos/m123.jpg")


@pytest.mark.parametrize(
    ("proxy_url", "rakuten_url"),
    [
        (
            "https://image02.doorzo.net/tshopr10sjp/renet3/cabinet/ccc67/0011000791.jpg?fitin=600:600",
            "https://thumbnail.image.rakuten.co.jp/@0_mall/renet3/cabinet/ccc67/0011000791.jpg?_ex=600x600",
        ),
        (
            "https://imghk.doorzo.net/tshopr10sjp/hmvjapan/cabinet/a65/18000/16516226.jpg?fitin=600:600",
            "https://thumbnail.image.rakuten.co.jp/@0_mall/hmvjapan/cabinet/a65/18000/16516226.jpg?_ex=600x600",
        ),
    ],
)
def test_doorzo_rakuten_proxy_image_uses_public_rakuten_cdn(
    proxy_url: str, rakuten_url: str
) -> None:
    assert normalize_product_image_url(proxy_url) == rakuten_url


def test_doorzo_mercari_shops_proxy_uses_public_mercari_cdn() -> None:
    proxy_url = (
        "https://imghk.doorzo.net/-/large/plain/"
        "2JVY4CiUVnPDjoeyeLhXrz.jpg@jpg"
    )

    assert normalize_product_image_url(proxy_url) == (
        "https://assets.mercari-shops-static.com/-/large/plain/"
        "2JVY4CiUVnPDjoeyeLhXrz.jpg@jpg"
    )
