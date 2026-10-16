// er-duel-rules.js -- the competitive duel ruleset referee as a single Frida agent.
//
// Prints "Attention: Rule break: <player>. <rule>" whenever anyone in the session -- you or any
// other player -- breaks the duel ruleset, with what broke it on the next line, and appends each
// break to er-duel-rules-breaks.txt beside eldenring.exe. The same checks as er_duel_rules.dll
// (crates/er-duel-rules-core), for anyone who would rather not install a DLL; the DLL is the one
// that also lists the breaks on screen.
//
// RUN IT (Windows, game started with EAC off, e.g. through Better Multiplayer or Seamless):
//   pip install frida-tools
//   frida -n eldenring.exe -l er-duel-rules.js
// Leave that window open while you play. Every line it prints is also appended to
// er-duel-rules-frida.log beside eldenring.exe, which is the file to send back;
// er-duel-rules-breaks.txt holds the rule breaks alone, one dated line each.
//
// WHAT IT TOUCHES: it reads memory and nothing else, except two Frida hooks whose first bytes are
// checked before they are placed: the game's per-frame tick (the checks run there, on the game's
// own thread) and SpecialEffect::Apply (so a heal or a flask that lasts one frame is still seen).
// It refuses to start on any game build but the one it was written against, because every address
// and offset below belongs to that build.
//
// WHAT ONLY YOUR OWN COPY SEES: the BMP manual attack aiming setting and an unlocked frame rate
// are not sent to other players, so each player's copy judges only that player.
//
// WHAT IT CANNOT SEE: macros or third-party tools other than an FPS unlocker, hidden ping (BMP
// keeps it where this script cannot find it yet), smurfing or faked results, transmog consent,
// block desync, effect-duration tricks, bugged weapon states, void tech and pre-buffs. Rune Arc is
// checked only in Better Multiplayer, where every player must have one active; in vanilla the game
// only lets the host's Great Rune work.
//
// Generated data lives between the GENERATED markers below and is rewritten by
// scripts/er-duel-rules-gen.py --write; everything else is written by hand.
'use strict';

