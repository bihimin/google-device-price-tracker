"""Market configuration for the 5 tracked geographies."""

MARKETS = {
    "jp": {
        "name": "Japan",
        "google_store": True,
        "locale": "ja",
        "currency": "JPY",
        "store_path": "jp",
        "store_hl": "ja",
    },
    "in": {
        "name": "India",
        "google_store": True,
        "locale": "en-IN",
        "currency": "INR",
        "store_path": "in",
        "store_hl": "en-IN",
    },
    "au": {
        "name": "Australia",
        "google_store": True,
        "locale": "en-GB",
        "currency": "AUD",
        "store_path": "au",
        "store_hl": "en-GB",
    },
    "sg": {
        "name": "Singapore",
        "google_store": True,
        "locale": "en-GB",
        "currency": "SGD",
        "store_path": "sg",
        "store_hl": "en-GB",
    },
    "tw": {
        "name": "Taiwan",
        "google_store": True,
        "locale": "zh-TW",
        "currency": "TWD",
        "store_path": "tw",
        "store_hl": "zh-TW",
    },
}

# Google Store category pages used to discover the live catalog of product
# slugs for a market. Each category page's HTML links to config/product
# pages across the whole catalog (phones, watches, earbuds, home), so a
# small set of seed pages is enough to harvest nearly everything.
CATEGORY_SEED_PATHS = [
    "category/phones",
    "category/watches_trackers",
    "category/earbuds",
    "category/connected_home",
]

# Known non-phone product slugs we additionally probe directly, in case a
# market's category pages don't link every discontinued/legacy SKU that is
# still purchasable.
EXTRA_PRODUCT_SLUGS = [
    "pixel_watch_4",
    "pixel_watch_3",
    "fitbit_charge_6",
    "fitbit_inspire_3",
    "fitbit_versa_4",
    "fitbit_sense_2",
    "fitbit_ace_3",
    "fitbit_luxe",
    "google_fitbit_edge",
    "google_fitbit_air",
    "pixel_buds_pro_2",
    "pixel_buds_2a",
    "pixel_buds_a_series",
    "google_home_speaker",
    "nest_cam_outdoor_wired_2nd_gen",
    "nest_cam_indoor_wired_3rd_gen",
    "nest_cam_indoor",
    "nest_cam_battery",
    "nest_cam_floodlight",
    "nest_doorbell_battery",
    "nest_wifi_pro",
    "nest_hub_2nd_gen",
    "google_nest_hub_max",
    "google_tv_streamer",
    "pixel_tablet",
]

# Slugs that are phone configurators (schema.org Product block contains one
# <AggregateOffer> per colour+storage combo) vs. plain product pages (one
# aggregate "from" price). Anything not listed here defaults to "product".
CONFIG_SLUGS = {
    "pixel_11", "pixel_11_pro", "pixel_11_pro_fold",
    "pixel_10", "pixel_10_pro", "pixel_10_pro_fold", "pixel_10a",
    "pixel_9", "pixel_9_pro", "pixel_9_pro_xl", "pixel_9_pro_fold", "pixel_9a",
    "pixel_tablet",
}
