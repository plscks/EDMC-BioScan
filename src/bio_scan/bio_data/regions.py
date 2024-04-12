region_map: dict[str, list[int]] = {
    'orion-cygnus': [1, 4, 7, 8, 16, 17, 18, 35],
    'orion-cygnus-1': [4, 7, 8, 16, 17, 18, 35],
    'orion-cygnus-core': [7, 8, 16, 17, 18, 35],
    'sagittarius-carina': [1, 4, 9, 18, 19, 20, 21, 22, 23, 40],
    'sagittarius-carina-core': [9, 18, 19, 20, 21, 22, 23, 40],
    'sagittarius-carina-core-9': [18, 19, 20, 21, 22, 23, 40],
    'scutum-centaurus': [1, 4, 9, 10, 11, 12, 24, 25, 26, 42, 28],
    'scutum-centaurus-core': [9, 10, 11, 12, 24, 25, 26, 42, 28],
    'outer': [1, 2, 5, 6, 13, 14, 27, 29, 31, 41, 37],
    'perseus': [1, 3, 7, 15, 30, 32, 33, 34, 36, 38, 39],
    'perseus-core': [3, 7, 15, 30, 32, 33, 34, 36, 38, 39],
    'exterior': [14, 21, 22, 23, 24, 25, 26, 27, 28, 29, 31, 34, 36,
                 37, 38, 39, 40, 41, 42],
    'anemone-a': [7, 8, 13, 14, 15, 16, 17, 18, 27, 32],
    'amphora': [10, 19, 20, 21, 22],
    'brain-tree': [2, 9, 10, 17, 18, 35],
    'empyrean-straits': [2],
    'center': [1, 2, 3]
}

guardian_nebulae: dict[str, tuple[int, tuple[float, float, float]]] = {
    'Hen 2-333': (750, (-840.65625, -561.15625, 13361.8125)),
    'Gamma Velorum': (750, (1099.21875, -146.6875, -133.59375)),
    'Skaudai AA-A h71': (100, (-5493.09375, -589.28125, 10424.4375)),
    'Blaa Hypai AA-A h68': (100,  (1220.40625, -694.625, 12312.8125)),
    'Eorl Auwsy AA-A h72': (100, (4949.9375, 164, 20640.125)),
    'Prai Hypoo AA-A h60': (100, (-9294.875, -458.40625, 7905.71875)),
    'Eta Carina Nebula': (100, (8579.96875, -138.96875, 2701.375)),
    'NGC 3199': (100, (14574.15625, -259.625, 3511.90625))
}

tuber_zones: dict[str, tuple[tuple[int, int], tuple[float, float, float]]] = {
    'Arcadian Stream': ((200, 600), (8885, -20, 20535)),
    'Empyrean Straits': ((300, 400), (4380, 350, 21260)),
    'Galactic Center': ((500, 1000), (44.5, 492.7, 25916)),
    'Hawking A': ((150, 600), (5788, 150, 6335)),
    'Hawking B': ((200, 600), (9990, -40, 8335)),
    'Inner Orion Spur': ((200, 600), (-3485, 39, 7320)),
    'Inner O-P Conflux': ((350, 750), (-13245, -80, 30285)),
    'Inner S-C Arm A': ((200, 600), (-1600, -37, 10720)),
    'Inner S-C Arm B': ((150, 600), (-6650, -12, 12575)),
    'Inner S-C Arm C': ((200, 600), (-9355, -50, 17175)),
    'Inner S-C Arm D': ((300, 400), (-12000, 232, 22670)),
    'Izanami': ((200, 750), (-4610, 370, 37225)),
    'Norma Arm A': ((500, 1000), (3722.6, 200, 16441)),
    'Norma Arm B': ((200, 500), (3740, 175, 16460)),
    'Norma Expanse A': ((200, 600), (4245, -42, 12071)),
    'Norma Expanse B': ((150, 250), (5580, 40, 11727)),
    'Odin A': ((750, 1000), (-7945, 230, 28025)),
    'Odin B': ((200, 600), (-5329, -68, 18647)),
    'Ryker A': ((250, 750), (1715, 766, 34070)),
    'Ryker B': ((750, 1500), (-1445, 345, 30345)),
    'Trojan Belt': ((250, 500), (18600, 65, 31750)),
}