// --- BEGIN GENERATED (scripts/er-duel-rules-gen.py --write) ---
const T = {"affinity": {"COLD": 900, "LIGHTNING": 600}, "bannedArmor": [[210000, "Briar Helm"], [210100, "Briar Armor"], [210200, "Briar Gauntlets"], [210300, "Briar Greaves"], [211100, "Briar Armor (Altered)"], [5160000, "Rakshasa Helm"], [5160100, "Rakshasa Armor"], [5160200, "Rakshasa Gauntlets"], [5160300, "Rakshasa Greaves"]], "bannedTears": [[11002, "Crimson Crystal Tear", "Heal"], [11003, "Crimson Crystal Tear", "Heal"], [11007, "Crimson Bubbletear", "Heal"], [11009, "Crimsonburst Crystal Tear", "Heal"], [11012, "Winged Crystal Tear", "Named"], [11013, "Thorny Cracked Tear", "Damage"], [11014, "Spiked Cracked Tear", "Damage"], [11015, "Windy Crystal Tear", "Named"], [11018, "Leaden Hardtear", "Named"], [11020, "Crimsonwhorl Bubbletear", "Heal"], [11021, "Strength-knot Crystal Tear", "Stat"], [11022, "Dexterity-knot Crystal Tear", "Stat"], [11023, "Intelligence-knot Crystal Tear", "Stat"], [11024, "Faith-knot Crystal Tear", "Stat"], [11026, "Stonebarb Cracked Tear", "Named"], [11028, "Flame-Shrouding Cracked Tear", "Damage"], [11029, "Magic-Shrouding Cracked Tear", "Damage"], [11030, "Lightning-Shrouding Cracked Tear", "Damage"], [11031, "Holy-Shrouding Cracked Tear", "Damage"], [2011010, "Crimsonburst Dried Tear", "Heal"], [2011050, "Bloodsucking Cracked Tear", "Damage"], [2011070, "Deflecting Hardtear", "Named"]], "bodyBuffs": [[1586, "Contagious Fury"], [1588, "Contagious Fury"], [1640, "Holy Ground"], [1650, "Endure"], [1655, "Endure"], [1730, "Golden Vow"], [1768, "Rallying Standard"], [1836, "Last Rites"], [1850, "Oath of Vengeance"], [1851, "Oath of Vengeance"], [1870, "Shared Order"], [3910, "Spellproof Dried Liver"], [3920, "Fireproof Dried Liver"], [3930, "Lightningproof Dried Liver"], [3950, "Exalted Flesh"], [3953, "Pickled Turtle Neck"], [3960, "Immunizing Cured Meat"], [3961, "Invigorating Cured Meat"], [3962, "Clarifying Cured Meat"], [3963, "Dappled Cured Meat"], [19980, "Rock Heart"], [19981, "Priestess Heart"], [500138, "Phantom Great Rune"], [500139, "Phantom Great Rune"], [500820, "Boiled Crab"], [500830, "Boiled Prawn"], [501180, "Holyproof Dried Liver"], [501290, "Starlight Shards"], [501310, "Immunizing White Cured Meat"], [501320, "Invigorating White Cured Meat"], [501330, "Clarifying White Cured Meat"], [501340, "Dappled White Cured Meat"], [502100, "Soft Cotton"], [503355, "Baldachin's Blessing"], [503361, "Radiant Baldachin's Blessing"], [503500, "Uplifting Aromatic"], [503501, "Uplifting Aromatic"], [503520, "Ironjar Aromatic"], [503550, "Bloodboil Aromatic"], [1413000, "Terra Magica"], [1467000, "Unseen Form"], [1467002, "Unseen Form"], [1605000, "Flame, Grant Me Strength"], [1606000, "Flame, Protect Me"], [1627000, "Black Flame's Protection"], [1633000, "Barrier of Gold"], [1634000, "Protection of the Erdtree"], [1645000, "Flame Fortification"], [1646000, "Magic Fortification"], [1647000, "Lightning Fortification"], [1648000, "Divine Fortification"], [1649000, "Lord's Divine Fortification"], [1651000, "Assassin's Approach"], [1660000, "Golden Vow"], [1697100, "Dragonbolt Blessing"], [1733000, "Howl of Shabriri"], [1790300, "Golden Lightning Fortification"], [20000851, "Flare, O Serpent"], [20000856, "Flare, O Serpent"], [20000980, "Light"], [20500910, "Lulling Branch"], [20501100, "Dragon Communion Flesh"], [20501105, "Dragonscale Flesh"], [20501110, "Fingerprint Nostrum"], [20501120, "Spellproof Pickled Liver"], [20501130, "Fireproof Pickled Liver"], [20501140, "Lightningproof Pickled Liver"], [20501150, "Holyproof Pickled Liver"], [20501160, "Opaline Pickled Liver"], [20501170, "Well-Pickled Turtle Neck"], [20501201, "Scorpion Stew"], [20501206, "Gourmet Scorpion Stew"], [20501210, "Sacred Bloody Flesh"], [20501212, "Sacred Bloody Flesh"], [20502100, "Ancient Dragon's Blessing"], [20503170, "Golden Vow"], [21490000, "Mantle of Thorns"], [21691000, "Dragonbolt of Florissax"]], "ceruleanFlask": [501050, 501051, 501052, 501053, 501054, 501055, 501056, 501057, 501058, 501059, 501060, 501061, 501062], "chillingMistBuffs": [826, 828], "fanDaggerBullets": [10175000], "greaseBuffs": [3140, 3144, 3150, 3152, 3154, 3156, 3160, 3161, 3162, 3163, 3165, 3166, 3167, 3168, 3170, 3171, 3172, 3173, 3175, 3177, 3179, 3181, 3185, 3186, 3187, 3188, 3190, 3192, 3194, 3196, 3260, 3262, 3310, 3312, 3314, 3316, 102310, 102312, 102314, 102316, 501690, 501691, 20501400, 20501401, 20501402, 20501403, 20501410, 20501411, 20501412, 20501413, 20501420, 20501421, 20501422, 20501423, 20501430, 20501431, 20501432, 20501433, 20501480, 20501481, 20501620, 20501622], "heals": [[1641, "Holy Ground"], [3360, "Warming Stone"], [501235, "Raw Meat Dumpling"], [503311, "Frenzyflame Stone"], [1642000, "Urgent Heal"], [1642100, "Heal"], [1642101, "Heal"], [1642200, "Great Heal"], [1642201, "Great Heal"], [1642300, "Lord's Heal"], [1642301, "Lord's Heal"], [1642400, "Erdtree Heal"], [1642401, "Erdtree Heal"], [1643000, "Blessing's Boon"], [1643001, "Blessing's Boon"], [1643100, "Blessing of the Erdtree"], [1643101, "Blessing of the Erdtree"], [1685000, "Bestial Vitality"], [20500901, "Blessing of Marika"], [20501202, "Scorpion Stew"], [20501207, "Gourmet Scorpion Stew"], [20503310, "Sunwarmth Stone"], [21640000, "Heal from Afar"], [21640001, "Heal from Afar"], [21667000, "Minor Erdtree"]], "parrySkills": [[92, "Parry"], [93, "Buckler Parry"], [95, "Carian Retaliation"], [96, "Storm Wall"], [97, "Golden Parry"], [99, "Thops's Barrier"]], "poisonGrease": [3175, 3177, 3179, 3181], "regenEquipment": [["Armor", 690000, "Royal Remains Helm"], ["Armor", 690100, "Royal Remains Armor"], ["Armor", 690200, "Royal Remains Gauntlets"], ["Armor", 690300, "Royal Remains Greaves"], ["Shield", 32140000, "Icon Shield"], ["Talisman", 5020, "Blessed Dew Talisman"]], "rippleWeapons": [[14050000, "Ripple Blade"], [18060000, "Ripple Crescent Halberd"]], "rotGrease": [3310, 3312, 3314, 3316], "runeArcEffects": [[490, "Rune Arc"], [491, "Rune Arc"], [499, "Rune Arc"], [600, "Godrick's Great Rune"], [601, "Godrick's Great Rune"], [602, "Godrick's Great Rune"], [603, "Godrick's Great Rune"], [604, "Godrick's Great Rune"], [605, "Godrick's Great Rune"], [606, "Godrick's Great Rune"], [607, "Godrick's Great Rune"], [609, "Godrick's Great Rune"], [610, "Radahn's Great Rune"], [611, "Radahn's Great Rune"], [612, "Radahn's Great Rune"], [613, "Radahn's Great Rune"], [614, "Radahn's Great Rune"], [615, "Radahn's Great Rune"], [616, "Radahn's Great Rune"], [617, "Radahn's Great Rune"], [619, "Radahn's Great Rune"], [620, "Morgott's Great Rune"], [621, "Morgott's Great Rune"], [622, "Morgott's Great Rune"], [623, "Morgott's Great Rune"], [624, "Morgott's Great Rune"], [625, "Morgott's Great Rune"], [626, "Morgott's Great Rune"], [627, "Morgott's Great Rune"], [629, "Morgott's Great Rune"], [630, "Rykard's Great Rune"], [631, "Rykard's Great Rune"], [632, "Rykard's Great Rune"], [633, "Rykard's Great Rune"], [634, "Rykard's Great Rune"], [635, "Rykard's Great Rune"], [636, "Rykard's Great Rune"], [637, "Rykard's Great Rune"], [639, "Rykard's Great Rune"], [640, "Mohg's Great Rune"], [641, "Mohg's Great Rune"], [642, "Mohg's Great Rune"], [643, "Mohg's Great Rune"], [644, "Mohg's Great Rune"], [645, "Mohg's Great Rune"], [646, "Mohg's Great Rune"], [647, "Mohg's Great Rune"], [649, "Mohg's Great Rune"], [650, "Malenia's Great Rune"], [651, "Malenia's Great Rune"], [652, "Malenia's Great Rune"], [653, "Malenia's Great Rune"], [654, "Malenia's Great Rune"], [655, "Malenia's Great Rune"], [656, "Malenia's Great Rune"], [657, "Malenia's Great Rune"], [659, "Malenia's Great Rune"]], "skill": {"BARBARIC_ROAR": 140, "BLIND_SPOT": 276, "BLOODHOUNDS_STEP": 156, "CHILLING_MIST": 75, "DETERMINATION": 130, "ENDURE": 150, "FLAMING_STRIKE": 63, "MULETA": 372, "NEEDLE_PIERCER": 301, "OATH_OF_VENGEANCE": 242, "PAINFUL_STRIKE": 269, "QUICKSTEP": 155, "RAPTOR_OF_THE_MISTS": 157, "RED_BEAR_HUNT": 275, "ROYAL_KNIGHTS_RESOLVE": 131, "SAVAGE_CLAWS": 274, "SHRIEK_OF_SORROW": 286, "SPINNING_SLASH": 3, "STORMCALLER": 23, "STORM_ASSAULT": 22, "STORM_STOMP": 111, "SWIFT_SLASH": 277, "SWORD_DANCE": 24}, "sleepGrease": [3150, 3152, 3154, 3156, 102310, 102312, 102314, 102316], "spellBullets": [["Gravitational Missile", [210471000, 210471001, 210471002, 210471003, 210471004, 210471005, 210471006, 210471007, 210471050, 210471051, 210471052, 210471053, 210471054, 210471055, 210471056, 210471057, 210471058, 210471059, 210471060]], ["Night Maiden's Mist", [10650000, 10650001, 10650002, 10650050, 10650051, 10650052]], ["The Flame of Frenzy", [10731000, 10731001, 10731002, 10731005, 10731006, 10731010, 10731050, 10731051, 10731052, 10731055, 10731056, 10731060]], ["Roar of Rugalea", [210680000, 210680050]]], "spiritAshTriggers": [[200000, "Black Knife Tiche"], [201000, "Banished Knight Oleg"], [202000, "Banished Knight Engvall"], [203000, "Fanged Imp Ashes"], [204000, "Latenna the Albinauric"], [205000, "Nomad Ashes"], [206000, "Nightmaiden & Swordstress Puppets"], [207000, "Mimic Tear Ashes"], [208000, "Crystalian Ashes"], [209000, "Ancestral Follower Ashes"], [210000, "Winged Misbegotten Ashes"], [211000, "Albinauric Ashes"], [212000, "Skeletal Militiaman Ashes"], [213000, "Skeletal Bandit Ashes"], [214000, "Oracle Envoy Ashes"], [215000, "Putrid Corpse Ashes"], [216000, "Depraved Perfumer Carmaan"], [217000, "Perfumer Tricia"], [218000, "Glintstone Sorcerer Ashes"], [219000, "Twinsage Sorcerer Ashes"], [220000, "Page Ashes"], [221000, "Battlemage Hugues"], [222000, "Clayman Ashes"], [223000, "Cleanrot Knight Finlay"], [224000, "Kindred of Rot Ashes"], [225000, "Marionette Soldier Ashes"], [226000, "Avionette Soldier Ashes"], [227000, "Fire Monk Ashes"], [228000, "Blackflame Monk Amon"], [229000, "Man-Serpent Ashes"], [230000, "Azula Beastman Ashes"], [231000, "Kaiden Sellsword Ashes"], [232000, "Lone Wolf Ashes"], [233000, "Giant Rat Ashes"], [233100, "spirit summon"], [233200, "spirit summon"], [234000, "Demi-Human Ashes"], [235000, "Rotten Stray Ashes"], [236000, "Spirit Jellyfish Ashes"], [237000, "Warhawk Ashes"], [238000, "Stormhawk Deenh"], [239000, "Bloodhound Knight Floh"], [240000, "Wandering Noble Ashes"], [241000, "Noble Sorcerer Ashes"], [242000, "Vulgar Militia Ashes"], [243000, "Mad Pumpkin Head Ashes"], [244000, "Land Squirt Ashes"], [245000, "Miranda Sprout Ashes"], [246000, "Soldjars of Fortune Ashes"], [247000, "Omenkiller Rollo"], [248000, "Greatshield Soldier Ashes"], [249000, "Archer Ashes"], [250000, "Godrick Soldier Ashes"], [251000, "Raya Lucaria Soldier Ashes"], [252000, "Leyndell Soldier Ashes"], [253000, "Radahn Soldier Ashes"], [254000, "Mausoleum Soldier Ashes"], [255000, "Haligtree Soldier Ashes"], [256000, "Ancient Dragon Knight Kristoff"], [257000, "Redmane Knight Ogha"], [258000, "Lhutel the Headless"], [259000, "Nepheli Loux Puppet"], [260000, "Dung Eater Puppet"], [261000, "Finger Maiden Therolina Puppet"], [262000, "Dolores the Sleeping Arrow Puppet"], [263000, "Jarwight Puppet"], [20200000, "Curseblade Meera"], [20201000, "Bloodfiend Hexer's Ashes"], [20202000, "Gravebird Ashes"], [20203000, "Fire Knight Hilde"], [20204000, "Spider Scorpion Ashes"], [20205000, "Inquisitor Ashes"], [20206000, "Demi-Human Swordsman Yosh"], [20207000, "Messmer Soldier Ashes"], [20208000, "Black Knight Commander Andreas"], [20209000, "Black Knight Captain Huw"], [20210000, "Bigmouth Imp Ashes"], [20211000, "Man-Fly Ashes"], [20212000, "Taylew the Golem Smith"], [20213000, "Divine Bird Warrior Ornis"], [20214000, "Horned Warrior Ashes"], [20215000, "Ancient Dragon Florissax"], [20217000, "Fingercreeper Ashes"], [20218000, "Fire Knight Queelign"], [20219000, "Swordhand of Night Jol\u00e1n"], [20220000, "Jol\u00e1n and Anna"], [20223000, "spirit summon"], [20224000, "spirit summon"], [20225000, "spirit summon"], [20226000, "spirit summon"], [20227000, "spirit summon"], [20228000, "spirit summon"], [20229000, "spirit summon"], [20230000, "spirit summon"]], "statusSkills": [[52, "Ice Spear"], [72, "Blood Blade"], [75, "Chilling Mist"], [76, "Poisonous Mist"], [110, "Hoarfrost Stomp"], [136, "Seppuku"], [168, "Zamor Ice Storm"], [174, "Death Flare"], [216, "Reduvia Blood Blade"], [223, "Mists of Slumber"], [225, "Wolf's Assault"], [234, "Bloodboon Ritual"], [243, "Ice Lightning Sword"], [248, "Ghostflame Ignition"], [250, "Frenzyflame Thrust"], [283, "Divine Beast Frost Stomp"], [311, "Mists of Eternal Sleep"], [334, "Feeble Lord's Frenzied Flame"], [358, "Deadly Poison Spray"], [364, "Ghostflame Call"], [365, "Rancor Slash"], [366, "Rancor Slash"]], "swordDanceFastClips": [40000, 40005, 40010, 40015, 45800, 45805, 45810, 45815], "talisman": {"ALL_CRUCIBLES": 8240, "CRUCIBLE_FEATHER": 4070, "FINE_CRUCIBLE_FEATHER": 8010, "GODFREY_ICON": 3090, "RED_FEATHER": 2040, "RELLANAS_CAMEO": 8220, "SHARD_OF_ALEXANDER": 1231, "SPEAR": 2060, "TWO_HANDED_SWORD": 8040, "WARRIOR_JAR_SHARD": 1230}, "tearEffects": [[3505, 11002, "Crimson Crystal Tear", "Heal"], [3512, 11007, "Crimson Bubbletear", "Heal"], [3513, 11007, "Crimson Bubbletear", "Heal"], [3515, 11021, "Strength-knot Crystal Tear", "Stat"], [3516, 11022, "Dexterity-knot Crystal Tear", "Stat"], [3517, 11023, "Intelligence-knot Crystal Tear", "Stat"], [3518, 11024, "Faith-knot Crystal Tear", "Stat"], [3519, 11018, "Leaden Hardtear", "Named"], [3550, 11013, "Thorny Cracked Tear", "Damage"], [3551, 11013, "Thorny Cracked Tear", "Damage"], [3552, 11013, "Thorny Cracked Tear", "Damage"], [3553, 11013, "Thorny Cracked Tear", "Damage"], [3554, 11013, "Thorny Cracked Tear", "Damage"], [3555, 11013, "Thorny Cracked Tear", "Damage"], [3556, 11013, "Thorny Cracked Tear", "Damage"], [3557, 11013, "Thorny Cracked Tear", "Damage"], [3558, 11013, "Thorny Cracked Tear", "Damage"], [3559, 11013, "Thorny Cracked Tear", "Damage"], [3560, 11013, "Thorny Cracked Tear", "Damage"], [3561, 11013, "Thorny Cracked Tear", "Damage"], [3580, 11020, "Crimsonwhorl Bubbletear", "Heal"], [3581, 11020, "Crimsonwhorl Bubbletear", "Heal"], [3582, 11020, "Crimsonwhorl Bubbletear", "Heal"], [3583, 11020, "Crimsonwhorl Bubbletear", "Heal"], [3584, 11020, "Crimsonwhorl Bubbletear", "Heal"], [3585, 11020, "Crimsonwhorl Bubbletear", "Heal"], [3586, 11020, "Crimsonwhorl Bubbletear", "Heal"], [3587, 11020, "Crimsonwhorl Bubbletear", "Heal"], [102000, 2011070, "Deflecting Hardtear", "Named"], [511009, 11009, "Crimsonburst Crystal Tear", "Heal"], [511012, 11012, "Winged Crystal Tear", "Named"], [511014, 11014, "Spiked Cracked Tear", "Damage"], [511015, 11015, "Windy Crystal Tear", "Named"], [511016, 11015, "Windy Crystal Tear", "Named"], [511026, 11026, "Stonebarb Cracked Tear", "Named"], [511028, 11028, "Flame-Shrouding Cracked Tear", "Damage"], [511029, 11029, "Magic-Shrouding Cracked Tear", "Damage"], [511030, 11030, "Lightning-Shrouding Cracked Tear", "Damage"], [511031, 11031, "Holy-Shrouding Cracked Tear", "Damage"], [20511010, 2011010, "Crimsonburst Dried Tear", "Heal"], [20511011, 2011010, "Crimsonburst Dried Tear", "Heal"], [20511050, 2011050, "Bloodsucking Cracked Tear", "Damage"], [20511051, 2011050, "Bloodsucking Cracked Tear", "Damage"]], "weapon": {"BACKHAND_BLADE": 64500000, "CLAWS_OF_NIGHT": 22500000, "CLEANROT_KNIGHTS_SWORD": 5010000, "CLUB": 11010000, "IDUS_SWORD": 67530000, "LEONTIELS_GREATSWORD": 3560000, "MILADY": 67500000, "MOHGWYNS_SACRED_SPEAR": 17010000, "PATA": 21510000, "RAPTOR_TALONS": 22030000, "REVERSE_BLADED_SWORD": 64530000, "SHAMSHIR": 7030000, "SMITHSCRIPT_CIRQUE": 64510000, "SMITHSCRIPT_DAGGER": 63500000, "SMITHSCRIPT_SHIELD": 30510000, "SPREAD_CROSSBOW": 43510000, "STAR_FIST": 21080000, "STONE_CLUB": 11140000, "SWORD_LANCE": 3500000, "WAKIZASHI": 1100000}, "wepType": {"AXE": 17, "BACKHAND_BLADE": 92, "CLAW": 37, "CURVED_SWORD": 9, "DAGGER": 1, "FIST": 35, "GREATSHIELD": 69, "GREAT_SPEAR": 28, "HEAVY_THRUSTING_SWORD": 16, "LIGHT_GREATSWORD": 93, "MEDIUM_SHIELD": 67, "SMALL_SHIELD": 65, "THRUSTING_SHIELD": 90, "THRUSTING_SWORD": 15, "TWINBLADE": 14}};
const PARAM_FIELDS = {"CalcCorrectGraph": {"fields": {"adjPt_maxGrowVal0": [40, "f32"], "adjPt_maxGrowVal1": [44, "f32"], "adjPt_maxGrowVal2": [48, "f32"], "adjPt_maxGrowVal3": [52, "f32"], "adjPt_maxGrowVal4": [56, "f32"], "stageMaxGrowVal0": [20, "f32"], "stageMaxGrowVal1": [24, "f32"], "stageMaxGrowVal2": [28, "f32"], "stageMaxGrowVal3": [32, "f32"], "stageMaxGrowVal4": [36, "f32"], "stageMaxVal0": [0, "f32"], "stageMaxVal1": [4, "f32"], "stageMaxVal2": [8, "f32"], "stageMaxVal3": [12, "f32"], "stageMaxVal4": [16, "f32"]}, "index": 30, "struct": "CACL_CORRECT_GRAPH_ST"}, "EquipParamAccessory": {"fields": {"refCategory": [49, "u8"], "refId": [4, "s32"], "residentSpEffectId1": [76, "s32"], "residentSpEffectId2": [80, "s32"], "residentSpEffectId3": [84, "s32"], "residentSpEffectId4": [88, "s32"], "weight": [12, "f32"]}, "index": 2, "struct": "EQUIP_PARAM_ACCESSORY_ST"}, "EquipParamProtector": {"fields": {"residentSpEffectId": [40, "s32"], "residentSpEffectId2": [44, "s32"], "residentSpEffectId3": [48, "s32"], "weight": [36, "f32"]}, "index": 1, "struct": "EQUIP_PARAM_PROTECTOR_ST"}, "EquipParamWeapon": {"fields": {"isEnhance": [262, "bit", 7], "physGuardCutRate": [52, "f32"], "reinforceTypeId": [218, "s16"], "spEffectBehaviorId0": [68, "s32"], "spEffectBehaviorId1": [72, "s32"], "spEffectBehaviorId2": [76, "s32"], "weight": [16, "f32"], "wepType": [422, "u16"]}, "index": 0, "struct": "EQUIP_PARAM_WEAPON_ST"}, "ReinforceParamWeapon": {"fields": {"physicsGuardCutRate": [44, "f32"]}, "index": 4, "struct": "REINFORCE_PARAM_WEAPON_ST"}, "SpEffectParam": {"fields": {"addEndureStatus": [800, "s8"], "equipWeightChangeRate": [228, "f32"], "spCategory": [318, "u16"]}, "index": 15, "struct": "SP_EFFECT_PARAM_ST"}};
// --- END GENERATED ---

