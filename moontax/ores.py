"""Group ids of the five EVE moon-asteroid groups.

The per-ore tax dropdown is populated from these via **public** ESI
(``tasks.load_ore_catalog`` / the ``moontax_load_ores`` command). These groups hold
the base moon ores plus their quality variants ("Brimful …") **and** their compressed
variants ("Compressed …"); the catalog keeps all three and links variants to their base
ore (see ``OreType.base_type_id``). Ordered ubiquitous → exceptional; ``OreType.group_id``
ordering follows the same so the dropdown reads by rarity.
"""

MOON_ORE_GROUP_IDS = [
    1884,  # Ubiquitous Moon Asteroids
    1920,  # Common Moon Asteroids
    1921,  # Uncommon Moon Asteroids
    1922,  # Rare Moon Asteroids
    1923,  # Exceptional Moon Asteroids
]

# Moon ore compresses at a fixed 100 raw units → 1 compressed unit (uniform across all
# moon ores). Used to let players pay a tax line in compressed ore (see core.compression).
COMPRESSION_RATIO = 100
