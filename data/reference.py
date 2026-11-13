"""Reference ("master") data shared by the generator and the cleaner.

In a real company this would live in reference tables. The cleaner uses it to map
messy spellings to canonical values and to fill gaps (e.g. sector from subsector,
country from city).
"""

# sector -> subsector -> the asset type a company in that subsector owns
SECTORS = {
    "energy": {
        "unit": "MW",
        "subsectors": {
            "Solar": "Solar farm",
            "Onshore wind": "Wind farm",
            "Offshore wind": "Offshore wind farm",
            "Battery storage": "Battery storage facility",
        },
        "name_words": ["Solar", "Wind", "Power", "Renewables", "Energy", "Grid"],
    },
    "data_centers": {
        "unit": "MW",
        "subsectors": {
            "Hyperscale": "Data center campus",
            "Colocation": "Colocation data center",
            "Edge": "Edge data center",
        },
        "name_words": ["Data", "Compute", "Cloud", "Digital", "Datacenters"],
    },
    "fiber": {
        "unit": "km",
        "subsectors": {
            "FTTH": "FTTH network",
            "Long-haul": "Long-haul fiber route",
            "Metro": "Metro fiber ring",
        },
        "name_words": ["Fiber", "Fibre", "Connect", "Networks", "Broadband"],
    },
    "toll_roads": {
        "unit": "km",
        "subsectors": {
            "Toll road": "Toll road",
            "Bridge & tunnel": "Toll bridge",
        },
        "name_words": ["Motorways", "Roads", "Highways", "Tollways", "Expressway"],
    },
    "water": {
        "unit": "ML/day",
        "subsectors": {
            "Water treatment": "Water treatment plant",
            "Desalination": "Desalination plant",
            "Wastewater": "Wastewater treatment plant",
        },
        "name_words": ["Water", "Aqua", "Water Services", "Utilities"],
    },
}

SUBSECTOR_TO_SECTOR = {
    sub.lower(): sector for sector, info in SECTORS.items() for sub in info["subsectors"]
}
ASSET_TYPE_TO_UNIT = {
    asset_type.lower(): info["unit"]
    for info in SECTORS.values()
    for asset_type in info["subsectors"].values()
}

# Messy spellings seen in source files -> canonical sector
SECTOR_ALIASES = {
    "energy": "energy", "power": "energy", "power & energy": "energy",
    "renewables": "energy", "renewable energy": "energy",
    "data center": "data_centers", "data centers": "data_centers",
    "data centre": "data_centers", "data centres": "data_centers",
    "datacenter": "data_centers", "datacenters": "data_centers",
    "data_centers": "data_centers",
    "fiber": "fiber", "fibre": "fiber", "fiber optic": "fiber", "fibre optic": "fiber",
    "telecom fiber": "fiber",
    "toll road": "toll_roads", "toll roads": "toll_roads", "tollroad": "toll_roads",
    "toll_roads": "toll_roads", "roads": "toll_roads",
    "water": "water", "water utilities": "water", "water utility": "water",
}

# country -> cities (each city appears under exactly one country, so a city
# identifies its country unambiguously)
COUNTRIES = {
    "United States": ["Houston", "Phoenix", "Ashburn", "Dallas", "Denver"],
    "Canada": ["Toronto", "Calgary", "Montreal"],
    "United Kingdom": ["London", "Manchester", "Leeds"],
    "Germany": ["Frankfurt", "Hamburg", "Munich"],
    "Spain": ["Madrid", "Seville", "Valencia"],
    "Netherlands": ["Amsterdam", "Rotterdam"],
    "France": ["Paris", "Lyon", "Marseille"],
    "Australia": ["Sydney", "Perth", "Melbourne"],
    "Chile": ["Santiago", "Antofagasta"],
    "Brazil": ["São Paulo", "Rio de Janeiro"],
    "Japan": ["Tokyo", "Osaka"],
    "India": ["Mumbai", "Chennai"],
}
CITY_TO_COUNTRY = {city.lower(): c for c, cities in COUNTRIES.items() for city in cities}

COUNTRY_ALIASES = {
    "usa": "United States", "us": "United States", "u.s.": "United States",
    "u.s.a.": "United States", "united states of america": "United States",
    "uk": "United Kingdom", "u.k.": "United Kingdom", "great britain": "United Kingdom",
    "england": "United Kingdom",
    "deutschland": "Germany",
    "holland": "Netherlands", "the netherlands": "Netherlands",
    "españa": "Spain", "espana": "Spain",
    "brasil": "Brazil",
}
COUNTRY_ALIASES.update({c.lower(): c for c in COUNTRIES})

DEAL_TYPE_ALIASES = {
    "acquisition": "acquisition", "acq": "acquisition", "buyout": "acquisition",
    "follow on": "follow_on", "follow-on": "follow_on", "follow_on": "follow_on",
    "add-on": "follow_on",
    "refinancing": "refinancing", "refi": "refinancing", "refinance": "refinancing",
    "partial exit": "partial_exit", "partial_exit": "partial_exit", "partial sale": "partial_exit",
    "full exit": "full_exit", "full_exit": "full_exit", "exit": "full_exit", "sale": "full_exit",
}

DEAL_STATUS_ALIASES = {
    "closed": "closed", "completed": "closed", "done": "closed",
    "pending": "pending", "signed": "pending", "in progress": "pending",
    "terminated": "terminated", "cancelled": "terminated", "canceled": "terminated",
}

COMPANY_STATUS_ALIASES = {
    "active": "active", "held": "active", "current": "active",
    "exited": "exited", "sold": "exited", "realised": "exited", "realized": "exited",
}