// ============================================================================================
// The build this agent is pinned to. `TimeDateStamp` comes from the in-memory PE header, which
// the game does not rewrite; every RVA and offset below was measured against this build.
// ============================================================================================
const BUILD = { label: 'ELDEN RING 1.17.1 (eldenring.exe 2.7.1.0)', timeDateStamp: 0x6a96b418 };

// Singleton pointers (`.data` RVAs on 1.17.1): docs/recon/rva-map-1162-to-1170.data.tsv for the
// first three, and the `mov [rip+...], rax` after `CSBulletManager`'s constructor in
// `_Common_Initialize` (1.17.1 0x140aeeec9) for the fourth.
const RVA = {
    gameDataMan: 0x3d61f98,
    worldChrMan: 0x3d69ff8,
    soloParamRepository: 0x3d85f58,
    bulletManager: 0x3d667a8,
    // CSFlipperImp's singleton pointer (1.16.2 0x4589ad8, crates/er-telemetry-core/src/lib.rs).
    flipper: 0x458db58,
    // Hooks, with the bytes each must start with (eldenring-deobf-1.17.1.bin).
    frameTick: 0x773900,
    speffectApply: 0x4fb6b0,
};
const PROLOGUE = {
    frameTick: '48 8b c4 55 56 57 41 54 41 55 41 56 41 57 48 8d',
    speffectApply: '48 89 6c 24 10 48 89 74 24 18 57 41 56 41 57 48',
};

// Struct offsets, read off fromsoftware-rs by the compiler (offset_of!) on 2026-10-15.
const OFF = {
    wcmPlayerChrSet: 0x10ee0,
    wcmMainPlayer: 0x1e508,
    chrSetCapacity: 0x10,
    chrSetEntries: 0x18,
    chrSetEntryStride: 0x10,
    playerGameData: 0x580,
    chrHandle: 0x8,
    chrSpecialEffect: 0x178,
    chrModules: 0x190,
    modData: 0x0,
    modTimeAct: 0x18,
    modRide: 0xe8,
    dataHp: 0x138,
    timeActQueue: 0x20,
    timeActEntry: 0x10,
    timeActCount: 10,
    timeActPlayTime: 0x4,
    timeActWriteIdx: 0xc0,
    rideNode: 0x10,
    rideState: 0x50,
    speHead: 0x8,
    speEntryParamId: 0x8,
    speEntryNext: 0x30,
    pgdLevel: 0x68,
    pgdEndurance: 0x44,
    pgdAttributes: 0x3c,
    // PlayerGameData.rune_arc_active, between upstream's offset-named unkfe..unk100 fields.
    pgdRuneArcActive: 0xff,
    // GameDataMan +0x58 GameSettings; +0x16 manual attack aiming (+0x17 auto target), read live on
    // 1.17.1 and pinned by fromsoftware-rs game_data_man.rs.
    gdmGameSettings: 0x58,
    settingsManualAttackAiming: 0x16,
    // CSFlipper +0x1c seconds per frame at the target rate (0.016667 = 60 fps), +0x2b8 the game's
    // measured frame rate.
    flipperTargetSpf: 0x1c,
    flipperMeasuredFps: 0x2b8,
    pgdName: 0x9c,
    pgdNameChars: 17,
    pgdSize: 0xaf0,
    pgdChrAsm: 0x31c,
    pgdPhysick: 0x694,
    asmArmStyle: 0x8,
    asmLeftSlot: 0xc,
    asmRightSlot: 0x10,
    asmParamIds: 0x7c,
    gdmRemoteList: 0x10,
    gdmRemoteStates: 0x20,
    bmHead: 0x8,
    bulletOwner: 0xb40,
    bulletParamIds: [0xdd0, 0xda0, 0xe00],
    bulletNext: 0xe18,
    sprHolders: 0x80,
    holderSize: 0x48,
    holderResCaps: 0x8,
    paramResCap: 0x80,
    fd4ParamResCapData: 0x80,
};
const REMOTE_FULLY_SYNCED = 0xf;
const REMOTE_SLOTS = 5;
const WALK_LIMIT = 512;

// ============================================================================================
// Rules: a port of crates/er-duel-rules-core/src/{player,facts,rules,referee}.rs. Pure: nothing
// here touches the game, so `node scripts/test-er-duel-rules-frida.cjs` runs it.
// ============================================================================================
const NO_ITEM = -1;
const RUNE_LEVEL = 125;
const CERULEAN_FLASK_LIMIT = 2;
const SHIELD_PHYS_GUARD_CAP = 90.0;
const LIGHT_LOAD_CEILING = Math.fround(0.3);
// referee.rs REARM_MS: a lasting break seen again within this long is the same break; an event is never merged.
const REARM_MS = 1000;
// A weapon buff the game did not strip within this long of its hand's weapon changing was carried
// across (referee.rs BUFF_TRANSFER_GRACE_MS).
const BUFF_TRANSFER_GRACE_MS = 250;
// The game caps at 60; a higher target, or a measured rate this far past it, is an unlocker.
const UNLOCKED_TARGET_FPS = 61;
const UNLOCKED_MEASURED_FPS = 65;
// Time a player has from entering the match or respawning to light a Rune Arc in Better
// Multiplayer (referee.rs RUNE_ARC_GRACE_MS). A death puts the arc out.
const RUNE_ARC_GRACE_MS = 20000;
// The game's flag, or any Rune Arc / Great Rune effect row (port of rune_arc_active).
const runeArcActive = (p) => p.runeArcFlag === true || p.speffects.some((id) => lookup(T.runeArcEffects, id) !== null);
const CATEGORY_RIGHT_WEAPON_BUFF = 162;
const CATEGORY_LEFT_WEAPON_BUFF = 163;
const ARM = { EMPTY: 0, ONE: 1, LEFT_BOTH: 2, RIGHT_BOTH: 3 };

const RULE_TEXT = {
    RuneLevel: 'Rune Level: 125',
    PlayerBuff: 'Player Buffs Globally Banned',
    PassiveRegen: 'Passive tick-based HP Regen via Armour/Talismans/Shields',
    Healing: 'HP Recovery via Spells/Crystal Tears/Consumables',
    SpectralSteed: 'Spectral Steed Whistle',
    SpiritAshes: 'Spirit Ashes',
    Respec: 'Respecing or swapping character during a match is not allowed',
    SwitchingBuilds: 'Switching builds is prohibited once you enter the same world as your opponent',
    ArmorSwapPoiseReset: 'Armor swap poise reset',
    BuffTransfer: 'Buff Transfers',
    ManualAttackAiming: 'Manual attack aiming setting must be disabled when in the BMP mod',
    UnlockedFps: 'Adhere to the Elden Ring Terms of Service (unlocked FPS)',
    RuneArc: 'Rune Arc (in Better Multiplayer every player has one active)',
    CeruleanFlasks: 'Blue flasks limited to 2',
    FanDaggers: 'Fan Daggers',
    ShieldGuard: 'Blocking With Shields above 90% physical absorption',
    RotGrease: 'Rot Greases',
    SleepGreaseRipple: 'Sleep greases on Ripple Weapons',
    PoisonGreaseShamshir: 'Poison greases on 2H Shamshir',
    WeaponBuffRestricted: 'Buff Restrictions',
    StatTear: 'All Stat Boosting Tears',
    DamageTear: 'All Damage Boosting Tears',
    NamedTear: 'Banned Crystal Tear',
    ThrustingShield: 'Thrusting Shields Class',
    PowerstanceBackhandBlade: 'PS Backhand Blade (Class)',
    SmithscriptCirque: 'Smithscript Cirque',
    TwoHandedBackhandBlade: '2h Backhand Blade',
    LightningReverseBlades: 'Lightning Infused 2h Reverse Blades',
    TwoHandedCleanrot: "2H Cleanrot Knight's Sword",
    PataNotOffhand: 'Pata only allowed as Offhand Moveset',
    ClawsOfNight: 'Claws of Night',
    SmithscriptShield: 'Smithscript Shield',
    SpreadCrossbow: 'Spread Crossbow',
    SmithscriptDagger: 'Smithscript Dagger',
    Milady: 'Milady',
    StoneClubStatus: '2H Stone Club/Club Any Status Application',
    LightningRaptorTalons: "Lightning Infused 2h Raptor's Talons",
    BannedSpell: 'Banned Spell',
    LightRoll: 'Light Roll (<30% Equip Load)',
    BannedArmor: "All Pieces of Briar or Rakshasa's armour",
    BannedAsh: 'Banned Ash of War',
    ChillingMistCold: 'Chilling Mist applied on cold infused weapons',
    SpinningSlashLightGreatsword: 'Spinning Slash on Light Greatswords',
    MuletaTalismans: 'Muleta with Ash Damage boosting talismans',
    IFrameTalisman: 'All I-Frame Affecting Talismans',
    RedFeather: 'Red Feather',
    TwoHandedSwordTalisman: 'Two-Handed Sword Tali.',
    SpearTalismanSwordLance: 'Spear Talisman on Sword Lance',
    OffhandPoisonGrease: 'Offhand drawstring poison grease',
};

const weaponBase = (id) => id - (((id % 10000) + 10000) % 10000);
const weaponAffinity = (id) => Math.floor((((id % 10000) + 10000) % 10000) / 100) * 100;
const weaponRow = (id) => id - (((id % 100) + 100) % 100);
const weaponLevel = (id) => ((id % 100) + 100) % 100;

function skillClip(anim) {
    const tae = Math.trunc(anim / 1000000);
    return tae >= 600 ? [tae - 600, anim % 1000000] : null;
}

// The TimeAct anim queue is a ring of the last ten frames: each frame the game writes the current
// animation and its play time at writeIdx and advances it, so one animation fills every slot over
// ten frames. A start is where consecutive frames change id or the play time falls back.
// Port of player::anim_starts; `history` is [[animId, playTime], ...] in slot order.
function animStarts(last, history, writeIdx) {
    const len = history.length;
    if (!(writeIdx >= 0 && writeIdx < len)) return { starts: [], cursor: last };
    const [anim, playTime] = history[(writeIdx + len - 1) % len];
    const cursor = { writeIdx, anim, playTime };
    if (!last) return { starts: [], cursor };
    let frames = (writeIdx + len - last.writeIdx) % len;
    if (frames === 0 && (anim !== last.anim || playTime !== last.playTime)) frames = len;
    const starts = [];
    let [prevAnim, prevTime] = [last.anim, last.playTime];
    for (let k = 0; k < frames; k++) {
        const [a, t] = history[(last.writeIdx + k) % len];
        if (a > 0 && (a !== prevAnim || t < prevTime)) starts.push(a);
        [prevAnim, prevTime] = [a, t];
    }
    return { starts, cursor };
}

function active(p, hand) {
    const list = hand === 'left' ? p.weaponsLeft : p.weaponsRight;
    const slot = hand === 'left' ? p.leftSlot : p.rightSlot;
    const id = list[slot];
    return id === undefined ? NO_ITEM : id;
}

function twoHanded(p) {
    if (p.armStyle === ARM.LEFT_BOTH) return 'left';
    if (p.armStyle === ARM.RIGHT_BOTH) return 'right';
    return null;
}

const twoHandedWeapon = (p) => (twoHanded(p) === null ? NO_ITEM : active(p, twoHanded(p)));
// c0000.hks GetSwordArtInfo: a skill that is neither a shield nor an arrow-stance skill plays from
// the two-handed weapon, or one-handed from the right (port of non_shield_skill_weapon).
const nonShieldSkillWeapon = (p) => active(p, twoHanded(p) || 'right');
const weapons = (p) => p.weaponsLeft.concat(p.weaponsRight).filter((id) => id >= 0);
const lookup = (rows, id) => { const r = rows.find((row) => row[0] === id); return r ? r[1] : null; };
const classOf = (facts, id) => { if (id < 0) return null; const w = facts.weapon(id); return w ? w.wepType : null; };

/// CalcCorrectGraph in single precision (crates/er-duel-rules-core/src/facts.rs `CorrectGraph::at`).
function correctGraphAt(g, x) {
    const f = Math.fround;
    const sv = g.stageMaxVal, gv = g.stageMaxGrowVal;
    x = f(Math.min(x, sv[4]));
    if (!(x > 0)) return gv[0];
    let i = 0;
    while (i < 4 && x > sv[i + 1]) i += 1;
    if (i === 4) return gv[4];
    const lo = sv[i], span = f(sv[i + 1] - sv[i]), dg = f(gv[i + 1] - gv[i]), e = g.adjPtMaxGrowVal[i];
    let v;
    if (e < 0) {
        const p = f(Math.pow(f(f(span - f(x - lo)) / span), -e));
        v = f(gv[i] + f(f(1 - p) * dg));
    } else {
        const p = f(Math.pow(f(f(x - lo) / span), e));
        v = f(gv[i] + f(p * dg));
    }
    return Math.min(Math.max(v, Math.min(gv[i], gv[i + 1])), Math.max(gv[i], gv[i + 1]));
}

/// Burden over max load, or null when an item has no row (crates/er-duel-rules-core/src/facts.rs).
function equipLoad(p, facts) {
    const f = Math.fround;
    let burden = 0;
    for (const id of weapons(p)) { const w = facts.weapon(id); if (!w) return null; burden = f(burden + w.weight); }
    for (const id of p.armor) { if (id < 0) continue; const w = facts.armorWeight(id); if (w === null) return null; burden = f(burden + w); }
    for (const id of p.talismans) { if (id < 0) continue; const w = facts.talismanWeight(id); if (w === null) return null; burden = f(burden + w); }
    const ids = new Set(p.speffects);
    for (const id of p.talismans) if (id >= 0) facts.residentEffects('talisman', id).forEach((e) => ids.add(e));
    for (const id of p.armor) if (id >= 0) facts.residentEffects('armor', id).forEach((e) => ids.add(e));
    let rate = 1, addEnd = 0;
    for (const id of ids) {
        if (id <= 0) continue;
        const e = facts.effect(id);
        if (!e) continue;
        if (e.equipWeightRate > 0) rate = f(rate * e.equipWeightRate);
        addEnd += e.addEndurance;
    }
    const base = facts.equipLoadBase(Math.min(p.endurance + addEnd, 99));
    if (base === null) return null;
    const max = f(base * rate);
    return { burden, max, ratio: max > 0 ? f(burden / max) : 0, light: max > 0 && f(burden / max) <= LIGHT_LOAD_CEILING };
}

function buffRestrictedSetup(p, hand, facts) {
    const id = active(p, hand);
    const cls = classOf(facts, id);
    if (cls === null) return null;
    const base = weaponBase(id);
    const W = T.weapon, C = T.wepType;
    if (base === W.IDUS_SWORD) return 'Idus Sword';
    if ([C.SMALL_SHIELD, C.MEDIUM_SHIELD, C.GREATSHIELD, C.THRUSTING_SHIELD].includes(cls)) return 'Shields';
    if (twoHanded(p) === hand) {
        if (cls === C.THRUSTING_SWORD) return '2H Thrusting Sword';
        if (base === W.STONE_CLUB || base === W.CLUB) return '2H Stone Club/Club';
        if (base === W.SWORD_LANCE) return '2H Sword Lance';
        if (base === W.RAPTOR_TALONS) return '2H Raptors Talons';
        if (cls === C.GREAT_SPEAR && base !== W.MOHGWYNS_SACRED_SPEAR) return '2H Greatspears';
        if (base === W.STAR_FIST) return '2H Star Fists';
        if (cls === C.TWINBLADE) return '2H Twinblades';
        if (base === W.REVERSE_BLADED_SWORD) return '2H Reverse Blades';
        return null;
    }
    if (hand === 'left' && twoHanded(p) === null && cls === C.DAGGER) return 'Offhand Daggers';
    if (hand === 'left' && twoHanded(p) === null && cls === C.AXE) return 'Offhand Hatchets';
    if (hand === 'right' && twoHanded(p) === null && base === W.WAKIZASHI) return 'Mainhand 1H Wakizashi';
    return null;
}

/// Every rule `p` breaks this frame except the per-round flask count.
function check(p, facts) {
    const out = [];
    // rules.rs violation / equipped / event: something done, what is equipped (judged only once an
    // opponent shares the world), and one discrete act that is never merged with another.
    const v = (rule, detail) => out.push({ rule, detail, onEquipment: false, event: false });
    const eq = (rule, detail) => out.push({ rule, detail, onEquipment: true, event: false });
    const ev = (rule, detail) => out.push({ rule, detail, onEquipment: false, event: true });
    const W = T.weapon, C = T.wepType, S = T.skill, TAL = T.talisman;
    if (p.level !== 0 && p.level !== RUNE_LEVEL) eq('RuneLevel', `Rune Level ${p.level}`);

    // Weapons.
    const shieldClasses = [C.SMALL_SHIELD, C.MEDIUM_SHIELD, C.GREATSHIELD, C.THRUSTING_SHIELD];
    const bannedWeapons = [[W.SMITHSCRIPT_CIRQUE, 'SmithscriptCirque'], [W.CLAWS_OF_NIGHT, 'ClawsOfNight'],
        [W.SMITHSCRIPT_SHIELD, 'SmithscriptShield'], [W.SPREAD_CROSSBOW, 'SpreadCrossbow'],
        [W.SMITHSCRIPT_DAGGER, 'SmithscriptDagger'], [W.MILADY, 'Milady']];
    for (const id of weapons(p)) {
        const base = weaponBase(id);
        for (const [banned, rule] of bannedWeapons) if (base === banned) eq(rule, 'equipped');
        const w = facts.weapon(id);
        if (!w) continue;
        if (w.wepType === C.THRUSTING_SHIELD) eq('ThrustingShield', 'equipped');
        if (shieldClasses.includes(w.wepType) && w.wepType !== C.THRUSTING_SHIELD && w.physGuardCut > SHIELD_PHYS_GUARD_CAP) {
            eq('ShieldGuard', `${Math.round(w.physGuardCut)}% physical guard`);
        }
        const regen = T.regenEquipment.find((r) => r[0] === 'Shield' && r[1] === base);
        if (regen) eq('PassiveRegen', regen[2]);
    }
    const two = twoHandedWeapon(p);
    if (two >= 0) {
        const base = weaponBase(two), lightning = weaponAffinity(two) === T.affinity.LIGHTNING;
        if (base === W.BACKHAND_BLADE) v('TwoHandedBackhandBlade', 'two-handed');
        if (base === W.REVERSE_BLADED_SWORD && lightning) v('LightningReverseBlades', 'two-handed');
        if (base === W.RAPTOR_TALONS && lightning) v('LightningRaptorTalons', 'two-handed');
        if (base === W.CLEANROT_KNIGHTS_SWORD) v('TwoHandedCleanrot', 'two-handed');
        if (base === W.PATA) v('PataNotOffhand', 'two-handed');
        if ((base === W.STONE_CLUB || base === W.CLUB) && facts.weapon(two) && facts.weapon(two).inflictsStatus) {
            v('StoneClubStatus', 'status infusion');
        }
    }
    if (weaponBase(active(p, 'right')) === W.PATA) v('PataNotOffhand', 'in the right hand');
    if (p.armStyle === ARM.ONE) {
        const right = active(p, 'right'), left = active(p, 'left');
        if (right >= 0 && left >= 0) {
            const rc = classOf(facts, right), lc = classOf(facts, left);
            if (rc !== null && rc === lc) {
                if (rc === C.BACKHAND_BLADE) v('PowerstanceBackhandBlade', 'powerstanced');
                if (weaponBase(left) === W.PATA && weaponBase(right) !== W.PATA) v('PataNotOffhand', 'powerstanced');
            }
        }
    }

    // Armour and talismans.
    for (const id of p.armor) {
        if (id < 0) continue;
        const name = lookup(T.bannedArmor, id);
        if (name) eq('BannedArmor', name);
        const regen = T.regenEquipment.find((r) => r[0] === 'Armor' && r[1] === id);
        if (regen) eq('PassiveRegen', regen[2]);
    }
    const holdsSwordLance = ['right', 'left'].some((h) => weaponBase(active(p, h)) === W.SWORD_LANCE);
    const iframe = { [TAL.CRUCIBLE_FEATHER]: 'Crucible Feather Talisman', [TAL.ALL_CRUCIBLES]: 'Talisman of All Crucibles',
        [TAL.FINE_CRUCIBLE_FEATHER]: 'Fine Crucible Feather Talisman' };
    for (const id of p.talismans) {
        if (id < 0) continue;
        if (iframe[id]) eq('IFrameTalisman', iframe[id]);
        if (id === TAL.RED_FEATHER) eq('RedFeather', 'equipped');
        if (id === TAL.TWO_HANDED_SWORD) eq('TwoHandedSwordTalisman', 'equipped');
        if (id === TAL.SPEAR && holdsSwordLance) eq('SpearTalismanSwordLance', 'equipped');
        const regen = T.regenEquipment.find((r) => r[0] === 'Talisman' && r[1] === id);
        if (regen) eq('PassiveRegen', regen[2]);
    }

    // Crystal tears.
    const tearRule = { Named: 'NamedTear', Stat: 'StatTear', Damage: 'DamageTear', Heal: 'Healing' };
    for (const id of p.physick) {
        if (id < 0) continue;
        const tear = T.bannedTears.find((r) => r[0] === id);
        if (tear) eq(tearRule[tear[2]], tear[1]);
    }

    // SpEffects.
    const buffed = { left: false, right: false };
    for (const id of p.speffects) {
        // A banned tear in use: the physick put its effect on the player.
        const used = T.tearEffects.find((r) => r[0] === id);
        if (used) v(tearRule[used[3]], used[2]);
        const buff = lookup(T.bodyBuffs, id);
        if (buff) v('PlayerBuff', buff);
        const heal = lookup(T.heals, id);
        if (heal) v('Healing', heal);
        if (T.rotGrease.includes(id)) v('RotGrease', 'rot grease');
        const spirit = lookup(T.spiritAshTriggers, id);
        if (spirit) v('SpiritAshes', spirit);
        const e = facts.effect(id);
        const hand = !e ? null : e.category === CATEGORY_RIGHT_WEAPON_BUFF ? 'right' : e.category === CATEGORY_LEFT_WEAPON_BUFF ? 'left' : null;
        if (hand === null) continue;
        buffed[hand] = true;
        const wid = active(p, hand);
        if (wid < 0) continue;
        const base = weaponBase(wid);
        // A grease takes only on an isEnhance row, so one on any other weapon was carried there.
        if (T.greaseBuffs.includes(id)) {
            const wf = facts.weapon(wid);
            if (wf && wf.takesGrease === false) v('BuffTransfer', `grease on weapon ${wid}, which takes none`);
        }
        if (T.sleepGrease.includes(id)) { const r = lookup(T.rippleWeapons, base); if (r) v('SleepGreaseRipple', r); }
        if (T.poisonGrease.includes(id)) {
            if (hand === 'left') v('OffhandPoisonGrease', 'poison grease on the left weapon');
            if (base === W.SHAMSHIR && twoHanded(p) === hand) v('PoisonGreaseShamshir', 'two-handed Shamshir');
        }
        if (T.chillingMistBuffs.includes(id) && weaponAffinity(wid) === T.affinity.COLD) v('ChillingMistCold', 'cold weapon');
    }
    for (const hand of ['left', 'right']) {
        if (!buffed[hand]) continue;
        const setup = buffRestrictedSetup(p, hand, facts);
        if (setup) v('WeaponBuffRestricted', `weapon buff on ${setup}`);
    }

    // Skills, on use.
    const bannedSkills = [[S.BLOODHOUNDS_STEP, "Bloodhound's Step"], [S.QUICKSTEP, 'Quickstep'],
        [S.RAPTOR_OF_THE_MISTS, 'Raptor of the Mists'], [S.ENDURE, 'Endure'], [S.OATH_OF_VENGEANCE, 'Oath of Vengeance'],
        [S.ROYAL_KNIGHTS_RESOLVE, "Royal Knight's Resolve"], [S.DETERMINATION, 'Determination'],
        [S.FLAMING_STRIKE, 'Flaming Strike'], [S.STORM_STOMP, 'Storm Stomp'], [S.STORM_ASSAULT, 'Storm Assault'],
        [S.STORMCALLER, 'Stormcaller'], [S.SWIFT_SLASH, 'Swift Slash'], [S.BLIND_SPOT, 'Blind Spot'],
        [S.NEEDLE_PIERCER, 'Needle Piercer'], [S.RED_BEAR_HUNT, 'Red Bear Hunt'], [S.BARBARIC_ROAR, 'Barbaric Roar'],
        [S.SHRIEK_OF_SORROW, 'Shriek of Sorrow'], [S.PAINFUL_STRIKE, 'Painful Strikes'], [S.SAVAGE_CLAWS, 'Savage Claws']];
    const ashTalismans = [TAL.WARRIOR_JAR_SHARD, TAL.SHARD_OF_ALEXANDER, TAL.RELLANAS_CAMEO, TAL.GODFREY_ICON];
    for (const anim of p.newAnims) {
        const sc = skillClip(anim);
        if (sc === null) continue;
        const [typeNew, clip] = sc;
        const banned = bannedSkills.find((s) => s[0] === typeNew);
        if (banned) ev('BannedAsh', banned[1]);
        const parry = lookup(T.parrySkills, typeNew);
        if (parry) ev('BannedAsh', `All Parries: ${parry}`);
        if (typeNew === S.SWORD_DANCE && T.swordDanceFastClips.includes(clip)) ev('BannedAsh', 'Fast Sword Dance');
        if (typeNew === S.SPINNING_SLASH && classOf(facts, nonShieldSkillWeapon(p)) === C.LIGHT_GREATSWORD) {
            ev('SpinningSlashLightGreatsword', 'used');
        }
        // "2H Stone Club/Club: Any Status Application", through a skill's own status.
        const statusSkill = lookup(T.statusSkills, typeNew);
        const skillBase = weaponBase(nonShieldSkillWeapon(p));
        if (statusSkill && twoHanded(p) !== null && (skillBase === W.STONE_CLUB || skillBase === W.CLUB)) ev('StoneClubStatus', statusSkill);
        if (typeNew === S.MULETA && p.talismans.some((id) => ashTalismans.includes(id))) ev('MuletaTalismans', 'used');
    }

    // Projectiles.
    for (const bullet of p.bullets) {
        if (T.fanDaggerBullets.includes(bullet)) v('FanDaggers', 'thrown');
        for (const [spell, bullets] of T.spellBullets) if (bullets.includes(bullet)) v('BannedSpell', spell);
    }

    if (p.riding) v('SpectralSteed', 'riding Torrent');
    // Settings only this machine can read about its own player (LocalSettings in player.rs).
    const s = p.localSettings;
    if (s) {
        if (s.betterMultiplayer && s.manualAttackAiming) eq('ManualAttackAiming', 'enabled');
        if (s.targetFps > UNLOCKED_TARGET_FPS || s.measuredFps > UNLOCKED_MEASURED_FPS) {
            eq('UnlockedFps', `target ${s.targetFps.toFixed(0)} fps, running at ${s.measuredFps.toFixed(0)}`);
        }
    }
    // "Light Roll (<30% Equip Load)": a roll performed at that load, each one its own break.
    const load = p.newAnims.some(isRoll) ? equipLoad(p, facts) : null;
    if (load && load.light) ev('LightRoll', `rolled at ${(load.ratio * 100).toFixed(1)}% equip load`);
    return out;
}

/// rules.rs is_roll: c0000's roll animations, eight directions per block. Measured live 2026-10-16:
/// the same 27100 at no armor and at a full set, so only the load tells a light roll. The
/// single-direction 27000..27030 beside them are backsteps.
function isRoll(anim) {
    const inBlock = (anim >= 27100 && anim <= 27137) || (anim >= 27140 && anim <= 27143)
        || (anim >= 27500 && anim <= 27507) || (anim >= 27600 && anim <= 27607);
    return inBlock && anim % 10 <= 7;
}

const ATTRIBUTE_NAMES = ['Vigor', 'Mind', 'Endurance', 'Strength', 'Dexterity', 'Intelligence', 'Faith', 'Arcane'];
const slotChanges = (name, old, now) => old.flatMap((o, i) => (o === now[i] ? [] : [`${name} ${i + 1}: ${o} -> ${now[i]}`]));
const buildOf = (p) => ({ level: p.level, attributes: p.attributes.slice(), weaponsLeft: p.weaponsLeft.slice(), weaponsRight: p.weaponsRight.slice(),
    armor: p.armor.slice(), talismans: p.talismans.slice(), physick: p.physick.slice() });

/// Which breaks become a notice, the blue flask count per round, and a build that changes once two
/// players share a world (referee.rs).
class Referee {
    constructor() { this.lastSeen = new Map(); this.flasks = new Map(); this.alive = new Map(); this.entered = new Map(); this.hands = {}; this.aliveSince = new Map(); }

    // Port of Referee::carried_buffs: the local player only.
    carriedBuffs(nowMs, p, facts) {
        const out = [];
        for (const hand of ['left', 'right']) {
            const cat = hand === 'left' ? CATEGORY_LEFT_WEAPON_BUFF : CATEGORY_RIGHT_WEAPON_BUFF;
            const buffs = p.speffects.filter((id) => { const e = facts.effect(id); return e !== null && e.category === cat; });
            const weapon = active(p, hand);
            const h = this.hands[hand] || (this.hands[hand] = { weapon, buffs: [], carried: [], changedAt: 0 });
            if (weapon !== h.weapon) { h.carried = h.buffs; h.changedAt = nowMs; }
            if (h.carried.length && nowMs - h.changedAt >= BUFF_TRANSFER_GRACE_MS) {
                if (h.carried.some((id) => buffs.includes(id))) out.push({ rule: 'BuffTransfer', detail: `weapon buff kept across a weapon change to ${weapon}` });
                h.carried = [];
            }
            h.weapon = weapon;
            h.buffs = buffs;
        }
        return out;
    }

    // Port of Referee::build_changes.
    buildChanges(p) {
        if (p.level === 0) return [];
        const now = buildOf(p);
        const entered = this.entered.get(p.key);
        if (!entered) { this.entered.set(p.key, { build: now, armorChanged: false }); return []; }
        const old = entered.build;
        const out = [];
        const stats = ATTRIBUTE_NAMES.flatMap((n, i) => (old.attributes[i] === now.attributes[i] ? [] : [`${n} ${old.attributes[i]} -> ${now.attributes[i]}`]));
        if (old.level !== now.level) stats.unshift(`level ${old.level} -> ${now.level}`);
        if (stats.length) out.push({ rule: 'Respec', detail: stats.join(', ') });
        const gear = slotChanges('right weapon', old.weaponsRight, now.weaponsRight)
            .concat(slotChanges('left weapon', old.weaponsLeft, now.weaponsLeft), slotChanges('talisman', old.talismans, now.talismans),
                slotChanges('tear', old.physick, now.physick));
        const armor = slotChanges('armor', old.armor, now.armor);
        if (armor.length === 0) {
            if (entered.armorChanged) { entered.armorChanged = false; out.push({ rule: 'ArmorSwapPoiseReset', detail: 'armor taken off and put back on' }); }
        } else {
            entered.armorChanged = true;
            gear.push(...armor);
        }
        if (gear.length) out.push({ rule: 'SwitchingBuilds', detail: gear.join(', ') });
        return out;
    }

    observe(nowMs, players, facts) {
        const present = new Set(players.map((p) => p.key));
        for (const k of [...this.alive.keys()]) if (!present.has(k)) this.alive.delete(k);
        for (const k of [...this.flasks.keys()]) if (!present.has(k)) this.flasks.delete(k);
        const inMatch = players.length >= 2;
        if (inMatch) { for (const k of [...this.entered.keys()]) if (!present.has(k)) this.entered.delete(k); } else this.entered.clear();
        for (const k of [...this.aliveSince.keys()]) if (!present.has(k)) this.aliveSince.delete(k);
        let roundOver = false;
        for (const p of players) {
            const alive = p.hp > 0;
            const was = this.alive.get(p.key);
            if (was === true && !alive) roundOver = true;
            if (alive && was !== true) this.aliveSince.set(p.key, nowMs);
            this.alive.set(p.key, alive);
        }
        // A session is Better Multiplayer when the local game has the module loaded.
        const betterMultiplayer = players.some((p) => p.localSettings && p.localSettings.betterMultiplayer);
        if (roundOver) for (const f of this.flasks.values()) f.used = 0;
        const alerts = [];
        for (const p of players) {
            const violations = check(p, facts);
            const drinking = p.speffects.some((id) => T.ceruleanFlask.includes(id));
            const f = this.flasks.get(p.key) || { drinking: false, used: 0 };
            if (drinking && !f.drinking) f.used += 1;
            f.drinking = drinking;
            this.flasks.set(p.key, f);
            if (f.used > CERULEAN_FLASK_LIMIT) violations.push({ rule: 'CeruleanFlasks', detail: `Flask of Cerulean Tears number ${f.used} this round` });
            if (inMatch) violations.push(...this.buildChanges(p));
            const since = this.aliveSince.get(p.key);
            if (inMatch && betterMultiplayer && p.hp > 0 && !runeArcActive(p) && since !== undefined && nowMs - since >= RUNE_ARC_GRACE_MS) {
                violations.push({ rule: 'RuneArc', detail: `no active Rune Arc ${RUNE_ARC_GRACE_MS / 1000} s into the round` });
            }
            if (p.isLocal) violations.push(...this.carriedBuffs(nowMs, p, facts));
            for (const vio of violations) {
                if (vio.onEquipment && !inMatch) continue;
                let fresh = true;
                if (!vio.event) {
                    const key = `${p.key}|${vio.rule}|${vio.detail}`;
                    const last = this.lastSeen.get(key);
                    this.lastSeen.set(key, nowMs);
                    fresh = last === undefined || nowMs - last >= REARM_MS;
                }
                if (fresh) alerts.push({ key: p.key, player: p.name, isLocal: p.isLocal, rule: vio.rule, detail: vio.detail });
            }
        }
        for (const [k, last] of [...this.lastSeen]) if (nowMs - last >= REARM_MS) this.lastSeen.delete(k);
        return alerts;
    }

    flasksUsed(key) { const f = this.flasks.get(key); return f ? f.used : 0; }
}

/// The player as listed: the character, and for whoever runs this copy their Steam name beside it,
/// so a file read later says which account broke the rule; "(you)" only when Steam could not be
/// asked (feed.rs player_label).
function playerLabel(player, isLocal, steamName) {
    if (!isLocal) return player;
    return steamName ? `${player} (${steamName})` : `${player} (you)`;
}

function headline(a, steamName) {
    return `Attention: Rule break: ${playerLabel(a.player, a.isLocal, steamName)}. ${RULE_TEXT[a.rule]}`;
}

/// Every rule break, one line each, kept across runs; the same file and line as the DLL's
/// (`er_duel_rules_core::feed::Entry::file_line`).
const BREAKS_FILE_NAME = 'er-duel-rules-breaks.txt';

/// `YYYY-MM-DD HH:MM:SS  <player>  <rule>  --  <detail>`. `when` is the local wall-clock time as
/// `{ year, month, day, hour, minute, second }`, month counted from 1.
function breakLine(when, a, steamName) {
    const two = (n) => String(n).padStart(2, '0');
    const stamp = `${when.year}-${two(when.month)}-${two(when.day)} ` +
        `${two(when.hour)}:${two(when.minute)}:${two(when.second)}`;
    const line = `${stamp}  ${playerLabel(a.player, a.isLocal, steamName)}  ${RULE_TEXT[a.rule]}`;
    return a.detail ? `${line}  --  ${a.detail}` : line;
}

// ============================================================================================
// Reading the game. Everything below runs inside Frida only.
// ============================================================================================
if (typeof Process === 'undefined') {
    module.exports = { T, check, equipLoad, correctGraphAt, Referee, headline, breakLine, playerLabel, skillClip, animStarts, weaponBase, weaponAffinity, RULE_TEXT };
} else {
    main();
}

function main() {
    const game = Process.getModuleByName('eldenring.exe');
    const logPath = game.path.replace(/[^\\/]*$/, '') + 'er-duel-rules-frida.log';
    let logFile = null;
    try { logFile = new File(logPath, 'a'); } catch (e) { /* console only */ }
    const say = (line) => {
        const stamped = `[${new Date().toISOString()}] ${line}`;
        console.log(stamped);
        if (logFile !== null) { try { logFile.write(stamped + '\n'); logFile.flush(); } catch (e) { /* keep going */ } }
    };

    const peHeader = game.base.add(game.base.add(0x3c).readU32());
    const stamp = peHeader.add(8).readU32();
    if (stamp !== BUILD.timeDateStamp) {
        say(`er-duel-rules: REFUSING TO RUN -- this agent is written for ${BUILD.label} (exe timestamp ` +
            `0x${BUILD.timeDateStamp.toString(16)}) and this game is 0x${stamp.toString(16)}. Every address it reads ` +
            'belongs to that build; reading them on another one reports nonsense. Nothing was hooked.');
        return;
    }
    const at = (rva) => game.base.add(rva);
    const bytesAt = (p, n) => Array.from(new Uint8Array(p.readByteArray(n))).map((b) => b.toString(16).padStart(2, '0')).join(' ');

    // -- params ------------------------------------------------------------------------------
    const paramCache = {};
    function paramFile(name) {
        const def = PARAM_FIELDS[name];
        const cached = paramCache[name];
        if (cached !== undefined) return cached;
        const repo = at(RVA.soloParamRepository).readPointer();
        if (repo.isNull()) return null;
        const holder = repo.add(OFF.sprHolders + def.index * OFF.holderSize);
        if (holder.readU32() < 1) return null;
        const resCap = holder.add(OFF.holderResCaps).readPointer();
        if (resCap.isNull()) return null;
        const fd4 = resCap.add(OFF.paramResCap).readPointer();
        if (fd4.isNull()) return null;
        const file = fd4.add(OFF.fd4ParamResCapData).readPointer();
        if (file.isNull()) return null;
        const format2d = file.add(0x2d).readU8();
        const structName = (format2d & 0x80) ? file.add(file.add(0x10).readU32()).readCString() : file.add(0xc).readCString();
        if (structName !== def.struct) {
            say(`er-duel-rules: param slot ${def.index} holds ${structName}, expected ${def.struct}; that table is not read`);
            paramCache[name] = null;
            return null;
        }
        if ((format2d & 0x04) === 0) {
            say(`er-duel-rules: ${def.struct} does not use 64-bit row offsets; that table is not read`);
            paramCache[name] = null;
            return null;
        }
        const rowCount = file.add(0xa).readU16();
        const afterName = file.sub(0x10).readU32();
        const lookupTable = file.add(Math.ceil(afterName / 0x10) * 0x10);
        const pf = { file, rowCount, lookupTable, rows: new Map() };
        paramCache[name] = pf;
        return pf;
    }
    function row(name, id) {
        const pf = paramFile(name);
        if (pf === null || id < 0) return null;
        if (pf.rows.has(id)) return pf.rows.get(id);
        let lo = 0, hi = pf.rowCount - 1, found = null;
        while (lo <= hi) {
            const mid = (lo + hi) >> 1;
            const e = pf.lookupTable.add(mid * 8);
            const key = e.readU32();
            if (key === id) { found = e.add(4).readU32(); break; }
            if (key < id) lo = mid + 1; else hi = mid - 1;
        }
        let ptr = null;
        if (found !== null) {
            const descriptor = pf.file.add(0x40 + found * 24);
            if (descriptor.readU32() === id) ptr = pf.file.add(descriptor.add(8).readU64().toNumber());
        }
        pf.rows.set(id, ptr);
        return ptr;
    }
    function field(name, ptr, f) {
        const d = PARAM_FIELDS[name].fields[f];
        const p = ptr.add(d[0]);
        switch (d[1]) {
            case 'f32': return p.readFloat();
            case 'u8': return p.readU8();
            case 's8': return p.readS8();
            case 'u16': return p.readU16();
            case 's16': return p.readS16();
            case 'u32': return p.readU32();
            case 'bit': return (p.readU8() >> d[2]) & 1;
            default: return p.readS32();
        }
    }
    let graph220 = null;
    const facts = {
        weapon(id) {
            const r = row('EquipParamWeapon', weaponRow(id));
            if (r === null) return null;
            const reinforce = row('ReinforceParamWeapon', field('EquipParamWeapon', r, 'reinforceTypeId') + weaponLevel(id));
            const rate = reinforce === null ? 1 : field('ReinforceParamWeapon', reinforce, 'physicsGuardCutRate');
            return {
                wepType: field('EquipParamWeapon', r, 'wepType'),
                weight: field('EquipParamWeapon', r, 'weight'),
                physGuardCut: Math.fround(field('EquipParamWeapon', r, 'physGuardCutRate') * rate),
                inflictsStatus: ['spEffectBehaviorId0', 'spEffectBehaviorId1', 'spEffectBehaviorId2']
                    .some((f) => field('EquipParamWeapon', r, f) > 0),
                takesGrease: field('EquipParamWeapon', r, 'isEnhance') === 1,
            };
        },
        armorWeight(id) { const r = row('EquipParamProtector', id); return r === null ? null : field('EquipParamProtector', r, 'weight'); },
        talismanWeight(id) { const r = row('EquipParamAccessory', id); return r === null ? null : field('EquipParamAccessory', r, 'weight'); },
        residentEffects(kind, id) {
            if (kind === 'talisman') {
                const r = row('EquipParamAccessory', id);
                if (r === null) return [];
                const ids = ['residentSpEffectId1', 'residentSpEffectId2', 'residentSpEffectId3', 'residentSpEffectId4']
                    .map((f) => field('EquipParamAccessory', r, f));
                if (field('EquipParamAccessory', r, 'refCategory') === 2) ids.push(field('EquipParamAccessory', r, 'refId'));
                return ids;
            }
            const r = row('EquipParamProtector', id);
            if (r === null) return [];
            return ['residentSpEffectId', 'residentSpEffectId2', 'residentSpEffectId3'].map((f) => field('EquipParamProtector', r, f));
        },
        effect(id) {
            const r = row('SpEffectParam', id);
            if (r === null) return null;
            return {
                category: field('SpEffectParam', r, 'spCategory'),
                equipWeightRate: field('SpEffectParam', r, 'equipWeightChangeRate'),
                addEndurance: field('SpEffectParam', r, 'addEndureStatus'),
            };
        },
        equipLoadBase(endurance) {
            if (graph220 === null) {
                const r = row('CalcCorrectGraph', 220);
                if (r === null) return null;
                const read = (prefix) => [0, 1, 2, 3, 4].map((i) => field('CalcCorrectGraph', r, prefix + i));
                graph220 = { stageMaxVal: read('stageMaxVal'), stageMaxGrowVal: read('stageMaxGrowVal'), adjPtMaxGrowVal: read('adjPt_maxGrowVal') };
            }
            return correctGraphAt(graph220, endurance);
        },
    };

    // -- players -----------------------------------------------------------------------------
    const handleKey = (p) => p.readU64().toString();
    const queues = new Map();
    const applied = new Map();
    let playerAddresses = new Map();
    let pending = 0;

    // Better Multiplayer can be injected after the agent attaches, so the module is looked up
    // again every few seconds rather than once.
    const BETTER_MULTIPLAYER_MODULE = 'bettermultiplayer.dll';
    let bmp = { at: -Infinity, loaded: false };
    let lastSettings = '';
    function localSettings(gdm) {
        const now = Date.now();
        if (now - bmp.at > 5000) bmp = { at: now, loaded: Process.findModuleByName(BETTER_MULTIPLAYER_MODULE) !== null };
        const settings = gdm.add(OFF.gdmGameSettings).readPointer();
        const flipper = at(RVA.flipper).readPointer();
        const spf = flipper.isNull() ? 0 : flipper.add(OFF.flipperTargetSpf).readFloat();
        const s = {
            manualAttackAiming: !settings.isNull() && settings.add(OFF.settingsManualAttackAiming).readU8() !== 0,
            betterMultiplayer: bmp.loaded,
            targetFps: spf > 0 ? 1 / spf : 0,
            measuredFps: flipper.isNull() ? 0 : flipper.add(OFF.flipperMeasuredFps).readFloat(),
        };
        const line = `manual_attack_aiming=${s.manualAttackAiming} better_multiplayer=${s.betterMultiplayer} target_fps=${s.targetFps.toFixed(1)}`;
        if (line !== lastSettings) { say(`settings: ${line} measured_fps=${s.measuredFps.toFixed(1)}`); lastSettings = line; }
        return s;
    }

    function readPlayers() {
        const wcm = at(RVA.worldChrMan).readPointer();
        const gdm = at(RVA.gameDataMan).readPointer();
        if (wcm.isNull() || gdm.isNull()) { queues.clear(); playerAddresses = new Map(); return []; }
        const main = wcm.add(OFF.wcmMainPlayer).readPointer();
        const set = wcm.add(OFF.wcmPlayerChrSet);
        const capacity = Math.min(set.add(OFF.chrSetCapacity).readU32(), 64);
        const entries = set.add(OFF.chrSetEntries).readPointer();
        const remoteList = gdm.add(OFF.gdmRemoteList).readPointer();
        const remoteStates = gdm.add(OFF.gdmRemoteStates).readPointer();

        const bullets = new Map();
        const bm = at(RVA.bulletManager).readPointer();
        if (!bm.isNull()) {
            let b = bm.add(OFF.bmHead).readPointer();
            for (let n = 0; n < WALK_LIMIT && !b.isNull(); n++) {
                const owner = handleKey(b.add(OFF.bulletOwner));
                const id = OFF.bulletParamIds.map((o) => b.add(o).readS32()).find((x) => x > 0);
                if (id !== undefined) { if (!bullets.has(owner)) bullets.set(owner, []); bullets.get(owner).push(id); }
                b = b.add(OFF.bulletNext).readPointer();
            }
        }

        const players = [];
        const seen = new Set();
        const addresses = new Map();
        pending = 0;
        for (let i = 0; i < capacity; i++) {
            const chr = entries.add(i * OFF.chrSetEntryStride).readPointer();
            if (chr.isNull()) continue;
            try {
                const key = handleKey(chr.add(OFF.chrHandle));
                const isLocal = chr.equals(main);
                const pgd = chr.add(OFF.playerGameData).readPointer();
                if (!isLocal) {
                    const index = pgd.sub(remoteList).toInt32() / OFF.pgdSize;
                    if (!Number.isInteger(index) || index < 0 || index >= REMOTE_SLOTS ||
                        remoteStates.add(index).readU8() !== REMOTE_FULLY_SYNCED) { pending += 1; continue; }
                }
                seen.add(key);
                addresses.set(chr.toString(), key);
                const asm = pgd.add(OFF.pgdChrAsm);
                const ids = (k) => asm.add(OFF.asmParamIds + k * 4).readS32();
                const tear = (k) => { const raw = pgd.add(OFF.pgdPhysick + k * 4).readU32(); return raw === 0xffffffff ? NO_ITEM : raw & 0x0fffffff; };
                const modules = chr.add(OFF.chrModules).readPointer();
                const timeAct = modules.add(OFF.modTimeAct).readPointer();
                const history = [];
                for (let q = 0; q < OFF.timeActCount; q++) {
                    const entry = timeAct.add(OFF.timeActQueue + q * OFF.timeActEntry);
                    history.push([entry.readS32(), entry.add(OFF.timeActPlayTime).readFloat()]);
                }
                const { starts: newAnims, cursor } = animStarts(queues.get(key), history, timeAct.add(OFF.timeActWriteIdx).readU32());
                if (cursor) queues.set(key, cursor);
                const speffects = [];
                let entry = chr.add(OFF.chrSpecialEffect).readPointer().add(OFF.speHead).readPointer();
                for (let n = 0; n < WALK_LIMIT && !entry.isNull(); n++) {
                    speffects.push(entry.add(OFF.speEntryParamId).readS32());
                    entry = entry.add(OFF.speEntryNext).readPointer();
                }
                // Effects applied since the last tick that have already ended (a heal, a flask).
                for (const id of applied.get(chr.toString()) || []) if (!speffects.includes(id)) speffects.push(id);
                const nameChars = [];
                for (let c = 0; c < OFF.pgdNameChars; c++) {
                    const ch = pgd.add(OFF.pgdName + c * 2).readU16();
                    if (ch === 0) break;
                    nameChars.push(ch);
                }
                const rideNode = modules.add(OFF.modRide).readPointer().add(OFF.rideNode).readPointer();
                players.push({
                    key,
                    name: String.fromCharCode(...nameChars),
                    isLocal,
                    level: pgd.add(OFF.pgdLevel).readU32(),
                    endurance: pgd.add(OFF.pgdEndurance).readU32(),
                    attributes: Array.from({ length: 8 }, (_, k) => pgd.add(OFF.pgdAttributes + k * 4).readU32()),
                    armStyle: asm.add(OFF.asmArmStyle).readU32(),
                    leftSlot: asm.add(OFF.asmLeftSlot).readU32(),
                    rightSlot: asm.add(OFF.asmRightSlot).readU32(),
                    weaponsLeft: [ids(0), ids(2), ids(4)],
                    weaponsRight: [ids(1), ids(3), ids(5)],
                    armor: [ids(12), ids(13), ids(14), ids(15)],
                    talismans: [ids(17), ids(18), ids(19), ids(20)],
                    physick: [tear(0), tear(1), tear(2)],
                    speffects,
                    newAnims,
                    bullets: bullets.get(key) || [],
                    hp: modules.add(OFF.modData).readPointer().add(OFF.dataHp).readS32(),
                    riding: rideNode.isNull() ? false : rideNode.add(OFF.rideState).readU32() !== 0,
                    localSettings: isLocal ? localSettings(gdm) : null,
                    runeArcFlag: pgd.add(OFF.pgdRuneArcActive).readU8() !== 0,
                });
            } catch (e) {
                // A character torn down under the read; it is simply not judged this tick.
            }
        }
        for (const k of [...queues.keys()]) if (!seen.has(k)) queues.delete(k);
        applied.clear();
        playerAddresses = addresses;
        return players;
    }

    // -- hooks -------------------------------------------------------------------------------
    const hooks = [];
    function hook(name, callbacks) {
        const target = at(RVA[name]);
        const got = bytesAt(target, 16);
        if (got !== PROLOGUE[name]) {
            say(`er-duel-rules: ${name} starts with ${got}, expected ${PROLOGUE[name]}; not hooked`);
            return false;
        }
        hooks.push(Interceptor.attach(target, callbacks));
        return true;
    }

    // The time comes from Windows rather than `Date`: Frida's runtime ignores daylight saving on
    // Windows, so `Date#getHours` ran an hour behind the clock (measured 2026-10-16, 08:27 against
    // 09:27 PDT). `GetLocalTime` is also what the DLL stamps with.
    const getLocalTime = new NativeFunction(Process.getModuleByName('kernel32.dll').getExportByName('GetLocalTime'), 'void', ['pointer']);
    const localStamp = () => {
        const st = Memory.alloc(16);  // SYSTEMTIME: eight u16 fields.
        getLocalTime(st);
        const field = (index) => st.add(index * 2).readU16();
        return { year: field(0), month: field(1), day: field(3), hour: field(4), minute: field(5), second: field(6) };
    };
    const breaksPath = game.path.replace(/[^\\/]*$/, '') + BREAKS_FILE_NAME;
    const recordBreak = (line) => {
        try {
            const file = new File(breaksPath, 'a');
            file.write(line + '\n');
            file.close();
        } catch (e) {
            say(`er-duel-rules: cannot append to ${breaksPath}: ${e.message}`);
        }
    };

    // The local Steam name, through the flat exports of the steam_api64.dll the game loads; asked
    // until Steam first answers, then kept.
    let steamName = null;
    const persona = (() => {
        try {
            const steam = Process.getModuleByName('steam_api64.dll');
            const friends = new NativeFunction(steam.getExportByName('SteamAPI_SteamFriends_v017'), 'pointer', []);
            const name = new NativeFunction(steam.getExportByName('SteamAPI_ISteamFriends_GetPersonaName'), 'pointer', ['pointer']);
            return () => { const f = friends(); return f.isNull() ? null : name(f).readUtf8String(); };
        } catch (e) {
            say(`er-duel-rules: no Steam name (${e.message}); the local player is listed as (you)`);
            return () => null;
        }
    })();
    const localSteamName = () => {
        if (steamName === null) {
            const got = persona();
            if (got) { steamName = got.trim(); say(`steam: the local player is listed as ${JSON.stringify(steamName)}`); }
        }
        return steamName;
    };

    const referee = new Referee();
    const loggedBuilds = new Map();
    const started = Date.now();
    let ticks = 0, alerts = 0, errors = 0;
    function tick() {
        ticks += 1;
        let players;
        try { players = readPlayers(); } catch (e) { errors += 1; return; }
        for (const p of players) {
            const build = `level=${p.level} attributes=[${p.attributes}] rune_arc=${runeArcActive(p)} arm=${p.armStyle} slots=L${p.leftSlot}/R${p.rightSlot} ` +
                `right=[${p.weaponsRight}] left=[${p.weaponsLeft}] armor=[${p.armor}] talismans=[${p.talismans}] physick=[${p.physick}]`;
            if (loggedBuilds.get(p.key) !== build) {
                say(`player: ${playerLabel(p.name, p.isLocal, localSteamName())} ${build}`);
                loggedBuilds.set(p.key, build);
            }
            for (const anim of p.newAnims) {
                const sc = skillClip(anim);
                if (sc !== null) say(`skill: ${p.name} anim=${anim} swordArtsTypeNew=${sc[0]} clip=${String(sc[1]).padStart(6, '0')}`);
            }
        }
        const before = new Map(players.map((p) => [p.key, referee.flasksUsed(p.key)]));
        for (const a of referee.observe(Date.now() - started, players, facts)) {
            alerts += 1;
            say(`${headline(a, localSteamName())}\n    -> ${a.detail}`);
            recordBreak(breakLine(localStamp(), a, localSteamName()));
        }
        for (const p of players) {
            const n = referee.flasksUsed(p.key);
            if (n > before.get(p.key)) say(`flask: ${p.name} blue flask ${n} this round`);
        }
        if (ticks % 600 === 0) {
            say(`status: ticks=${ticks} players=${players.length} remote_pending=${pending} alerts=${alerts} read_errors=${errors}`);
        }
    }

    const ticking = hook('frameTick', { onEnter() { tick(); } });
    hook('speffectApply', {
        onEnter(args) { this.owner = args[2].toString(); this.id = args[1].toInt32(); },
        onLeave(retval) {
            if (retval.toInt32() < 0 || !playerAddresses.has(this.owner)) return;
            if (!applied.has(this.owner)) applied.set(this.owner, []);
            applied.get(this.owner).push(this.id);
        },
    });
    // The checks run inside the game's frame tick, on the game thread. If another mod has already
    // patched it, nothing else here is a frame event, so the referee says so and stays off.
    if (!ticking) {
        say('er-duel-rules: REFUSED -- the frame tick is already patched by another mod, so no rule is checked; send this log to whoever shared the script');
    }
    say(`er-duel-rules: armed on ${BUILD.label}; ${hooks.length} hook(s); log ${logFile === null ? 'unavailable' : logPath}`);

    rpc.exports = { dispose() { hooks.forEach((h) => h.detach()); } };
}
