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
// YOUR OWN RULES: er-duel-rules.json beside eldenring.exe turns any rule off, changes the rune
// level, the blue flask count and the shield cap, and adds bans of your own built from anything
// in the game -- a weapon, a class, an affinity, a grip, armour, talismans, tears, skills, spells,
// items, raw effect/projectile/animation ids, rune level and attributes, in any combination.
// The agent reads it again within two seconds of a change. Write it with the Cheat Engine
// table's Rules window, with scripts/er-duel-rules-bans.py, or from this REPL:
//   findItem('rivers')                                        names to use
//   ban('No 2H Rivers', weapon('Rivers of Blood', { where: 'twoHanded' }))
//   ban('Bleed katanas with Lord of Blood', weapon(null, { class: 'Katana', affinity: 'Blood' }),
//       talisman("Lord of Blood's Exultation"))
//   ban('No Comet Azur', spell('Comet Azur'))     ruleOff('LightRoll')     setRule('runeLevel', 150)
//   rules()     unban('No Comet Azur')     banOn('No 2H Rivers', false)
// weapon() takes where: equipped (any slot), held, right, left, twoHanded, oneHanded,
// powerstanced, skill; and buff: true for a weapon carrying a weapon buff.
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
const CATALOG = {"affinities": [["Standard", 0], ["Heavy", 100], ["Keen", 200], ["Quality", 300], ["Fire", 400], ["Flame Art", 500], ["Lightning", 600], ["Sacred", 700], ["Magic", 800], ["Cold", 900], ["Poison", 1000], ["Blood", 1100], ["Occult", 1200]], "armor": [["Alberich's Bracers", [120200]], ["Alberich's Pointed Hat", [120000]], ["Alberich's Pointed Hat (Altered)", [121000]], ["Alberich's Robe", [120100]], ["Alberich's Robe (Altered)", [121100]], ["Alberich's Trousers", [120300]], ["Albinauric Mask", [1060000]], ["All-Knowing Armor", [590100]], ["All-Knowing Armor (Altered)", [591100]], ["All-Knowing Gauntlets", [590200]], ["All-Knowing Greaves", [590300]], ["All-Knowing Helm", [590000]], ["Ansbach's Attire", [5110100]], ["Ansbach's Attire (Altered)", [5111100]], ["Ansbach's Boots", [5110300]], ["Ansbach's Manchettes", [5110200]], ["Aristocrat Boots", [370300]], ["Aristocrat Coat", [380100]], ["Aristocrat Garb", [370100]], ["Aristocrat Garb (Altered)", [371100]], ["Aristocrat Hat", [380000]], ["Aristocrat Headband", [370000]], ["Armor of Night", [5090100]], ["Armor of Solitude", [5130100]], ["Armor of Solitude (Altered)", [5131100]], ["Arms", [10200]], ["Ascetic's Ankle Guards", [5210300]], ["Ascetic's Loincloth", [5210100]], ["Ascetic's Wrist Guards", [5210200]], ["Ash-of-War Scarab", [1900000]], ["Astrologer Gloves", [630200]], ["Astrologer Hood", [630000]], ["Astrologer Robe", [630100]], ["Astrologer Robe (Altered)", [631100]], ["Astrologer Trousers", [630300]], ["Azur's Glintstone Crown", [581000]], ["Azur's Glintstone Robe", [581100]], ["Azur's Manchettes", [581200]], ["Bandit Boots", [930300]], ["Bandit Garb", [931100]], ["Bandit Manchettes", [930200]], ["Bandit Mask", [1401000]], ["Banished Knight Armor", [200100]], ["Banished Knight Armor (Altered)", [201100]], ["Banished Knight Gauntlets", [200200]], ["Banished Knight Greaves", [200300]], ["Banished Knight Helm", [200000]], ["Banished Knight Helm (Altered)", [201000]], ["Battlemage Legwraps", [1000300]], ["Battlemage Manchettes", [1000200]], ["Battlemage Robe", [1000100]], ["Beast Champion Armor", [720100]], ["Beast Champion Armor (Altered)", [721100]], ["Beast Champion Gauntlets", [720200]], ["Beast Champion Greaves", [720300]], ["Beast Champion Helm", [720000]], ["Black Dumpling", [2020000]], ["Black Hood", [1400000]], ["Black Knife Armor", [180100]], ["Black Knife Armor (Altered)", [181100]], ["Black Knife Gauntlets", [180200]], ["Black Knife Greaves", [180300]], ["Black Knife Hood", [180000]], ["Black Knight Armor", [5150100]], ["Black Knight Gauntlets", [5150200]], ["Black Knight Greaves", [5150300]], ["Black Knight Helm", [5150000]], ["Black Wolf Mask", [170000]], ["Blackflame Monk Armor", [351100]], ["Blackflame Monk Gauntlets", [351200]], ["Blackflame Monk Greaves", [351300]], ["Blackflame Monk Hood", [351000]], ["Blackguard's Iron Mask", [891000]], ["Blaidd's Armor", [170100]], ["Blaidd's Armor (Altered)", [171100]], ["Blaidd's Gauntlets", [170200]], ["Blaidd's Greaves", [170300]], ["Bloodhound Knight Armor", [790100]], ["Bloodhound Knight Armor (Altered)", [791100]], ["Bloodhound Knight Gauntlets", [790200]], ["Bloodhound Knight Greaves", [790300]], ["Bloodhound Knight Helm", [790000]], ["Bloodsoaked Manchettes", [963200]], ["Bloodsoaked Mask", [964000]], ["Bloodsoaked Tabard", [1870100]], ["Blue Cloth Cowl", [670000]], ["Blue Cloth Vest", [670100]], ["Blue Festive Garb", [802100]], ["Blue Festive Hood", [802000]], ["Blue Silver Bracelets", [240200]], ["Blue Silver Mail Armor", [240100]], ["Blue Silver Mail Armor (Altered)", [241100]], ["Blue Silver Mail Hood", [240000]], ["Blue Silver Mail Skirt", [240300]], ["Body", [10100]], ["Braided Arm Wraps", [5070200]], ["Braided Cord Robe", [5070100]], ["Briar Armor", [210100]], ["Briar Armor (Altered)", [211100]], ["Briar Gauntlets", [210200]], ["Briar Greaves", [210300]], ["Briar Helm", [210000]], ["Broken Gold Mask", [5340000]], ["Bull-Goat Armor", [140100]], ["Bull-Goat Gauntlets", [140200]], ["Bull-Goat Greaves", [140300]], ["Bull-Goat Helm", [140000]], ["Carian Knight Armor", [980100]], ["Carian Knight Armor (Altered)", [981100]], ["Carian Knight Gauntlets", [980200]], ["Carian Knight Greaves", [980300]], ["Carian Knight Helm", [980000]], ["Caterpillar Mask", [5070000]], ["Cerulean Tear Scarab", [1920000]], ["Chain Armor", [1100100]], ["Chain Coif", [1100000]], ["Chain Leggings", [1100300]], ["Chain-Draped Tabard", [1830100]], ["Champion Bracers", [730200]], ["Champion Gaiters", [730300]], ["Champion Headband", [730000]], ["Champion Pauldron", [730100]], ["Circlet of Light", [5272000]], ["Cleanrot Armor", [340100]], ["Cleanrot Armor (Altered)", [341100]], ["Cleanrot Gauntlets", [340200]], ["Cleanrot Greaves", [340300]], ["Cleanrot Helm", [340000]], ["Cleanrot Helm (Altered)", [341000]], ["Cloth Garb", [160100]], ["Cloth Trousers", [160300]], ["Common Soldier Cloth Armor", [5240100]], ["Common Soldier Gauntlets", [5240200]], ["Common Soldier Greaves", [5240300]], ["Common Soldier Helm", [5240000]], ["Commoner's Garb", [810100]], ["Commoner's Garb (Altered)", [811100]], ["Commoner's Headband", [810000]], ["Commoner's Headband (Altered)", [811000]], ["Commoner's Shoes", [810300]], ["Commoner's Simple Garb", [812000]], ["Commoner's Simple Garb (Altered)", [812100]], ["Confessor Armor", [880100]], ["Confessor Armor (Altered)", [881100]], ["Confessor Boots", [880300]], ["Confessor Gloves", [880200]], ["Confessor Hood", [880000]], ["Confessor Hood (Altered)", [881000]], ["Consort's Mask", [960000]], ["Consort's Robe", [960100]], ["Consort's Trousers", [960300]], ["Corhyn's Robe", [620100]], ["Crimson Hood", [740000]], ["Crimson Tear Scarab", [1910000]], ["Crucible Axe Armor", [570100]], ["Crucible Axe Armor (Altered)", [572100]], ["Crucible Axe Helm", [570000]], ["Crucible Gauntlets", [570200]], ["Crucible Greaves", [570300]], ["Crucible Hammer-Helm", [5310000]], ["Crucible Tree Armor", [571100]], ["Crucible Tree Armor (Altered)", [573100]], ["Crucible Tree Helm", [571000]], ["Cuckoo Knight Armor", [1780100]], ["Cuckoo Knight Armor (Altered)", [1781100]], ["Cuckoo Knight Gauntlets", [1780200]], ["Cuckoo Knight Greaves", [1780300]], ["Cuckoo Knight Helm", [1780000]], ["Cuckoo Surcoat", [1710100]], ["Curseblade Mask", [5210000]], ["Dancer's Bracer", [5080200]], ["Dancer's Dress", [5080100]], ["Dancer's Dress (Altered)", [5081100]], ["Dancer's Hood", [5080000]], ["Dancer's Trousers", [5080300]], ["Dane's Hat", [3000000]], ["Death Knight Armor", [5200100]], ["Death Knight Gauntlets", [5200200]], ["Death Knight Greaves", [5200300]], ["Death Knight Helm", [5200000]], ["Death Mask Helm", [5182000]], ["Deathbed Dress", [1930100]], ["Depraved Perfumer Gloves", [540200]], ["Depraved Perfumer Headscarf", [540000]], ["Depraved Perfumer Robe", [540100]], ["Depraved Perfumer Robe (Altered)", [541100]], ["Depraved Perfumer Trousers", [540300]], ["Diallos's Mask", [651000]], ["Dirty Chainmail", [1060100]], ["Divine Beast Head", [5290000]], ["Divine Beast Helm", [5252000]], ["Divine Beast Warrior Armor", [5252100]], ["Divine Bird Helm", [5253000]], ["Divine Bird Warrior Armor", [5253100]], ["Divine Bird Warrior Gauntlets", [5253200]], ["Divine Bird Warrior Greaves", [5253300]], ["Drake Knight Armor", [60100]], ["Drake Knight Armor (Altered)", [61100]], ["Drake Knight Gauntlets", [60200]], ["Drake Knight Greaves", [60300]], ["Drake Knight Helm", [60000]], ["Drake Knight Helm (Altered)", [61000]], ["Dryleaf Arm Wraps", [3000200]], ["Dryleaf Cuissardes", [3000300]], ["Dryleaf Robe", [3000100]], ["Dryleaf Robe (Altered)", [3001100]], ["Duelist Greaves", [310300]], ["Duelist Helm", [310000]], ["Eccentric's Armor", [940100]], ["Eccentric's Breeches", [940300]], ["Eccentric's Hood", [940000]], ["Eccentric's Hood (Altered)", [941000]], ["Eccentric's Manchettes", [940200]], ["Elden Lord Armor", [460100]], ["Elden Lord Armor (Altered)", [461100]], ["Elden Lord Bracers", [460200]], ["Elden Lord Crown", [460000]], ["Elden Lord Greaves", [460300]], ["Envoy Crown", [820000]], ["Erdtree Surcoat", [1720100]], ["Errant Sorcerer Boots", [990300]], ["Errant Sorcerer Manchettes", [990200]], ["Errant Sorcerer Robe", [990100]], ["Errant Sorcerer Robe (Altered)", [991100]], ["Exile Armor", [190100]], ["Exile Gauntlets", [190200]], ["Exile Greaves", [190300]], ["Exile Hood", [190000]], ["Eye Surcoat", [1101100]], ["Fang Helm", [5021000]], ["Fell Omen Cloak", [1050100]], ["Festive Garb", [800100]], ["Festive Garb (Altered)", [801100]], ["Festive Hood", [800000]], ["Festive Hood (Altered)", [801000]], ["Fia's Hood", [1940000]], ["Fia's Robe", [1940100]], ["Fia's Robe (Altered)", [1941100]], ["Finger Maiden Fillet", [902000]], ["Finger Maiden Robe", [902100]], ["Finger Maiden Robe (Altered)", [903100]], ["Finger Maiden Shoes", [902300]], ["Finger Robe", [5062100]], ["Fingerprint Armor", [950100]], ["Fingerprint Armor (Altered)", [951100]], ["Fingerprint Gauntlets", [950200]], ["Fingerprint Greaves", [950300]], ["Fingerprint Helm", [950000]], ["Fire Knight Armor", [5180100]], ["Fire Knight Armor (Altered)", [5181100]], ["Fire Knight Gauntlets", [5180200]], ["Fire Knight Greaves", [5180300]], ["Fire Knight Helm", [5180000]], ["Fire Monk Armor", [350100]], ["Fire Monk Gauntlets", [350200]], ["Fire Monk Greaves", [350300]], ["Fire Monk Hood", [350000]], ["Fire Prelate Armor", [360100]], ["Fire Prelate Armor (Altered)", [361100]], ["Fire Prelate Gauntlets", [360200]], ["Fire Prelate Greaves", [360300]], ["Fire Prelate Helm", [360000]], ["Foot Soldier Cap", [1830000]], ["Foot Soldier Gauntlets", [1830200]], ["Foot Soldier Greaves", [1830300]], ["Foot Soldier Helm", [1860000]], ["Foot Soldier Helmet", [1840000]], ["Foot Soldier Tabard", [1840100]], ["Freyja's Armor", [5120100]], ["Freyja's Armor (Altered)", [5121100]], ["Freyja's Gauntlets", [5120200]], ["Freyja's Greaves", [5120300]], ["Freyja's Helm", [5120000]], ["Fur Leggings", [300300]], ["Fur Raiment", [300100]], ["Gaius's Armor", [3010100]], ["Gaius's Gauntlets", [3010200]], ["Gaius's Greaves", [3010300]], ["Gaius's Helm", [3010000]], ["Gauntlets", [1100200]], ["Gauntlets of Night", [5090200]], ["Gauntlets of Solitude", [5130200]], ["Gelmir Knight Armor", [1760100]], ["Gelmir Knight Armor (Altered)", [1761100]], ["Gelmir Knight Gauntlets", [1760200]], ["Gelmir Knight Greaves", [1760300]], ["Gelmir Knight Helm", [1760000]], ["Gilded Foot Soldier Cap", [1850000]], ["Glintstone Scarab", [1902000]], ["Gloried Attire", [5190100]], ["Godrick Knight Armor", [1770100]], ["Godrick Knight Armor (Altered)", [1771100]], ["Godrick Knight Gauntlets", [1770200]], ["Godrick Knight Greaves", [1770300]], ["Godrick Knight Helm", [1770000]], ["Godrick Soldier Gauntlets", [1700200]], ["Godrick Soldier Greaves", [1700300]], ["Godrick Soldier Helm", [1700000]], ["Godskin Apostle Bracelets", [520200]], ["Godskin Apostle Hood", [520000]], ["Godskin Apostle Robe", [520100]], ["Godskin Apostle Trousers", [520300]], ["Godskin Noble Bracelets", [530200]], ["Godskin Noble Hood", [530000]], ["Godskin Noble Robe", [530100]], ["Godskin Noble Trousers", [530300]], ["Gold Bracelets", [1040200]], ["Gold Tattoo (Arm)", [5340200]], ["Gold Tattoo (Chest)", [5340100]], ["Gold Tattoo (Leg)", [5340300]], ["Gold Waistwrap", [1040300]], ["Goldmask's Rags", [1040100]], ["Gravebird Anklets", [5230300]], ["Gravebird Armor", [5231100]], ["Gravebird Bracelets", [5230200]], ["Gravebird Helm", [5230000]], ["Gravebird's Blackquill Armor", [5230100]], ["Gravekeeper Cloak", [310100]], ["Gravekeeper Cloak (Altered)", [311100]], ["Great Horned Headband", [300000]], ["Greathelm", [1101000]], ["Greathood", [1600000]], ["Greatjar", [5320000]], ["Greaves of Night", [5090300]], ["Greaves of Solitude", [5130300]], ["Guardian Bracers", [330200]], ["Guardian Garb", [331100]], ["Guardian Garb (Full Bloom)", [330100]], ["Guardian Greaves", [330300]], ["Guardian Mask", [330000]], ["Guilty Hood", [160000]], ["Haima Glintstone Crown", [1000000]], ["Haligtree Crest Surcoat", [1750100]], ["Haligtree Gauntlets", [1750200]], ["Haligtree Greaves", [1750300]], ["Haligtree Helm", [1750000]], ["Haligtree Knight Armor", [1820100]], ["Haligtree Knight Armor (Altered)", [1821100]], ["Haligtree Knight Gauntlets", [1820200]], ["Haligtree Knight Greaves", [1820300]], ["Haligtree Knight Helm", [1820000]], ["Head", [10000]], ["Helm of Night", [5090000]], ["Helm of Solitude", [5130000]], ["Hierodas Glintstone Crown", [990000]], ["High Page Clothes", [1990100]], ["High Page Clothes (Altered)", [1991100]], ["High Page Hood", [1990000]], ["High Priest Gloves", [5060200]], ["High Priest Hat", [5060000]], ["High Priest Robe", [5060100]], ["High Priest Undergarments", [5060300]], ["Highland Attire", [5191100]], ["Highwayman Cloth Armor", [1980100]], ["Highwayman Gauntlets", [1980200]], ["Highwayman Hood", [1980000]], ["Horned Warrior Armor", [5250100]], ["Horned Warrior Gauntlets", [5250200]], ["Horned Warrior Greaves", [5250300]], ["Horned Warrior Helm", [5250000]], ["Hoslow's Armor", [650100]], ["Hoslow's Armor (Altered)", [652100]], ["Hoslow's Gauntlets", [650200]], ["Hoslow's Greaves", [650300]], ["Hoslow's Helm", [650000]], ["Igon's Armor", [5100100]], ["Igon's Armor (Altered)", [5101100]], ["Igon's Gauntlets", [5100200]], ["Igon's Helm", [5100000]], ["Igon's Helm (Altered)", [5101000]], ["Igon's Loincloth", [5100300]], ["Iji's Mirrorhelm", [1301000]], ["Imp Head (Cat)", [1080000]], ["Imp Head (Corpse)", [1083000]], ["Imp Head (Elder)", [1085000]], ["Imp Head (Fanged)", [1081000]], ["Imp Head (Lion)", [5330000]], ["Imp Head (Long-Tongued)", [1082000]], ["Imp Head (Wolf)", [1084000]], ["Incantation Scarab", [1901000]], ["Iron Gauntlets", [40200]], ["Iron Helmet", [40000]], ["Iron Kasa", [150000]], ["Iron Rivet Armor", [5020100]], ["Iron Rivet Gauntlets", [5020200]], ["Iron Rivet Greaves", [5020300]], ["Ivory-Draped Tabard", [1880100]], ["Jar", [1120000]], ["Juvenile Scholar Cap", [1030000]], ["Juvenile Scholar Robe", [1030100]], ["Kaiden Armor", [50100]], ["Kaiden Gauntlets", [50200]], ["Kaiden Helm", [50000]], ["Kaiden Trousers", [50300]], ["Karolos Glintstone Crown", [833000]], ["Knight Armor", [1500100]], ["Knight Gauntlets", [1500200]], ["Knight Greaves", [1500300]], ["Knight Helm", [1500000]], ["Land of Reeds Armor", [870100]], ["Land of Reeds Armor (Altered)", [871100]], ["Land of Reeds Gauntlets", [870200]], ["Land of Reeds Greaves", [870300]], ["Land of Reeds Helm", [870000]], ["Lazuli Glintstone Crown", [832000]], ["Lazuli Robe", [2030000]], ["Leather Arm Wraps", [5190200]], ["Leather Armor", [1400100]], ["Leather Boots", [1400300]], ["Leather Crown", [5191000]], ["Leather Gloves", [1400200]], ["Leather Headband", [5190000]], ["Leather Leg Wraps", [5190300]], ["Leather Trousers", [40300]], ["Leather-Draped Tabard", [1850100]], ["Leda's Armor", [5000100]], ["Legs", [10300]], ["Leontiel's Armor", [5360100]], ["Leontiel's Boots", [5360300]], ["Leontiel's Hat", [5360000]], ["Leontiel's Hat (Altered)", [5361000]], ["Leontiel's Leather Gloves", [5360200]], ["Leyndell Knight Armor", [1790100]], ["Leyndell Knight Armor (Altered)", [1791100]], ["Leyndell Knight Gauntlets", [1790200]], ["Leyndell Knight Greaves", [1790300]], ["Leyndell Knight Helm", [1790000]], ["Leyndell Soldier Gauntlets", [1720200]], ["Leyndell Soldier Greaves", [1720300]], ["Leyndell Soldier Helm", [1720000]], ["Lionel's Armor", [640100]], ["Lionel's Armor (Altered)", [641100]], ["Lionel's Gauntlets", [640200]], ["Lionel's Greaves", [640300]], ["Lionel's Helm", [640000]], ["Lord of Blood's Robe", [480100]], ["Lord of Blood's Robe (Altered)", [481100]], ["Lusat's Glintstone Crown", [580000]], ["Lusat's Manchettes", [580200]], ["Lusat's Robe", [580100]], ["Malenia's Armor", [770100]], ["Malenia's Armor (Altered)", [771100]], ["Malenia's Gauntlet", [770200]], ["Malenia's Greaves", [770300]], ["Malenia's Winged Helm", [770000]], ["Malformed Dragon Armor", [260100]], ["Malformed Dragon Gauntlets", [260200]], ["Malformed Dragon Greaves", [260300]], ["Malformed Dragon Helm", [260000]], ["Maliketh's Armor", [760100]], ["Maliketh's Armor (Altered)", [761100]], ["Maliketh's Gauntlets", [760200]], ["Maliketh's Greaves", [760300]], ["Maliketh's Helm", [760000]], ["Marais Mask", [963000]], ["Marais Robe", [963100]], ["Marionette Soldier Armor", [840100]], ["Marionette Soldier Birdhelm", [850000]], ["Marionette Soldier Helm", [840000]], ["Mask of Confidence", [911000]], ["Mausoleum Gauntlets", [1740200]], ["Mausoleum Greaves", [1740300]], ["Mausoleum Knight Armor", [1810100]], ["Mausoleum Knight Armor (Altered)", [1811100]], ["Mausoleum Knight Gauntlets", [1810200]], ["Mausoleum Knight Greaves", [1810300]], ["Mausoleum Surcoat", [1740100]], ["Messmer Soldier Armor", [5140100]], ["Messmer Soldier Armor (Altered)", [5141100]], ["Messmer Soldier Gauntlets", [5140200]], ["Messmer Soldier Greaves", [5140300]], ["Messmer Soldier Helm", [5140000]], ["Messmer's Armor", [5220100]], ["Messmer's Gauntlets", [5220200]], ["Messmer's Greaves", [5220300]], ["Messmer's Helm", [5220000]], ["Messmer's Helm (Altered)", [5221000]], ["Mushroom Arms", [1130200]], ["Mushroom Body", [1130100]], ["Mushroom Crown", [2010000]], ["Mushroom Head", [1130000]], ["Mushroom Legs", [1130300]], ["Navy Hood", [741000]], ["Night Maiden Armor", [293100]], ["Night Maiden Twin Crown", [293000]], ["Night's Cavalry Armor", [230100]], ["Night's Cavalry Armor (Altered)", [231100]], ["Night's Cavalry Gauntlets", [230200]], ["Night's Cavalry Greaves", [230300]], ["Night's Cavalry Helm", [230000]], ["Night's Cavalry Helm (Altered)", [231000]], ["Noble's Gloves", [740200]], ["Noble's Traveling Garb", [740100]], ["Noble's Trousers", [740300]], ["Nomadic Merchant's Chapeau", [250000]], ["Nomadic Merchant's Finery", [250100]], ["Nomadic Merchant's Finery (Altered)", [251100]], ["Nomadic Merchant's Trousers", [250300]], ["Nox Bracelets", [290200]], ["Nox Greaves", [290300]], ["Nox Mirrorhelm", [1300000]], ["Nox Monk Armor", [290100]], ["Nox Monk Armor (Altered)", [291100]], ["Nox Monk Hood", [290000]], ["Nox Monk Hood (Altered)", [291000]], ["Nox Swordstress Armor", [292100]], ["Nox Swordstress Armor (Altered)", [294100]], ["Nox Swordstress Crown", [292000]], ["Nox Swordstress Crown (Altered)", [294000]], ["Oathseeker Knight Armor", [5002100]], ["Oathseeker Knight Gauntlets", [5000200]], ["Oathseeker Knight Greaves", [5000300]], ["Oathseeker Knight Helm", [5000000]], ["Octopus Head", [1110000]], ["Official's Attire", [964100]], ["Okina Mask", [872000]], ["Old Aristocrat Cowl", [390000]], ["Old Aristocrat Gown", [390100]], ["Old Aristocrat Shoes", [390300]], ["Old Sorcerer's Legwraps", [580300]], ["Olivinus Glintstone Crown", [831000]], ["Omen Armor", [970100]], ["Omen Gauntlets", [970200]], ["Omen Greaves", [970300]], ["Omen Helm", [970000]], ["Omenkiller Boots", [1890300]], ["Omenkiller Long Gloves", [1890200]], ["Omenkiller Robe", [1890100]], ["Omensmirk Mask", [1890000]], ["Page Garb", [220100]], ["Page Garb (Altered)", [221100]], ["Page Hood", [220000]], ["Page Trousers", [220300]], ["Pelt of Ralva", [5020000]], ["Perfumer Gloves", [90200]], ["Perfumer Hood", [90000]], ["Perfumer Robe", [90100]], ["Perfumer Robe (Altered)", [91100]], ["Perfumer Sarong", [90300]], ["Perfumer's Traveling Garb", [100100]], ["Perfumer's Traveling Garb (Altered)", [101100]], ["Preceptor's Big Hat", [910000]], ["Preceptor's Gloves", [910200]], ["Preceptor's Long Gown", [910100]], ["Preceptor's Long Gown (Altered)", [911100]], ["Preceptor's Trousers", [910300]], ["Prisoner Clothing", [890100]], ["Prisoner Iron Mask", [890000]], ["Prisoner Trousers", [890300]], ["Prophet Blindfold", [620000]], ["Prophet Robe", [622100]], ["Prophet Robe (Altered)", [621100]], ["Prophet Trousers", [620300]], ["Pumpkin Helm", [440000]], ["Queen's Bracelets", [510200]], ["Queen's Crescent Crown", [510000]], ["Queen's Leggings", [510300]], ["Queen's Robe", [510100]], ["Radahn Soldier Gauntlets", [1730200]], ["Radahn Soldier Greaves", [1730300]], ["Radahn Soldier Helm", [1730000]], ["Radahn's Gauntlets", [470200]], ["Radahn's Greaves", [470300]], ["Radahn's Lion Armor", [470100]], ["Radahn's Lion Armor (Altered)", [471100]], ["Radahn's Redmane Helm", [470000]], ["Radiant Gold Mask", [1040000]], ["Ragged Armor", [610100]], ["Ragged Armor (Altered)", [611100]], ["Ragged Gloves", [610200]], ["Ragged Hat", [610000]], ["Ragged Hat (Altered)", [611000]], ["Ragged Loincloth", [610300]], ["Raging Wolf Armor", [860100]], ["Raging Wolf Armor (Altered)", [861100]], ["Raging Wolf Gauntlets", [860200]], ["Raging Wolf Greaves", [860300]], ["Raging Wolf Helm", [860000]], ["Rakshasa Armor", [5160100]], ["Rakshasa Gauntlets", [5160200]], ["Rakshasa Greaves", [5160300]], ["Rakshasa Helm", [5160000]], ["Raptor's Black Feathers", [930100]], ["Raya Lucarian Gauntlets", [1710200]], ["Raya Lucarian Greaves", [1710300]], ["Raya Lucarian Helm", [1710000]], ["Raya Lucarian Robe", [830100]], ["Redmane Knight Armor", [1800100]], ["Redmane Knight Armor (Altered)", [1801100]], ["Redmane Knight Gauntlets", [1800200]], ["Redmane Knight Greaves", [1800300]], ["Redmane Knight Helm", [1800000]], ["Redmane Surcoat", [1730100]], ["Rellana's Armor", [5260100]], ["Rellana's Gloves", [5260200]], ["Rellana's Greaves", [5260300]], ["Rellana's Helm", [5260000]], ["Ronin's Armor", [150100]], ["Ronin's Armor (Altered)", [151100]], ["Ronin's Gauntlets", [150200]], ["Ronin's Greaves", [150300]], ["Rotten Duelist Greaves", [2000300]], ["Rotten Duelist Helm", [2000000]], ["Rotten Gravekeeper Cloak", [2000100]], ["Rotten Gravekeeper Cloak (Altered)", [2001100]], ["Royal Knight Armor", [280100]], ["Royal Knight Armor (Altered)", [281100]], ["Royal Knight Gauntlets", [280200]], ["Royal Knight Greaves", [280300]], ["Royal Knight Helm", [280000]], ["Royal Remains Armor", [690100]], ["Royal Remains Gauntlets", [690200]], ["Royal Remains Greaves", [690300]], ["Royal Remains Helm", [690000]], ["Ruler's Mask", [961000]], ["Ruler's Robe", [961100]], ["Sacred Crown Helm", [1880000]], ["Sage Hood", [430000]], ["Sage Robe", [430100]], ["Sage Trousers", [430300]], ["Salza's Hood", [5184000]], ["Sanguine Noble Hood", [320000]], ["Sanguine Noble Robe", [320100]], ["Sanguine Noble Waistcloth", [320300]], ["Scale Armor", [40100]], ["Scaled Armor", [80100]], ["Scaled Armor (Altered)", [81100]], ["Scaled Gauntlets", [80200]], ["Scaled Greaves", [80300]], ["Scaled Helm", [80000]], ["Scarlet Tabard", [1860100]], ["Shadow Militiaman Armor", [5280100]], ["Shadow Militiaman Gauntlets", [5280200]], ["Shadow Militiaman Greaves", [5280300]], ["Shadow Militiaman Helm", [5280000]], ["Shaman Furs", [301100]], ["Shaman Leggings", [301300]], ["Shining Horned Headband", [301000]], ["Silver Grooved Armor", [5350100]], ["Silver Grooved Armor (Altered)", [5351100]], ["Silver Grooved Gauntlets", [5350200]], ["Silver Grooved Greaves", [5350300]], ["Silver Grooved Helm", [5350000]], ["Silver Tear Mask", [1090000]], ["Skeletal Mask", [930000]], ["Snow Witch Hat", [1010000]], ["Snow Witch Robe", [1010100]], ["Snow Witch Robe (Altered)", [1011100]], ["Snow Witch Skirt", [1010300]], ["Soiled Loincloth", [5070300]], ["Sorcerer Leggings", [830300]], ["Sorcerer Manchettes", [830200]], ["Spellblade's Gloves", [130200]], ["Spellblade's Pointed Hat", [130000]], ["Spellblade's Traveling Attire", [130100]], ["Spellblade's Traveling Attire (Altered)", [131100]], ["Spellblade's Trousers", [130300]], ["St. Trina's Blossom", [5300000]], ["Steel Armor", [5370100]], ["Steel Gauntlets", [5370200]], ["Steel Greaves", [5370300]], ["Steel Helm", [5370000]], ["Tarnished Wrap", [160200]], ["Thiollier's Garb", [5030100]], ["Thiollier's Garb (Altered)", [5031100]], ["Thiollier's Gloves", [5030200]], ["Thiollier's Mask", [5030000]], ["Thiollier's Trousers", [5030300]], ["Traveler's Boots", [1020300]], ["Traveler's Clothes", [1020100]], ["Traveler's Gloves", [100200]], ["Traveler's Hat", [100000]], ["Traveler's Manchettes", [1020200]], ["Traveler's Slops", [100300]], ["Traveling Maiden Boots", [900300]], ["Traveling Maiden Gloves", [900200]], ["Traveling Maiden Hood", [900000]], ["Traveling Maiden Robe", [900100]], ["Traveling Maiden Robe (Altered)", [901100]], ["Tree Sentinel Armor", [270100]], ["Tree Sentinel Armor (Altered)", [271100]], ["Tree Sentinel Gauntlets", [270200]], ["Tree Sentinel Greaves", [270300]], ["Tree Sentinel Helm", [270000]], ["Tree Surcoat", [1102100]], ["Tree-and-Beast Surcoat", [1700100]], ["Twinned Armor", [600100]], ["Twinned Armor (Altered)", [601100]], ["Twinned Gauntlets", [600200]], ["Twinned Greaves", [600300]], ["Twinned Greaves (Altered)", [601300]], ["Twinned Helm", [600000]], ["Twinsage Glintstone Crown", [830000]], ["Upper-Class Robe", [962100]], ["Vagabond Knight Armor", [660100]], ["Vagabond Knight Armor (Altered)", [661100]], ["Vagabond Knight Gauntlets", [660200]], ["Vagabond Knight Greaves", [660300]], ["Vagabond Knight Helm", [660000]], ["Verdigris Armor", [5010100]], ["Verdigris Gauntlets", [5010200]], ["Verdigris Greaves", [5010300]], ["Verdigris Helm", [5010000]], ["Veteran's Armor", [780100]], ["Veteran's Armor (Altered)", [781100]], ["Veteran's Gauntlets", [780200]], ["Veteran's Greaves", [780300]], ["Veteran's Helm", [780000]], ["Vulgar Militia Armor", [420100]], ["Vulgar Militia Gauntlets", [420200]], ["Vulgar Militia Greaves", [420300]], ["Vulgar Militia Helm", [420000]], ["War Surgeon Gloves", [680200]], ["War Surgeon Gown", [680100]], ["War Surgeon Gown (Altered)", [681100]], ["War Surgeon Trousers", [680300]], ["Warrior Gauntlets", [670200]], ["Warrior Greaves", [670300]], ["White Mask", [680000]], ["White Reed Armor", [872100]], ["White Reed Gauntlets", [872200]], ["White Reed Greaves", [872300]], ["Winged Serpent Helm", [5183000]], ["Wise Man's Mask", [5110000]], ["Witch's Glintstone Crown", [834000]], ["Young Lion's Armor", [5270100]], ["Young Lion's Armor (Altered)", [5271100]], ["Young Lion's Gauntlets", [5270200]], ["Young Lion's Greaves", [5270300]], ["Young Lion's Helm", [5270000]], ["Zamor Armor", [1070100]], ["Zamor Bracelets", [1070200]], ["Zamor Legwraps", [1070300]], ["Zamor Mask", [1070000]]], "classes": [["Dagger", 1], ["Straight Sword", 3], ["Greatsword", 5], ["Colossal Sword", 7], ["Curved Sword", 9], ["Curved Greatsword", 11], ["Katana", 13], ["Twinblade", 14], ["Thrusting Sword", 15], ["Heavy Thrusting Sword", 16], ["Axe", 17], ["Greataxe", 19], ["Hammer", 21], ["Great Hammer", 23], ["Flail", 24], ["Spear", 25], ["Great Spear", 28], ["Halberd", 29], ["Reaper", 31], ["Unarmed", 33], ["Fist", 35], ["Claw", 37], ["Whip", 39], ["Colossal Weapon", 41], ["Light Bow", 50], ["Bow", 51], ["Greatbow", 53], ["Crossbow", 55], ["Ballista", 56], ["Glintstone Staff", 57], ["Sacred Seal", 61], ["Small Shield", 65], ["Medium Shield", 67], ["Greatshield", 69], ["Arrow", 81], ["Greatarrow", 83], ["Bolt", 85], ["Ballista Bolt", 86], ["Torch", 87], ["Hand-to-Hand Art", 88], ["Perfume Bottle", 89], ["Thrusting Shield", 90], ["Throwing Blade", 91], ["Backhand Blade", 92], ["Light Greatsword", 93], ["Great Katana", 94], ["Beast Claw", 95]], "items": [["Academy Magic Pot", [10066100, 10066101], []], ["Acid Spraymist", [10361000, 10361001], []], ["Albinauric Ashes", [], [211000, 211001, 211002, 211003, 211004, 211005, 211006, 211007, 211008, 211009, 211010]], ["Albinauric Pot", [10061000, 10061001], []], ["Alluring Pot", [10039000, 10039001, 10039002], []], ["Ancestral Follower Ashes", [], [209000, 209001, 209002, 209003, 209004, 209005, 209006, 209007, 209008, 209009, 209010]], ["Ancestral Infant's Head", [10300000, 10300001, 10300002], []], ["Ancient Dragon Florissax", [], [20215000, 20215001, 20215002, 20215003, 20215004, 20215005, 20215006, 20215007, 20215008, 20215009, 20215010]], ["Ancient Dragon Knight Kristoff", [], [256000, 256001, 256002, 256003, 256004, 256005, 256006, 256007, 256008, 256009, 256010]], ["Ancient Dragon's Blessing", [], [20502100]], ["Ancient Dragonbolt Pot", [10032100, 10032101, 10032110, 10032111, 10032112, 10032113, 10032114, 10032115], []], ["Archer Ashes", [], [249000, 249001, 249002, 249003, 249004, 249005, 249006, 249007, 249008, 249009, 249010]], ["Avionette Soldier Ashes", [], [226000, 226001, 226002, 226003, 226004, 226005, 226006, 226007, 226008, 226009, 226010]], ["Azula Beastman Ashes", [], [230000, 230001, 230002, 230003, 230004, 230005, 230006, 230007, 230008, 230009, 230010]], ["Baldachin's Blessing", [], [503355]], ["Banished Knight Engvall", [], [202000, 202001, 202002, 202003, 202004, 202005, 202006, 202007, 202008, 202009, 202010]], ["Banished Knight Oleg", [], [201000, 201001, 201002, 201003, 201004, 201005, 201006, 201007, 201008, 201009, 201010]], ["Battlemage Hugues", [], [221000, 221001, 221002, 221003, 221004, 221005, 221006, 221007, 221008, 221009, 221010]], ["Beastlure Pot", [10039100, 10039101], []], ["Bewitching Branch", [10335000, 10335001], []], ["Bigmouth Imp Ashes", [], [20210000, 20210001, 20210002, 20210003, 20210004, 20210005, 20210006, 20210007, 20210008, 20210009, 20210010]], ["Black Knife Tiche", [], [200000, 200001, 200002, 200003, 200004, 200005, 200006, 200007, 200008, 200009, 200010]], ["Black Knight Captain Huw", [], [20209000, 20209001, 20209002, 20209003, 20209004, 20209005, 20209006, 20209007, 20209008, 20209009, 20209010]], ["Black Knight Commander Andreas", [], [20208000, 20208001, 20208002, 20208003, 20208004, 20208005, 20208006, 20208007, 20208008, 20208009, 20208010]], ["Blackflame Monk Amon", [], [228000, 228001, 228002, 228003, 228004, 228005, 228006, 228007, 228008, 228009, 228010]], ["Blasphemous Claw", [10208000], []], ["Blessing of Marika", [], [20500900]], ["Blood Grease", [], [3190, 3194]], ["Bloodboil Aromatic", [10355000], []], ["Bloodfiend Hexer's Ashes", [], [20201000, 20201001, 20201002, 20201003, 20201004, 20201005, 20201006, 20201007, 20201008, 20201009, 20201010]], ["Bloodhound Knight Floh", [], [239000, 239001, 239002, 239003, 239004, 239005, 239006, 239007, 239008, 239009, 239010]], ["Blue Cipher Ring", [], [532]], ["Boiled Crab", [], [500820]], ["Boiled Prawn", [], [500830]], ["Bondstone", [210300000], []], ["Bone Dart", [10171000], []], ["Broken Rune", [], [20502951]], ["Call of Tibia", [210315000, 210315001, 210315002, 210315003, 210315004, 210315005, 210315010, 210315011, 210315012, 210315013, 210315014, 210315015], []], ["Charming Branch", [210335000, 210335001], []], ["Clarifying Boluses", [], [3065]], ["Clarifying Cured Meat", [], [3962]], ["Clarifying White Cured Meat", [], [501330]], ["Clayman Ashes", [], [222000, 222001, 222002, 222003, 222004, 222005, 222006, 222007, 222008, 222009, 222010]], ["Cleanrot Knight Finlay", [], [223000, 223001, 223002, 223003, 223004, 223005, 223006, 223007, 223008, 223009, 223010]], ["Crystal Dart", [10174000], []], ["Crystalian Ashes", [], [208000, 208001, 208002, 208003, 208004, 208005, 208006, 208007, 208008, 208009, 208010]], ["Cuckoo Glintstone", [10303000, 10303001, 10303002], []], ["Curseblade Meera", [], [20200000, 20200001, 20200002, 20200003, 20200004, 20200005, 20200006, 20200007, 20200008, 20200009, 20200010]], ["Cursed-Blood Pot", [10063000, 10063001, 10063002, 10063003], []], ["Dappled Cured Meat", [], [3963]], ["Dappled White Cured Meat", [], [501340]], ["Demi-Human Ashes", [], [234000, 234001, 234002, 234003, 234004, 234005, 234006, 234007, 234008, 234009, 234010]], ["Demi-Human Swordsman Yosh", [], [20206000, 20206001, 20206002, 20206003, 20206004, 20206005, 20206006, 20206007, 20206008, 20206009, 20206010]], ["Depraved Perfumer Carmaan", [], [216000, 216001, 216002, 216003, 216004, 216005, 216006, 216007, 216008, 216009, 216010]], ["Divine Bird Warrior Ornis", [], [20213000, 20213001, 20213002, 20213003, 20213004, 20213005, 20213006, 20213007, 20213008, 20213009, 20213010]], ["Dolores the Sleeping Arrow Puppet", [], [262000, 262001, 262002, 262003, 262004, 262005, 262006, 262007, 262008, 262009, 262010]], ["Dragon Communion Flesh", [], [20501100]], ["Dragon Communion Grease", [], [20501480, 20501481]], ["Dragon Communion Harpoon", [210171000], []], ["Dragonbolt Grease", [], [20501410, 20501412]], ["Dragonscale Flesh", [], [20501105]], ["Dragonwound Grease", [], [3260, 3262]], ["Drawstring Blood Grease", [], [3192, 3196]], ["Drawstring Dragonbolt Grease", [], [20501411, 20501413]], ["Drawstring Eternal Sleep Grease", [], [102312, 102316]], ["Drawstring Fire Grease", [], [3161, 3163]], ["Drawstring Golden Grease", [], [20501431, 20501433]], ["Drawstring Holy Grease", [], [3186, 3188]], ["Drawstring Lightning Grease", [], [3166, 3168]], ["Drawstring Magic Grease", [], [3171, 3173]], ["Drawstring Messmerfire Grease", [], [20501401, 20501403]], ["Drawstring Poison Grease", [], [3177, 3181]], ["Drawstring Rot Grease", [], [3312, 3316]], ["Drawstring Royal Magic Grease", [], [20501421, 20501423]], ["Drawstring Soporific Grease", [], [3152, 3156]], ["Duelist's Furled Finger", [], [10]], ["Dung Eater Puppet", [], [260000, 260001, 260002, 260003, 260004, 260005, 260006, 260007, 260008, 260009, 260010]], ["Eternal Sleep Grease", [], [102310, 102314]], ["Eternal Sleep Pot", [210074000, 210074001], []], ["Exalted Flesh", [], [3950]], ["Explosive Stone", [10183000, 10183001], []], ["Explosive Stone Clump", [10183100, 10183101], []], ["Fan Daggers", [10175000], []], ["Fanged Imp Ashes", [], [203000, 203001, 203002, 203003, 203004, 203005, 203006, 203007, 203008, 203009, 203010]], ["Festive Grease", [], [20501620, 20501622]], ["Fetid Pot", [10033000, 10033001], []], ["Finger Maiden Therolina Puppet", [], [261000, 261001, 261002, 261003, 261004, 261005, 261006, 261007, 261008, 261009, 261010]], ["Finger Severer", [], [7]], ["Fingercreeper Ashes", [], [20217000, 20217001, 20217002, 20217003, 20217004, 20217005, 20217006, 20217007, 20217008, 20217009, 20217010]], ["Fingerprint Nostrum", [], [20501110]], ["Fire Coil", [210318000, 210318010, 210318013, 210318014, 210318016], []], ["Fire Grease", [], [3160, 3162]], ["Fire Knight Hilde", [], [20203000, 20203001, 20203002, 20203003, 20203004, 20203005, 20203006, 20203007, 20203008, 20203009, 20203010]], ["Fire Knight Queelign", [], [20218000, 20218001, 20218002, 20218003, 20218004, 20218005, 20218006, 20218007, 20218008, 20218009, 20218010]], ["Fire Monk Ashes", [], [227000, 227001, 227002, 227003, 227004, 227005, 227006, 227007, 227008, 227009, 227010]], ["Fire Pot", [10030000, 10030001, 10030002, 10030003], []], ["Fire Spritestone", [210319000, 210319001, 210319002, 210319003, 210319004, 210319005], []], ["Fireproof Dried Liver", [], [3920]], ["Fireproof Pickled Liver", [], [20501130]], ["Flask of Cerulean Tears", [], [501050, 501051, 501052, 501053, 501054, 501055, 501056, 501057, 501058, 501059, 501060, 501061, 501062]], ["Flask of Crimson Tears", [], [501000, 501001, 501002, 501003, 501004, 501005, 501006, 501007, 501008, 501009, 501010, 501011, 501012]], ["Freezing Grease", [], [3140, 3144]], ["Freezing Pot", [10036000, 10036001], []], ["Frenzied Flame Pot", [210071000, 210071001], []], ["Frenzyflame Stone", [10331100, 10331101], []], ["Frozen Raisin", [10081200], []], ["Furlcalling Finger Remedy", [], [530]], ["Giant Rat Ashes", [], [233000, 233001, 233002, 233003, 233004, 233105, 233106, 233107, 233108, 233109, 233210]], ["Giantsflame Fire Pot", [10030200, 10030201, 10030202, 10030203], []], ["Glinting Nail", [210320002, 210320003, 210320004, 210320005, 210320006, 210320007, 210320008, 210320009, 210320010], []], ["Glintstone Scrap", [10305000, 10305001, 10305002, 10305003], []], ["Glintstone Sorcerer Ashes", [], [218000, 218001, 218002, 218003, 218004, 218005, 218006, 218007, 218008, 218009, 218010]], ["Glowstone", [10203000, 10203001], []], ["Godrick Soldier Ashes", [], [250000, 250001, 250002, 250003, 250004, 250005, 250006, 250007, 250008, 250009, 250010]], ["Gold-Pickled Fowl Foot", [], [3971]], ["Golden Grease", [], [20501430, 20501432]], ["Golden Horn Tender", [], [20501230]], ["Golden Rune [10]", [], [3278]], ["Golden Rune [11]", [], [3279]], ["Golden Rune [12]", [], [3280]], ["Golden Rune [13]", [], [3281]], ["Golden Rune [1]", [], [3269]], ["Golden Rune [2]", [], [3270]], ["Golden Rune [3]", [], [3271]], ["Golden Rune [4]", [], [3272]], ["Golden Rune [5]", [], [3273]], ["Golden Rune [6]", [], [3274]], ["Golden Rune [7]", [], [3275]], ["Golden Rune [9]", [], [3277]], ["Golden Vow", [210317000], []], ["Gourmet Scorpion Stew", [], [20501205]], ["Grace Mimic", [10205000, 10205001, 10205002, 10205003], []], ["Gravebird Ashes", [], [20202000, 20202001, 20202002, 20202003, 20202004, 20202005, 20202006, 20202007, 20202008, 20202009, 20202010]], ["Gravity Stone Chunk", [10307000, 10307001, 10307002, 10307003, 10307004], []], ["Gravity Stone Fan", [10306000, 10306001, 10306002, 10306003, 10306004, 10306005, 10306006, 10306007, 10306008, 10306009, 10306010, 10306011, 10306012, 10306013, 10306014, 10306015, 10306016, 10306017, 10306018, 10306019, 10306020, 10306021, 10306022], []], ["Greatshield Soldier Ashes", [], [248000, 248001, 248002, 248003, 248004, 248005, 248006, 248007, 248008, 248009, 248010]], ["Haligtree Soldier Ashes", [], [255000, 255001, 255002, 255003, 255004, 255005, 255006, 255007, 255008, 255009, 255010]], ["Hefty Fetid Pot", [210033000, 210033001], []], ["Hefty Fire Pot", [210030000, 210030001, 210030002, 210030003], []], ["Hefty Fly Pot", [210034000, 210034001, 210034002, 210034003], []], ["Hefty Freezing Pot", [210036000, 210036001], []], ["Hefty Frenzied Flame Pot", [210062000, 210062001], []], ["Hefty Furnace Pot", [210068000, 210068001], []], ["Hefty Lightning Pot", [210032000, 210032001, 210032010], []], ["Hefty Magic Pot", [210066000, 210066001], []], ["Hefty Oil Pot", [210038000, 210038001], []], ["Hefty Poison Pot", [210037000, 210037001], []], ["Hefty Rancor Pot", [210065000, 210065001, 210065002, 210065003], []], ["Hefty Red Lightning Pot", [210069000, 210069001, 210069010, 210069011, 210069012, 210069013, 210069014, 210069015, 210069016, 210069017], []], ["Hefty Rock Pot", [210031000, 210031001], []], ["Hefty Rot Pot", [210067000, 210067001], []], ["Hefty Volcano Pot", [210060000, 210060001], []], ["Hero's Rune [1]", [], [3283]], ["Hero's Rune [2]", [], [3284]], ["Hero's Rune [3]", [], [3285]], ["Hero's Rune [4]", [], [3286]], ["Hero's Rune [5]", [], [3287]], ["Holy Grease", [], [3185, 3187]], ["Holy Water Pot", [10035000, 10035001], []], ["Holyproof Dried Liver", [], [501180]], ["Holyproof Pickled Liver", [], [20501150]], ["Horned Bairn", [210313000, 210313001], []], ["Horned Warrior Ashes", [], [20214000, 20214001, 20214002, 20214003, 20214004, 20214005, 20214006, 20214007, 20214008, 20214009, 20214010]], ["Immunizing Cured Meat", [], [3960]], ["Immunizing White Cured Meat", [], [501310]], ["Innard Meat", [210170000], []], ["Inquisitor Ashes", [], [20205000, 20205001, 20205002, 20205003, 20205004, 20205005, 20205006, 20205007, 20205008, 20205009, 20205010]], ["Invigorating Cured Meat", [], [3961]], ["Invigorating White Cured Meat", [], [501320]], ["Iris of Occultation", [], [20502130]], ["Ironjar Aromatic", [10352000], []], ["Jarwight Puppet", [], [263000, 263001, 263002, 263003, 263004, 263005, 263006, 263007, 263008, 263009, 263010]], ["Jolán and Anna", [], [20220000, 20220001, 20220002, 20220003, 20220004, 20220005, 20220006, 20220007, 20220008, 20220009, 20220010]], ["Kaiden Sellsword Ashes", [], [231000, 231001, 231002, 231003, 231004, 231005, 231006, 231007, 231008, 231009, 231010]], ["Kindred of Rot Ashes", [], [224000, 224001, 224002, 224003, 224004, 224005, 224006, 224007, 224008, 224009, 224010]], ["Kukri", [10173000], []], ["Lamenter's Mask", [210203000], []], ["Land Squirt Ashes", [], [244000, 244001, 244002, 244003, 244004, 244005, 244006, 244007, 244008, 244009, 244010]], ["Lantern", [], [3245, 3246]], ["Large Glintstone Scrap", [10305100, 10305101, 10305102, 10305103, 10305104, 10305105, 10305106, 10305107], []], ["Latenna the Albinauric", [], [204000, 204001, 204002, 204003, 204004, 204005, 204006, 204007, 204008, 204009, 204010]], ["Leda's Rune", [], [20502950]], ["Leyndell Soldier Ashes", [], [252000, 252001, 252002, 252003, 252004, 252005, 252006, 252007, 252008, 252009, 252010]], ["Lhutel the Headless", [], [258000, 258001, 258002, 258003, 258004, 258005, 258006, 258007, 258008, 258009, 258010]], ["Lightning Grease", [], [3165, 3167]], ["Lightning Pot", [10032000, 10032001, 10032010], []], ["Lightningproof Dried Liver", [], [3930]], ["Lightningproof Pickled Liver", [], [20501140]], ["Lone Wolf Ashes", [], [232000, 232001, 232002, 232003, 232004, 232005, 232006, 232007, 232008, 232009, 232010]], ["Lord's Rune", [], [3288]], ["Lulling Branch", [], [20500910]], ["Mad Pumpkin Head Ashes", [], [243000, 243001, 243002, 243003, 243004, 243005, 243006, 243007, 243008, 243009, 243010]], ["Magic Grease", [], [3170, 3172]], ["Magic Pot", [10066000, 10066001], []], ["Man-Fly Ashes", [], [20211000, 20211001, 20211002, 20211003, 20211004, 20211005, 20211006, 20211007, 20211008, 20211009, 20211010]], ["Man-Serpent Ashes", [], [229000, 229001, 229002, 229003, 229004, 229005, 229006, 229007, 229008, 229009, 229010]], ["Margit's Shackle", [10214000], []], ["Marika's Rune", [], [20502960]], ["Marionette Soldier Ashes", [], [225000, 225001, 225002, 225003, 225004, 225005, 225006, 225007, 225008, 225009, 225010]], ["Mausoleum Soldier Ashes", [], [254000, 254001, 254002, 254003, 254004, 254005, 254006, 254007, 254008, 254009, 254010]], ["Memory of Grace", [], [3226]], ["Messmer Soldier Ashes", [], [20207000, 20207001, 20207002, 20207003, 20207004, 20207005, 20207006, 20207007, 20207008, 20207009, 20207010]], ["Messmerfire Grease", [], [20501400, 20501402]], ["Mimic Tear Ashes", [], [207000, 207001, 207002, 207003, 207004, 207005, 207006, 207007, 207008, 207009, 207010]], ["Mimic's Veil", [10304000], []], ["Miquella's Great Rune", [], [19683]], ["Miquella's Needle", [], [502190]], ["Miranda Sprout Ashes", [], [245000, 245002, 245003, 245004, 245005, 245006, 245007, 245008, 245009, 245010]], ["Miranda Sprout Ashes +1 ", [], [245001]], ["Mohg's Shackle", [10215000], []], ["Nepheli Loux Puppet", [], [259000, 259001, 259002, 259003, 259004, 259005, 259006, 259007, 259008, 259009, 259010]], ["Neutralizing Boluses", [], [3060]], ["Nightmaiden & Swordstress Puppets", [], [206000, 206001, 206002, 206003, 206004, 206005, 206006, 206007, 206008, 206009, 206010]], ["Noble Sorcerer Ashes", [], [241000, 241001, 241002, 241003, 241004, 241005, 241006, 241007, 241008, 241009, 241010]], ["Nomad Ashes", [], [205000, 205001, 205002, 205003, 205004, 205005, 205006, 205007, 205008, 205009, 205010]], ["Numen's Rune", [], [3282]], ["Oil Pot", [10038000, 10038001], []], ["Omen Bairn", [10301000, 10301001, 10301002, 10301010, 10301020, 10301021, 10301022], []], ["Omenkiller Rollo", [], [247000, 247001, 247002, 247003, 247004, 247005, 247006, 247007, 247008, 247009, 247010]], ["Opaline Pickled Liver", [], [20501160]], ["Oracle Envoy Ashes", [], [214000, 214001, 214002, 214003, 214004, 214005, 214006, 214007, 214008, 214009, 214010]], ["Page Ashes", [], [220000, 220001, 220002, 220003, 220004, 220005, 220006, 220007, 220008, 220009, 220010]], ["Perfumed Oil of Ranah", [210314000], []], ["Perfumer Tricia", [], [217000, 217001, 217002, 217003, 217004, 217005, 217006, 217007, 217008, 217009, 217010]], ["Phantom Great Rune", [10013500, 10013501], []], ["Pickled Turtle Neck", [], [3953]], ["Poison Grease", [], [3175, 3179]], ["Poison Pot", [10037000, 10037001], []], ["Poison Spraymist", [10358000, 10358001], []], ["Poisonbone Dart", [10172000], []], ["Poisoned Stone", [10184000], []], ["Poisoned Stone Clump", [10184100], []], ["Polter Stone", [210200000, 210200001, 210200005], []], ["Prattling Pate \"Apologies\"", [10220200], []], ["Prattling Pate \"Let's get to it\"", [10220600], []], ["Prattling Pate \"My beloved\"", [10220500], []], ["Prattling Pate \"Please help\"", [10220400], []], ["Prattling Pate \"Thank you\"", [10220100], []], ["Prattling Pate \"Wonderful\"", [10220300], []], ["Prattling Pate \"You're beautiful\"", [], [9690]], ["Preserving Boluses", [], [3070]], ["Priestess Heart", [], [19981]], ["Pureblood Knight's Medal", [], [502161]], ["Putrid Corpse Ashes", [], [215000, 215001, 215002, 215003, 215004, 215005, 215006, 215007, 215008, 215009, 215010]], ["Radahn Soldier Ashes", [], [253000, 253001, 253002, 253003, 253004, 253005, 253006, 253007, 253008, 253009, 253010]], ["Radiant Baldachin's Blessing", [], [503361]], ["Rainbow Stone", [130, 131, 132], []], ["Rancor Pot", [10065000, 10065001, 10065002, 10065003], []], ["Raw Meat Dumpling", [], [501235]], ["Raya Lucaria Soldier Ashes", [], [251000, 251001, 251002, 251003, 251004, 251005, 251006, 251007, 251008, 251009, 251010]], ["Recusant Finger", [], [16]], ["Red Lightning Pot", [210070000, 210070001, 210070010, 210070011, 210070012, 210070013, 210070014, 210070015], []], ["Redmane Fire Pot", [10030100, 10030101, 10030102, 10030103], []], ["Redmane Knight Ogha", [], [257000, 257001, 257002, 257003, 257004, 257005, 257006, 257007, 257008, 257009, 257010]], ["Regal Omen Bairn", [10301100, 10301101, 10301102, 10301110, 10301120, 10301121, 10301122], []], ["Rejuvenating Boluses", [], [3090]], ["Rock Heart", [], [19980]], ["Roped Fetid Pot", [10043000, 10043001], []], ["Roped Fire Pot", [10040000, 10040001, 10040002, 10040003], []], ["Roped Fly Pot", [10047000, 10047001, 10047002, 10047003], []], ["Roped Frenzied Flame Pot", [210076000, 210076001], []], ["Roped Holy Water Pot", [10051000, 10051001], []], ["Roped Lightning Pot", [10042000, 10042001, 10042010, 10042011, 10042012, 10042013, 10042014, 10042015], []], ["Roped Magic Pot", [10046000, 10046001], []], ["Roped Oil Pot", [10045000, 10045001], []], ["Roped Poison Pot", [10044000, 10044001], []], ["Roped Volcano Pot", [10049000, 10049001], []], ["Rot Grease", [], [3310, 3314]], ["Rot Pot", [10067000, 10067001], []], ["Rotten Stray Ashes", [], [235000, 235001, 235002, 235003, 235004, 235005, 235006, 235007, 235008, 235009, 235010]], ["Roundrock", [210172000, 210172001, 210172002], []], ["Rowa Raisin", [10081000], []], ["Royal Magic Grease", [], [20501420, 20501422]], ["Ruin Fragment", [10176000], []], ["Rune of an Unsung Hero", [], [20502959]], ["Sacred Bloody Flesh", [], [20501210]], ["Sacred Order Pot", [10035100, 10035101], []], ["Scorpion Stew", [], [20501200]], ["Scriptstone", [], [503320]], ["Shadow Realm Rune [1]", [], [20502952]], ["Shadow Realm Rune [2]", [], [20502953]], ["Shadow Realm Rune [3]", [], [20502954]], ["Shadow Realm Rune [4]", [], [20502955]], ["Shadow Realm Rune [5]", [], [20502956]], ["Shadow Realm Rune [6]", [], [20502957]], ["Shadow Realm Rune [7]", [], [20502958]], ["Shield Grease", [], [501690, 501691]], ["Silver Horn Tender", [], [20501220]], ["Silver-Pickled Fowl Foot", [], [3970]], ["Skeletal Bandit Ashes", [], [213000, 213001, 213002, 213003, 213004, 213005, 213006, 213007, 213008, 213009, 213010]], ["Skeletal Militiaman Ashes", [], [212000, 212001, 212002, 212003, 212004, 212005, 212006, 212007, 212008, 212009, 212010]], ["Sleep Pot", [10064000, 10064001], []], ["Small Golden Effigy", [], [534]], ["Small Red Effigy", [], [535]], ["Soap", [], [502120]], ["Soft Cotton", [10210000], []], ["Soldjars of Fortune Ashes", [], [246000, 246001, 246002, 246003, 246004, 246005, 246006, 246007, 246008, 246009, 246010]], ["Soporific Grease", [], [3150, 3154]], ["Spark Aromatic", [10351000, 10351001], []], ["Spectral Steed Whistle", [], [81]], ["Spellproof Dried Liver", [], [3910]], ["Spellproof Pickled Liver", [], [20501120]], ["Spider Scorpion Ashes", [], [20204000, 20204001, 20204002, 20204003, 20204004, 20204005, 20204006, 20204007, 20204008, 20204009, 20204010]], ["Spirit Jellyfish Ashes", [], [236000, 236001, 236002, 236003, 236004, 236005, 236006, 236007, 236008, 236009, 236010]], ["Spirit Raisin", [210080000], []], ["Spritestone", [210300100, 210300101, 210300102], []], ["Stanching Boluses", [], [3050]], ["Starlight Shards", [], [501290]], ["Stimulating Boluses", [], [3055]], ["Stormhawk Deenh", [], [238000, 238001, 238002, 238003, 238004, 238005, 238006, 238007, 238008, 238009, 238010]], ["Sunwarmth Stone", [210331000, 210331001], []], ["Surging Frenzied Flame", [210316000, 210316001, 210316002, 210316010, 210316015, 210316016, 210316017, 210316018, 210316019, 210316020, 210316021, 210316022, 210316030, 210316031, 210316032], []], ["Swarm Pot", [10034000, 10034001, 10034002, 10034003], []], ["Sweet Raisin", [10081100], []], ["Swordhand of Night Jolán", [], [20219000, 20219001, 20219002, 20219003, 20219004, 20219005, 20219006, 20219007, 20219008, 20219009, 20219010]], ["Tarnished's Furled Finger", [], [4]], ["Taunter's Tongue", [], [533]], ["Taylew the Golem Smith", [], [20212000, 20212001, 20212002, 20212003, 20212004, 20212005, 20212006, 20212007, 20212008, 20212009, 20212010]], ["Telescope", [], [3240]], ["Thawfrost Boluses", [], [3092]], ["Thiollier's Concoction", [], [102350]], ["Throwing Dagger", [10170000], []], ["Twinsage Sorcerer Ashes", [], [219000, 219001, 219002, 219003, 219004, 219005, 219006, 219007, 219008, 219009, 219010]], ["Uplifting Aromatic", [10350000], []], ["Volcano Pot", [10060000, 10060001], []], ["Vulgar Militia Ashes", [], [242000, 242001, 242002, 242003, 242004, 242005, 242006, 242007, 242008, 242009, 242010]], ["Wandering Noble Ashes", [], [240000, 240001, 240002, 240003, 240004, 240005, 240006, 240007, 240008, 240009, 240010]], ["Warhawk Ashes", [], [237000, 237001, 237002, 237003, 237004, 237005, 237006, 237007, 237008, 237009, 237010]], ["Warming Stone", [10331000, 10331001], []], ["Well-Pickled Turtle Neck", [], [20501170]], ["White Cipher Ring", [], [531]], ["Winged Misbegotten Ashes", [], [210000]], ["Winged Misbegotten Ashes + 1", [], [210001]], ["Winged Misbegotten Ashes + 10", [], [210010]], ["Winged Misbegotten Ashes + 2", [], [210002]], ["Winged Misbegotten Ashes + 3", [], [210003]], ["Winged Misbegotten Ashes + 4", [], [210004]], ["Winged Misbegotten Ashes + 5", [], [210005]], ["Winged Misbegotten Ashes + 6", [], [210006]], ["Winged Misbegotten Ashes + 7", [], [210007]], ["Winged Misbegotten Ashes + 8", [], [210008]], ["Winged Misbegotten Ashes + 9", [], [210009]], ["Wraith Calling Bell", [10308000], []]], "skills": [["Alabaster Lords' Pull", [240]], ["Ancient Lightning Spear", [249]], ["Angel's Wings", [189]], ["Aspects of the Crucible: Wings", [256]], ["Assassin's Gambit", [132]], ["Barbaric Roar", [140]], ["Barrage", [101]], ["Barricade Shield", [91]], ["Bear Witness!", [211]], ["Beast's Roar", [142]], ["Black Flame Tornado", [69]], ["Blade of Death", [236]], ["Blade of Gold", [237]], ["Blind Spot", [276]], ["Blindfold of Happiness", [354]], ["Blinkbolt", [280]], ["Blinkbolt: Long-hafted Axe", [315]], ["Blinkbolt: Twinaxe", [314]], ["Blood Blade", [72]], ["Blood Tax", [8]], ["Bloodblade Dance", [179]], ["Bloodboon Ritual", [234]], ["Bloodfiends' Bloodboon", [368]], ["Bloodhound's Finesse", [175]], ["Bloodhound's Step", [156]], ["Bloody Slash", [54]], ["Braggart's Roar", [144]], ["Bubble Shower", [204]], ["Buckler Parry", [93]], ["Carian Grandeur", [66]], ["Carian Greatsword", [67]], ["Carian Retaliation", [95]], ["Carian Sovereignty", [285]], ["Causality's Wrath", [373]], ["Charge Forth", [5]], ["Chilling Mist", [75]], ["Claw Flick", [246]], ["Contagious Fury", [197]], ["Corpse Piler", [177]], ["Corpse Wax Cutter", [167]], ["Cragblade", [137]], ["Cursed-Blood Slice", [227]], ["Darkness", [303]], ["Deadly Dance", [336]], ["Deadly Poison Spray", [358]], ["Death Flare", [174]], ["Destined Death", [238]], ["Determination", [130]], ["Devonia's Vortex", [319]], ["Devourer of Worlds", [183]], ["Discus Hurl", [348]], ["Divine Beast Frost Stomp", [283]], ["Double Slash", [12]], ["Dragonform Flame", [369]], ["Dragonwound Slash", [300]], ["Dryleaf Whirlwind", [258]], ["Dynast's Finesse", [171]], ["Dynastic Sickleplay", [313]], ["Earthshaker", [61]], ["Enchanted Shot", [103]], ["Endure", [150]], ["Eochaid's Dancing Blade", [212]], ["Erdtree Slam", [193]], ["Eruption", [56]], ["Establish Order", [222]], ["Euporia Vortex", [328]], ["Familial Rancor", [184]], ["Fan Shot", [337]], ["Feeble Lord's Frenzied Flame", [334]], ["Firebreather", [71]], ["Fires of Slumber", [195]], ["Flame Dance", [245]], ["Flame Skewer", [281]], ["Flame Spear", [284]], ["Flame Spit", [201]], ["Flame of the Redmanes", [114]], ["Flaming Strike", [63]], ["Flare, O Serpent", [360]], ["Flower Dragonbolt", [349]], ["Flowing Form", [218, 235]], ["Frenzyflame Thrust", [250]], ["Ghostflame Call", [364]], ["Ghostflame Ignition", [248]], ["Giant Hunt", [16]], ["Glintblade Phalanx", [50]], ["Glintstone Dart", [217]], ["Glintstone Pebble", [53]], ["Gold Breaker", [229]], ["Golden Crux", [323]], ["Golden Land", [62]], ["Golden Parry", [97]], ["Golden Retaliation", [196]], ["Golden Slam", [116]], ["Golden Tempering", [252]], ["Golden Vow", [133]], ["Gravitas", [58]], ["Gravity Bolt", [194]], ["Great Oracular Bubble", [205]], ["Great-Serpent Hunt", [188]], ["Ground Slam", [115]], ["Hoarah Loux's Earthshaker", [118]], ["Hoarfrost Stomp", [110]], ["Holy Ground", [152]], ["Hone Blade", [272]], ["Horn Calling", [362]], ["Horn Calling: Storm", [363]], ["I Command Thee, Kneel!", [230]], ["Ice Lightning Sword", [243]], ["Ice Spear", [52]], ["Igon's Drake Hunt", [357]], ["Impaling Thrust", [1]], ["Jori's Inquisition", [356]], ["Kick", [112]], ["Knowledge Above All", [182]], ["Kowtower's Resentment", [350]], ["Last Rites", [253]], ["Lifesteal Fist", [55]], ["Light", [302]], ["Lightning Ram", [113]], ["Lightning Slash", [65]], ["Lightspeed Slash", [370]], ["Lion's Claw", [0]], ["Loretta's Slash", [18]], ["Madding Spear-Hand Strike", [333]], ["Magma Guillotine", [176]], ["Magma Shower", [172]], ["Messmer's Assault", [320]], ["Mighty Shot", [102]], ["Miquella's Ring of Light", [251]], ["Mists of Eternal Sleep", [311]], ["Mists of Slumber", [223]], ["Moon-and-Fire Stance", [318]], ["Moonlight Greatsword", [208]], ["Moore's Charge", [324]], ["Muleta", [372]], ["Nebula", [173, 247]], ["Needle Piercer", [301]], ["Night-and-Flame Stance", [219]], ["No Skill", [255]], ["Oath of Vengeance", [242]], ["Onyx Lords' Repulsion", [241]], ["Onze's Line of Stars", [304]], ["Oracular Bubble", [203]], ["Ordovis's Vortex", [198]], ["Overhead Stance", [278]], ["Painful Strike", [269]], ["Palm Blast", [261]], ["Parry", [92]], ["Phantom Slash", [73]], ["Piercing Fang", [2]], ["Piercing Throw", [262]], ["Poison Moth Flight", [19]], ["Poison Spear-Hand Strike", [332]], ["Poisonous Mist", [76]], ["Prayerful Strike", [57]], ["Prelate's Charge", [13]], ["Promised Consort", [316]], ["Quickstep", [155]], ["Radahn's Rain", [169]], ["Raging Beast", [273]], ["Rain of Arrows", [105]], ["Rallying Standard", [210]], ["Rancor Shot", [371]], ["Rancor Slash", [365, 366]], ["Raptor of the Mists", [157]], ["Red Bear Hunt", [275]], ["Reduvia Blood Blade", [216]], ["Regal Beastclaw", [244]], ["Regal Roar", [231]], ["Repeating Fire", [335]], ["Repeating Thrust", [9]], ["Revenge of the Night", [352]], ["Revenger's Blade", [310]], ["Roaring Bash", [359]], ["Rolling Sparks", [265]], ["Romina's Purification", [331]], ["Rosus's Summons", [185]], ["Royal Knight's Resolve", [131]], ["Ruinous Ghostflame", [221]], ["Sacred Blade", [51]], ["Sacred Order", [134]], ["Sacred Phalanx", [187]], ["Sacred Ring of Light", [70]], ["Savage Claws", [274]], ["Savage Lion's Claw", [282]], ["Scattershot (Claws)", [361]], ["Scattershot Throw", [263]], ["Sea of Magma", [206]], ["Seppuku", [136]], ["Shadow Sunflower Headbutt", [317]], ["Shared Order", [135]], ["Shield Bash", [90]], ["Shield Crash", [98]], ["Shriek of Milos", [215]], ["Shriek of Sorrow", [286]], ["Siluria's Woe", [209]], ["Sky Shot", [108]], ["Sleep Evermore", [322]], ["Smithing Art Spears", [329]], ["Solitary Moon Slash", [351]], ["Sorcery of the Crozier", [192]], ["Soul Stifler", [213]], ["Spearcall Ritual", [224]], ["Spectral Lance", [74]], ["Spinning Chain", [25]], ["Spinning Gravity Thrust", [260]], ["Spinning Guillotine", [308]], ["Spinning Slash", [3]], ["Spinning Strikes", [11]], ["Spinning Weapon", [20, 199]], ["Spinning Wheel", [239]], ["Square Off", [15]], ["Stamp (Sweep)", [7]], ["Stamp (Upward Cut)", [6]], ["Starcaller Cry", [232]], ["Storm Assault", [22]], ["Storm Blade", [59]], ["Storm Kick", [190]], ["Storm Stomp", [111]], ["Storm Wall", [96]], ["Stormcaller", [23]], ["Surge of Faith", [200]], ["Swift Slash", [277]], ["Sword Dance", [24]], ["Taker's Flames", [214]], ["The Poison Flower Blooms Twice", [305]], ["The Queen's Black Flame", [170]], ["Thops's Barrier", [99]], ["Through and Through", [100]], ["Thunderbolt", [64]], ["Thundercloud Form", [226]], ["Thunderstorm", [186]], ["Tongues of Fire", [202]], ["Torch Attack", [17]], ["Transient Moonlight", [178]], ["Tremendous Phalanx", [367]], ["Troll's Roar", [143]], ["Unblockable Blade", [191, 254]], ["Unending Dance", [309]], ["Unsheathe", [14]], ["Vacuum Slice", [68]], ["Viper Bite", [207]], ["Vow of the Indomitable", [151]], ["Wall of Sparks", [264]], ["War Cry", [141]], ["Waterfowl Dance", [228]], ["Wave of Destruction", [233]], ["Wave of Gold", [220]], ["Waves of Darkness", [117]], ["Weed Cutter", [353]], ["White Light Charge", [325]], ["White Shadow's Lure", [160]], ["Wild Strikes", [10]], ["Wing Stance", [279]], ["Witching Hour Slash", [327]], ["Wolf's Assault", [225]], ["Zamor Ice Storm", [168]]], "spells": [["Adula's Moonblade", [10443100, 10443101, 10443110, 10443111, 10443120, 10443121, 10443130, 10443131], []], ["Agheel's Flame", [10700100, 10700101, 10700102, 10700110], []], ["Ambush Shard", [10460000, 10460001, 10460002, 10460003], []], ["Ancient Death Rancor", [10500100, 10500101, 10500102, 10500105, 10500150, 10500151, 10500152, 10500155], []], ["Ancient Dragons' Lightning Spear", [10694000, 10694001, 10694002, 10694003, 10694004, 10694005, 10694006, 10694007, 10694008, 10694009, 10694010, 10694011, 10694012, 10694013, 10694014, 10694015], []], ["Ancient Dragons' Lightning Strike", [10691000, 10691001, 10691002, 10691003, 10691004, 10691005, 10691010, 10691011, 10691012, 10691013, 10691014, 10691015, 10691016, 10691020, 10691021, 10691022, 10691023, 10691024, 10691025, 10691050, 10691051, 10691052, 10691053, 10691054, 10691055, 10691060, 10691061, 10691062, 10691063, 10691064, 10691065, 10691066, 10691070, 10691071, 10691072, 10691073, 10691074, 10691075], []], ["Aspects of the Crucible: Bloom", [210666000, 210666001, 210666002, 210666003, 210666004, 210666005, 210666050, 210666051, 210666052, 210666053, 210666054, 210666055], []], ["Aspects of the Crucible: Breath", [10752000, 10752001], []], ["Aspects of the Crucible: Thorns", [210665000, 210665001, 210665002, 210665003, 210665004, 210665005, 210665006], []], ["Assassin's Approach", [10651001], [1651000]], ["Barrier of Gold", [10633000, 10633001], []], ["Bayle's Flame Lightning", [210701000, 210701001, 210701002, 210701003], []], ["Bayle's Tyranny", [210700000, 210700001, 210700002, 210700003, 210700004, 210700005], []], ["Beast Claw", [10682000, 10682001, 10682002, 10682003, 10682004, 10682005, 10682006, 10682050, 10682051, 10682052, 10682053, 10682054, 10682055, 10682056, 10682057, 10682058, 10682059, 10682060, 10682061, 10682062], []], ["Bestial Constitution", [10686000, 10686001], []], ["Bestial Sling", [10680000, 10680001], []], ["Bestial Vitality", [], [1685000]], ["Black Blade", [10753000, 10753001, 10753010, 10753011, 10753020, 10753021, 10753030, 10753031], []], ["Black Flame", [10621000, 10621001, 10621050, 10621051], []], ["Black Flame Blade", [], [1626000]], ["Black Flame Ritual", [10625000, 10625001, 10625002, 10625003, 10625050, 10625051, 10625052, 10625053], []], ["Black Flame's Protection", [], [1627000]], ["Blades of Stone", [210470000, 210470001, 210470002, 210470003, 210470004, 210470005, 210470006, 210470007, 210470008, 210470050, 210470051, 210470052, 210470053, 210470054, 210470055, 210470056, 210470057, 210470058, 210470060, 210470061, 210470062, 210470063, 210470064, 210470065, 210470066, 210470067, 210470068, 210470070, 210470071, 210470072, 210470073, 210470074, 210470075, 210470076, 210470077, 210470078], []], ["Blessing of the Erdtree", [10643100, 10643101], []], ["Blessing's Boon", [10643000, 10643001], []], ["Bloodboon", [10631000, 10631001, 10631002, 10631005], []], ["Bloodflame Blade", [], [1632000]], ["Bloodflame Talons", [10630000, 10630001], []], ["Borealis's Mist", [10702100, 10702101, 10702102, 10702110], []], ["Briars of Punishment", [10491000, 10491001, 10491002, 10491003, 10491004, 10491005, 10491006], []], ["Briars of Sin", [10490000, 10490001, 10490002], []], ["Burn, O Flame!", [10612000, 10612001, 10612002, 10612003, 10612004, 10612005, 10612050, 10612051, 10612052, 10612053, 10612054, 10612055], []], ["Cannon of Haima", [10408000, 10408001, 10408050, 10408051], []], ["Carian Phalanx", [10430100, 10430101], []], ["Carian Retaliation", [10464000, 10464001], []], ["Catch Flame", [10600000, 10600001, 10600002, 10600005, 10600006], []], ["Cherishing Fingers", [210742000, 210742001, 210742002, 210742003, 210742004, 210742005, 210742006, 210742007, 210742008, 210742050, 210742051, 210742052, 210742053, 210742054, 210742055, 210742056, 210742057, 210742058], []], ["Collapsing Stars", [10472100, 10472101, 10472105, 10472150, 10472151, 10472155], []], ["Comet", [10402100, 10402150, 10402151], []], ["Comet Azur", [10420000, 10420001, 10420002, 10420003, 10420004], []], ["Crystal Barrage", [10406000, 10406001, 10406099], []], ["Crystal Burst", [10409000, 10409001, 10409002, 10409050, 10409051, 10409052], []], ["Crystal Release", [10451000, 10451001, 10451002, 10451010, 10451011, 10451012, 10451020, 10451021, 10451022], []], ["Crystal Torrent", [10452000, 10452001, 10452002], []], ["Cure Poison", [10644000], []], ["Darkness", [10653000, 10653001], []], ["Death Lightning", [10504000, 10504001, 10504002, 10504003, 10504004, 10504050, 10504051, 10504052, 10504053, 10504054], []], ["Discus of Light", [10670000, 10670001, 10670002, 10670003], []], ["Divine Beast Tornado", [210773000, 210773001, 210773010, 210773050, 210773051], []], ["Divine Bird Feathers", [210774000], []], ["Divine Fortification", [], [1648000]], ["Dragonbolt Blessing", [], [1697100]], ["Dragonbolt of Florissax", [210691000, 210691001], []], ["Dragonclaw", [10706000, 10706001, 10706002, 10706003], []], ["Dragonfire", [10700000, 10700001, 10700002], []], ["Dragonice", [10702000, 10702001, 10702002], []], ["Ekzykes's Decay", [10703100, 10703101, 10703102, 10703110], []], ["Elden Stars", [10672000, 10672001, 10672002, 10672003, 10672004, 10672005, 10672006], []], ["Electrify Armament", [], [1696000]], ["Electrocharge", [210692000, 210692050], [21692000, 21692010]], ["Erdtree Heal", [10642400, 10642401], []], ["Eternal Darkness", [10465000, 10465001], []], ["Explosive Ghostflame", [10501000, 10501001, 10501002, 10501003, 10501004, 10501005, 10501006, 10501007, 10501008, 10501009, 10501010, 10501020], []], ["Fia's Mist", [10502000, 10502001, 10502050, 10502051], []], ["Fire Serpent", [210780000, 210780001, 210780002, 210780003, 210780050, 210780051, 210780052, 210780053], []], ["Fire's Deadly Sin", [10790001, 10790051, 10790099], [1790000, 1790010]], ["Flame Fortification", [], [1645000]], ["Flame Sling", [10601000, 10601001, 10601050, 10601051], []], ["Flame of the Fell God", [10611000, 10611001, 10611002, 10611003, 10611004, 10611005, 10611006, 10611007, 10611008, 10611009], []], ["Flame, Cleanse Me", [10604000, 10604001, 10604002], [1604005]], ["Flame, Fall Upon Them", [10602000, 10602001, 10602002, 10602050, 10602051, 10602052], []], ["Flame, Grant Me Strength", [], [1605000]], ["Flame, Protect Me", [], [1606000]], ["Fleeting Microcosm", [210741000, 210741001, 210741002, 210741003, 210741004, 210741005, 210741006, 210741050, 210741051, 210741052, 210741053, 210741054, 210741055, 210741056], []], ["Fortissax's Lightning Spear", [10694100, 10694101, 10694102, 10694103, 10694104, 10694105, 10694106, 10694107, 10694108, 10694109, 10694110, 10694111, 10694112, 10694113, 10694114, 10694115, 10694150, 10694151, 10694152, 10694153, 10694154, 10694155, 10694156, 10694157, 10694158, 10694159, 10694160, 10694161, 10694162, 10694163, 10694164, 10694165], []], ["Founding Rain of Stars", [10421000, 10421001, 10421002, 10421003, 10421004, 10421005, 10421006, 10421007, 10421008, 10421050, 10421051, 10421052, 10421053, 10421054, 10421055, 10421056, 10421057, 10421058], []], ["Freezing Mist", [10442000, 10442001, 10442050, 10442051], []], ["Frenzied Burst", [10732000, 10732050], []], ["Frozen Armament", [], [1449000]], ["Frozen Lightning Spear", [10692100, 10692101, 10692102, 10692103, 10692104, 10692105, 10692106, 10692107, 10692109, 10692110, 10692111, 10692112], []], ["Furious Blade of Ansbach", [210630000, 210630001, 210630002, 210630003, 210630004, 210630005, 210630006, 210630010, 210630011, 210630012, 210630013, 210630014, 210630015, 210630016, 210630050, 210630051, 210630052, 210630053, 210630054, 210630055, 210630056, 210630060, 210630061, 210630062, 210630063, 210630064, 210630065, 210630066], []], ["Gavel of Haima", [10412000, 10412001], []], ["Gelmir's Fury", [10481000, 10481001, 10481002, 10481003, 10481004, 10481005, 10481010, 10481011, 10481015], []], ["Ghostflame Breath", [210702000, 210702001, 210702002, 210702010], []], ["Giant Golden Arc", [210771000, 210771001, 210771050, 210771051], []], ["Giantsflame Take Thee", [10610000, 10610001, 10610050, 10610051], []], ["Glintblade Phalanx", [10430000, 10430001], []], ["Glintblade Trio", [210431000, 210431001, 210431002, 210431003, 210431004, 210431005, 210431006, 210431050, 210431051, 210431052, 210431053, 210431054, 210431055, 210431056], []], ["Glintstone Arc", [10407000, 10407001, 10407002, 10407003, 10407004, 10407005, 10407006, 10407007, 10407099], []], ["Glintstone Breath", [10704000, 10704001, 10704002], []], ["Glintstone Cometshard", [10402000, 10402050], []], ["Glintstone Icecrag", [10440000], []], ["Glintstone Nail", [210450000, 210450001, 210450002, 210450003, 210450004, 210450005, 210450006, 210450007, 210450008, 210450009, 210450010, 210450050, 210450051, 210450052, 210450053, 210450054, 210450055, 210450056, 210450057, 210450058, 210450059, 210450060], []], ["Glintstone Nails", [210451000, 210451001, 210451002, 210451003, 210451004, 210451005, 210451006, 210451007, 210451008, 210451009, 210451010, 210451050, 210451051, 210451052, 210451053, 210451054, 210451055, 210451056, 210451057, 210451058, 210451059, 210451060], []], ["Glintstone Pebble", [10400000, 10400099], []], ["Glintstone Stars", [10404000, 10404001, 10404002, 10404003, 10404004, 10404005, 10404006, 10404007, 10404008, 10404009, 10404010, 10404011, 10404050, 10404051, 10404052, 10404053, 10404054, 10404055, 10404056, 10404057, 10404058, 10404059, 10404060, 10404061], []], ["Golden Arcs", [210770000, 210770050], []], ["Golden Lightning Fortification", [10790300, 10790301], []], ["Golden Vow", [10660000], []], ["Gravitational Missile", [210471000, 210471001, 210471002, 210471003, 210471004, 210471005, 210471006, 210471007, 210471050, 210471051, 210471052, 210471053, 210471054, 210471055, 210471056, 210471057, 210471058, 210471059, 210471060], []], ["Gravity Well", [10472000, 10472050], []], ["Great Glintstone Shard", [10400100, 10400199], []], ["Great Heal", [10642200, 10642201], []], ["Great Oracular Bubble", [10511000, 10511001, 10511050, 10511051], []], ["Greatblade Phalanx", [10430200, 10430201], []], ["Greyoll's Roar", [10709000, 10709001], []], ["Gurranq's Beast Claw", [10683000, 10683001, 10683002, 10683003, 10683004, 10683005, 10683006, 10683007, 10683008], []], ["Heal", [10642100, 10642101], []], ["Heal from Afar", [210640000, 210640001], []], ["Honed Bolt", [10693000, 10693001, 10693002, 10693003, 10693004, 10693005, 10693006, 10693007, 10693008], []], ["Howl of Shabriri", [10733000, 10733001], [1733000]], ["Immutable Shield", [], [1674000]], ["Impenetrable Thorns", [210491000, 210491001, 210491002, 210491003, 210491004, 210491005, 210491006, 210491007, 210491008], []], ["Inescapable Frenzy", [10730000, 10730001], []], ["Knight's Lightning Spear", [210690000, 210690001, 210690002, 210690003, 210690004, 210690005, 210690006, 210690007, 210690008, 210690009, 210690010, 210690050, 210690051, 210690052, 210690053, 210690054, 210690055, 210690056, 210690057, 210690058, 210690059, 210690060, 210690061, 210690062, 210690063, 210690064, 210690065], []], ["Land of Shadow", [210668000, 210668001, 210668002, 210668003, 210668004, 210668005, 210668006, 210668007, 210668008, 210668009], []], ["Lansseax's Glaive", [10695000, 10695001, 10695002, 10695003, 10695004, 10695005, 10695006, 10695007, 10695008, 10695009, 10695010, 10695011, 10695012, 10695013], []], ["Law of Causality", [10676000], [1676000, 1676005]], ["Law of Regression", [10673000, 10673001, 10673002, 10673010, 10673011, 10673012, 10673013], []], ["Light of Miquella", [210670000, 210670001, 210670002, 210670003, 210670004, 210670005, 210670006, 210670007, 210670008], []], ["Lightning Fortification", [], [1647000]], ["Lightning Spear", [10690000, 10690001, 10690002, 10690003, 10690004, 10690050, 10690051, 10690052, 10690053, 10690054, 10690055, 10690056, 10690057, 10690058, 10690059], []], ["Lightning Strike", [10692000, 10692002, 10692003, 10692004, 10692005, 10692006, 10692009, 10692010, 10692011, 10692012, 10692013, 10692014, 10692015, 10692016], []], ["Litany of Proper Death", [10675000, 10675001, 10675002, 10675003, 10675004, 10675005, 10675006, 10675007, 10675008, 10675009, 10675010, 10675011, 10675012, 10675013, 10675014, 10675015], [1675000]], ["Lord's Aid", [10644100, 10644101, 10644110, 10644111], []], ["Lord's Divine Fortification", [10649000, 10649001], []], ["Lord's Heal", [10642300, 10642301], []], ["Loretta's Greatbow", [10438000, 10438001, 10438050, 10438051], []], ["Loretta's Mastery", [10438100, 10438101, 10438102, 10438110, 10438111, 10438112, 10438150, 10438151, 10438152, 10438160, 10438161, 10438162], []], ["Lucidity", [10448000, 10448001], []], ["Magic Downpour", [10437000, 10437001, 10437002, 10437003, 10437004, 10437005, 10437050, 10437051, 10437052, 10437053, 10437054, 10437055], []], ["Magic Fortification", [], [1646000]], ["Magic Glintblade", [10439000, 10439001, 10439002, 10439003, 10439050, 10439051, 10439052, 10439053], []], ["Magma Breath", [10701000, 10701001, 10701002, 10701003, 10701004, 10701005, 10701009, 10701010], []], ["Magma Shot", [10480000, 10480001, 10480002, 10480003, 10480004, 10480050, 10480051, 10480052, 10480053, 10480054], []], ["Mantle of Thorns", [], [21490000, 21490001, 21490002]], ["Mass of Putrescence", [210621000, 210621001, 210621002, 210621005, 210621006, 210621007, 210621050, 210621051, 210621052, 210621053, 210621054, 210621055, 210621056, 210621057, 210621058, 210621059, 210621060], []], ["Messmer's Orb", [210782000, 210782001, 210782050, 210782051], []], ["Meteorite", [10470000, 10470001, 10470002, 10470003, 10470005, 10470006, 10470007, 10470008, 10470010, 10470011, 10470012, 10470013, 10470020, 10470021, 10470022, 10470023, 10470030, 10470031, 10470032, 10470033, 10470040, 10470041, 10470042, 10470043], []], ["Meteorite of Astel", [10470100, 10470101, 10470102, 10470103, 10470104, 10470105, 10470106, 10470110, 10470111, 10470112, 10470113, 10470114, 10470115, 10470120, 10470121, 10470122, 10470125, 10470150, 10470151, 10470152], []], ["Midra's Flame of Frenzy", [210730000, 210730001, 210730002, 210730003, 210730004, 210730005, 210730006, 210730007, 210730008, 210730009, 210730010, 210730011], []], ["Minor Erdtree", [210667000, 210667001], []], ["Miriam's Vanishing", [], [19975, 21430000]], ["Multilayered Ring of Light", [210671000, 210671001, 210671002, 210671050, 210671051, 210671052], []], ["Night Comet", [10462000, 10462001, 10462050, 10462051], []], ["Night Maiden's Mist", [10650000, 10650001, 10650002, 10650050, 10650051, 10650052], []], ["Night Shard", [10461000, 10461001], []], ["Noble Presence", [10624000, 10624001, 10624050, 10624051], [1624000]], ["O, Flame!", [10600100, 10600101, 10600105, 10600106, 10600150, 10600151, 10600155, 10600156], []], ["Oracle Bubbles", [10510000, 10510001, 10510002, 10510050, 10510051, 10510052, 10510099], []], ["Order Healing", [10678000], []], ["Order's Blade", [], [1677000]], ["Pest Threads", [10720000, 10720001, 10720002, 10720003, 10720004, 10720005], []], ["Pest-Thread Spears", [210721000, 210721001, 210721050, 210721051], []], ["Placidusax's Ruin", [10705000, 10705001, 10705002, 10705003, 10705004, 10705010], []], ["Poison Armament", [], [1723000]], ["Poison Mist", [10722000, 10722001], []], ["Protection of the Erdtree", [10634000, 10634001], []], ["Radagon's Rings of Light", [10671000, 10671001, 10671050, 10671051], []], ["Rain of Fire", [210781000, 210781001, 210781002, 210781003, 210781004, 210781005, 210781006, 210781007, 210781008, 210781050, 210781051, 210781052, 210781053, 210781054, 210781055, 210781056, 210781057, 210781058], []], ["Rancorcall", [10500000, 10500001, 10500002, 10500005, 10500050, 10500051, 10500052, 10500055], []], ["Ranni's Dark Moon", [10436100, 10436101, 10436102], []], ["Rejection", [10640000, 10640050], [1640000]], ["Rellana's Twin Moons", [210432000, 210432001, 210432002, 210432003, 210432004, 210432005, 210432006], []], ["Rennala's Full Moon", [10436000, 10436001, 10436002], []], ["Rings of Spectral Light", [210500000, 210500001, 210500002, 210500003, 210500004, 210500005, 210500050, 210500051, 210500052, 210500053, 210500054, 210500055], []], ["Roar of Rugalea", [210680000, 210680050], []], ["Rock Blaster", [10411000, 10411010, 10411011, 10411012, 10411013, 10411014, 10411015], []], ["Rock Sling", [10471000, 10471001, 10471002, 10471003, 10471004, 10471005, 10471006, 10471007, 10471008, 10471009, 10471010, 10471011, 10471012, 10471050, 10471051, 10471052, 10471053, 10471054, 10471055, 10471056, 10471057, 10471058, 10471059, 10471060, 10471061, 10471062], []], ["Roiling Magma", [10482000, 10482001, 10482002, 10482003, 10482004, 10482005, 10482006, 10482007, 10482050, 10482051, 10482052, 10482053, 10482054, 10482055, 10482056, 10482057], []], ["Rotten Breath", [10703000, 10703001, 10703002], []], ["Rotten Butterflies", [210720000, 210720001, 210720002, 210720003, 210720004, 210720005, 210720006, 210720007, 210720008], []], ["Rykard's Rancor", [10483000, 10483001, 10483002, 10483003, 10483004, 10483005], []], ["Scarlet Aeonia", [10724000, 10724001], []], ["Scholar's Armament", [], [1446000]], ["Scholar's Shield", [], [1447000]], ["Scouring Black Flame", [10623000, 10623001, 10623050, 10623051], []], ["Shadow Bait", [10652000, 10652001], []], ["Shard Spiral", [10403000, 10403001, 10403050, 10403051], []], ["Shatter Earth", [10410000, 10410001, 10410002], []], ["Shattering Crystal", [10450000, 10450001, 10450002, 10450003, 10450004, 10450050, 10450051, 10450052, 10450053, 10450054], []], ["Smarag's Glintstone Breath", [10704100, 10704101, 10704102, 10704110], []], ["Spira", [210772000, 210772001, 210772002, 210772003, 210772004], []], ["Star Shower", [10405000, 10405001, 10405002, 10405003, 10405004, 10405005, 10405006, 10405007, 10405008, 10405009, 10405010, 10405011, 10405012, 10405013, 10405014, 10405015, 10405016, 10405017, 10405018, 10405019, 10405020, 10405021, 10405022, 10405023, 10405050, 10405051, 10405052, 10405053, 10405054, 10405055, 10405056, 10405057, 10405058, 10405059, 10405060, 10405061, 10405062, 10405063, 10405064, 10405065, 10405066, 10405067, 10405068, 10405069, 10405070, 10405071, 10405072, 10405073], []], ["Starlight", [], [1414000]], ["Stars of Ruin", [10422000, 10422001, 10422002, 10422003, 10422004, 10422005, 10422006, 10422007, 10422008, 10422009, 10422010, 10422011, 10422015, 10422016, 10422017, 10422018, 10422019, 10422020, 10422021, 10422022, 10422023, 10422024, 10422025, 10422026, 10422030, 10422031, 10422032, 10422033, 10422034, 10422035, 10422036, 10422037, 10422050, 10422051, 10422052, 10422053, 10422054, 10422055, 10422056, 10422057, 10422058, 10422059, 10422060, 10422061, 10422065, 10422066, 10422067, 10422068, 10422069, 10422070, 10422071, 10422072, 10422073, 10422074, 10422075, 10422076, 10422080, 10422081, 10422082, 10422083, 10422084, 10422085, 10422086, 10422087, 10422098, 10422099], []], ["Stone of Gurranq", [10681000, 10681001], []], ["Surge, O Flame!", [10622000], []], ["Swarm of Flies", [10721000, 10721001], []], ["Swift Glintstone Shard", [10401000], []], ["Terra Magica", [10413000, 10413001, 10413002, 10413003], []], ["The Flame of Frenzy", [10731000, 10731001, 10731002, 10731005, 10731006, 10731010, 10731050, 10731051, 10731052, 10731055, 10731056, 10731060], []], ["Theodorix's Magma", [10701100, 10701101, 10701102, 10701103, 10701104, 10701105, 10701109, 10701110], []], ["Thops's Barrier", [10463000], []], ["Tibia's Summons", [10503000, 10503001, 10503002, 10503003, 10503004, 10503010, 10503011], []], ["Triple Rings of Light", [10670100, 10670101, 10670102, 10670103, 10670105], []], ["Unendurable Frenzy", [10731100, 10731103, 10731104, 10731105, 10731106, 10731107, 10731108, 10731110, 10731115, 10731116, 10731117], []], ["Unseen Blade", [], [1466000]], ["Unseen Form", [10467001], [1467000, 1467001, 1467002]], ["Urgent Heal", [10642000], []], ["Vortex of Putrescence", [210620000, 210620001, 210620002, 210620003, 210620004, 210620005, 210620006, 210620007, 210620010, 210620011, 210620012, 210620013], []], ["Vyke's Dragonbolt", [], [1697000, 1697001]], ["Watchful Spirit", [210760000, 210760001], []], ["Whirl, O Flame!", [10603000, 10603050], []], ["Wrath from Afar", [210669000, 210669001, 210669050, 210669051], []], ["Wrath of Gold", [10641000, 10641050], [1641000]], ["Zamor Ice Storm", [10441000, 10441001, 10441002, 10441009, 10441050, 10441051, 10441052, 10441059], []]], "talismans": [["Aged One's Exultation", [8060]], ["Ailment Talisman", [8120]], ["Ancestral Spirit's Horn", [6110]], ["Arrow's Reach Talisman", [2100]], ["Arrow's Soaring Sting Talisman", [8070]], ["Arrow's Sting Talisman", [2150]], ["Arsenal Charm", [1030]], ["Arsenal Charm +1", [1031]], ["Assassin's Cerulean Dagger", [5060]], ["Assassin's Crimson Dagger", [5050]], ["Axe Talisman", [2130]], ["Beloved Stardust", [8190]], ["Blade of Mercy", [8230]], ["Blessed Blue Dew Talisman", [8000]], ["Blessed Dew Talisman", [5020]], ["Blue Dancer Charm", [2110]], ["Blue-Feathered Branchsword", [4080]], ["Boltdrake Talisman", [4030]], ["Boltdrake Talisman +1", [4031]], ["Boltdrake Talisman +2", [4032]], ["Boltdrake Talisman +3", [7110]], ["Broken Finger Stalker Contract", [101]], ["Bull-Goat's Talisman", [1210]], ["Carian Filigreed Crest", [6020]], ["Cerulean Amber Medallion", [1010]], ["Cerulean Amber Medallion +1", [1011]], ["Cerulean Amber Medallion +2", [1012]], ["Cerulean Amber Medallion +3", [7010]], ["Cerulean Seed Talisman", [5010]], ["Cerulean Seed Talisman +1", [7150]], ["Clarifying Horn Charm", [1180]], ["Clarifying Horn Charm +1", [1181]], ["Clarifying Horn Charm +2", [7060]], ["Claw Talisman", [2180]], ["Companion Jar", [2210]], ["Concealing Veil", [6010]], ["Crepus's Vial", [6000]], ["Crimson Amber Medallion", [1000]], ["Crimson Amber Medallion +1", [1001]], ["Crimson Amber Medallion +2", [1002]], ["Crimson Amber Medallion +3", [7000]], ["Crimson Seed Talisman", [5000]], ["Crimson Seed Talisman +1", [7140]], ["Crucible Feather Talisman", [4070]], ["Crucible Knot Talisman", [4110]], ["Crucible Scale Talisman", [4060]], ["Crusade Insignia", [8050]], ["Curved Sword Talisman", [2200]], ["Daedicar's Woe", [6060]], ["Dagger Talisman", [2090]], ["Dragoncrest Greatshield Talisman", [4003]], ["Dragoncrest Shield Talisman", [4000]], ["Dragoncrest Shield Talisman +1", [4001]], ["Dragoncrest Shield Talisman +2", [4002]], ["Dried Bouquet", [8100]], ["Enraged Divine Beast", [8180]], ["Erdtree's Favor", [1040]], ["Erdtree's Favor +1", [1041]], ["Erdtree's Favor +2", [1042]], ["Faithful's Canvas Talisman", [3040]], ["Fine Crucible Feather Talisman", [8010]], ["Fire Scorpion Charm", [2020]], ["Flamedrake Talisman", [4020]], ["Flamedrake Talisman +1", [4021]], ["Flamedrake Talisman +2", [4022]], ["Flamedrake Talisman +3", [7100]], ["Flock's Canvas Talisman", [3050]], ["Furled Finger's Trick-Mirror", [6080]], ["Godfrey Icon", [3090]], ["Godskin Swaddling Cloth", [5040]], ["Gold Scarab", [1110]], ["Golden Braid", [7120]], ["Graven-Mass Talisman", [3001]], ["Graven-School Talisman", [3000]], ["Great-Jar's Arsenal", [1032]], ["Greatshield Talisman", [4100]], ["Green Turtle Talisman", [1150]], ["Haligdrake Talisman", [4040]], ["Haligdrake Talisman +1", [4041]], ["Haligdrake Talisman +2", [4042]], ["Hammer Talisman", [2070]], ["Host's Trick-Mirror", [6090]], ["Immunizing Horn Charm", [1170]], ["Immunizing Horn Charm +1", [1171]], ["Immunizing Horn Charm +2", [7050]], ["Kindred of Rot's Exultation", [2170]], ["Lacerating Crossed-Tree", [8140]], ["Lance Talisman", [2140]], ["Lightning Scorpion Charm", [2010]], ["Longtail Cat Talisman", [6040]], ["Lord of Blood's Exultation", [2160]], ["Magic Scorpion Charm", [2000]], ["Marika's Scarseal", [1220]], ["Marika's Soreseal", [1221]], ["Millicent's Prosthesis", [1250]], ["Moon of Nokstella", [1140]], ["Mottled Necklace", [1200]], ["Mottled Necklace +1", [1201]], ["Mottled Necklace +2", [7080]], ["Old Lord's Talisman", [3060]], ["Outer God Heirloom", [8020]], ["Pearl Shield Talisman", [8090]], ["Pearldrake Talisman", [4050]], ["Pearldrake Talisman +1", [4051]], ["Pearldrake Talisman +2", [4052]], ["Pearldrake Talisman +3", [7130]], ["Perfumer's Talisman", [2220]], ["Petition for Help", [100]], ["Primal Glintstone Blade", [3080]], ["Prince of Death's Cyst", [1191]], ["Prince of Death's Pustule", [1190]], ["Prosthesis-Wearer Heirloom", [1070]], ["Radagon Icon", [3070]], ["Radagon's Scarseal", [1050]], ["Radagon's Soreseal", [1051]], ["Red-Feathered Branchsword", [2040]], ["Rellana's Cameo", [8220]], ["Retaliatory Crossed-Tree", [8130]], ["Ritual Shield Talisman", [4090]], ["Ritual Sword Talisman", [2050]], ["Roar Medallion", [2190]], ["Rotten Winged Sword Insignia", [2081]], ["Sacred Scorpion Charm", [2030]], ["Sacrificial Twig", [6070]], ["Shabriri's Woe", [6050]], ["Shard of Alexander", [1231]], ["Sharpshot Talisman", [8150]], ["Shattered Stone Talisman", [8030]], ["Silver Scarab", [1100]], ["Smithing Talisman", [8110]], ["Spear Talisman", [2060]], ["Spelldrake Talisman", [4010]], ["Spelldrake Talisman +1", [4011]], ["Spelldrake Talisman +2", [4012]], ["Spelldrake Talisman +3", [7090]], ["St. Trina's Smile", [8160]], ["Stalwart Horn Charm", [1160]], ["Stalwart Horn Charm +1", [1161]], ["Stalwart Horn Charm +2", [7040]], ["Stargazer Heirloom", [1080]], ["Starscourge Heirloom", [1060]], ["Taker's Cameo", [5030]], ["Talisman of All Crucibles", [8240]], ["Talisman of Lord's Bestowal", [8200]], ["Talisman of the Dread", [8170]], ["Twinblade Talisman", [2120]], ["Two Fingers Heirloom", [1090]], ["Two-Handed Sword Talisman", [8040]], ["Two-Headed Turtle Talisman", [7030]], ["Verdigris Discus", [8210]], ["Viridian Amber Medallion", [1020]], ["Viridian Amber Medallion +1", [1021]], ["Viridian Amber Medallion +2", [1022]], ["Viridian Amber Medallion +3", [7020]], ["Warrior Jar Shard", [1230]], ["Winged Sword Insignia", [2080]]], "tears": [["Bloodsucking Cracked Tear", [2011050]], ["Cerulean Crystal Tear", [11004, 11005]], ["Cerulean Hidden Tear", [11025]], ["Cerulean-Sapping Cracked Tear", [2011030]], ["Crimson Bubbletear", [11007]], ["Crimson Crystal Tear", [11002, 11003]], ["Crimson-Sapping Cracked Tear", [2011020]], ["Crimsonburst Crystal Tear", [11009]], ["Crimsonburst Dried Tear", [2011010]], ["Crimsonspill Crystal Tear", [11000]], ["Crimsonwhorl Bubbletear", [11020]], ["Deflecting Hardtear", [2011070]], ["Dexterity-knot Crystal Tear", [11022]], ["Faith-knot Crystal Tear", [11024]], ["Flame-Shrouding Cracked Tear", [11028]], ["Glovewort Crystal Tear", [2011060]], ["Greenburst Crystal Tear", [11010]], ["Greenspill Crystal Tear", [11001]], ["Holy-Shrouding Cracked Tear", [11031]], ["Intelligence-knot Crystal Tear", [11023]], ["Leaden Hardtear", [11018]], ["Lightning-Shrouding Cracked Tear", [11030]], ["Magic-Shrouding Cracked Tear", [11029]], ["Oil-Soaked Tear", [2011040]], ["Opaline Bubbletear", [11008]], ["Opaline Hardtear", [11011]], ["Purifying Crystal Tear", [11027]], ["Ruptured Crystal Tear", [11016, 11017]], ["Speckled Hardtear", [11006]], ["Spiked Cracked Tear", [11014]], ["Stonebarb Cracked Tear", [11026]], ["Strength-knot Crystal Tear", [11021]], ["Thorny Cracked Tear", [11013]], ["Twiggy Cracked Tear", [11019]], ["Viridian Hidden Tear", [2011000]], ["Windy Crystal Tear", [11015]], ["Winged Crystal Tear", [11012]]], "weapons": [["Academy Glintstone Staff", 57, [33200000]], ["Alabaster Lord's Sword", 5, [3070000]], ["Albinauric Bow", 51, [41010000]], ["Albinauric Shield", 67, [31040000]], ["Albinauric Staff", 57, [33190000]], ["Ancient Meteoric Ore Greatsword", 7, [4500000]], ["Ansbach's Longbow", 51, [41510000]], ["Ant's Skull Plate", 69, [32220000]], ["Antspur Rapier", 15, [5040000]], ["Anvil Hammer", 41, [12510000]], ["Arbalest", 55, [43080000]], ["Arrow", 81, [50000000]], ["Astrologer's Staff", 57, [33130000]], ["Axe of Godfrey", 41, [23050000]], ["Axe of Godrick", 19, [15040000]], ["Azur's Glintstone Staff", 57, [33230000]], ["Backhand Blade", 92, [64500000]], ["Ballista Bolt", 86, [53000000]], ["Bandit's Curved Sword", 9, [7040000]], ["Banished Knight's Greatsword", 5, [3080000]], ["Banished Knight's Halberd", 29, [18030000]], ["Banished Knight's Shield", 67, [31030000]], ["Barbed Staff-Spear", 28, [17520000]], ["Bastard Sword", 5, [3000000]], ["Bastard's Stars", 24, [13030000]], ["Battle Axe", 17, [14000000]], ["Battle Hammer", 23, [12020000]], ["Beast Claw", 95, [68500000]], ["Beast Crest Heater Shield", 67, [31280000]], ["Beast-Repellent Torch", 87, [24060000]], ["Beastclaw Greathammer", 23, [12150000]], ["Beastman's Cleaver", 11, [8080000]], ["Beastman's Curved Sword", 9, [7010000]], ["Beastman's Jar-Shield", 67, [30060000]], ["Black Bow", 51, [41070000]], ["Black Knife", 1, [1010000]], ["Black Leather Shield", 67, [31340000]], ["Black Steel Greathammer", 23, [12520000]], ["Black Steel Greatshield", 69, [32500000]], ["Black Steel Twinblade", 14, [10510000]], ["Black-Key Bolt", 85, [52030000]], ["Blade of Calling", 1, [1160000]], ["Blasphemous Blade", 5, [3140000]], ["Bloodbone Arrow", 81, [50310000]], ["Bloodbone Arrow (Fletched)", 81, [50300000]], ["Bloodbone Bolt", 85, [52150000]], ["Bloodfiend's Arm", 41, [12530000]], ["Bloodfiend's Fork", 25, [16540000]], ["Bloodfiend's Sacred Spear", 28, [16550000]], ["Bloodhound Claws", 37, [22020000]], ["Bloodhound's Fang", 11, [8030000]], ["Bloodstained Dagger", 1, [1140000]], ["Bloody Helice", 16, [6000000]], ["Blue Crest Heater Shield", 67, [31300000]], ["Blue-Gold Kite Shield", 67, [31100000]], ["Blue-White Wooden Shield", 65, [30100000]], ["Bolt", 85, [52000000]], ["Bolt of Gransax", 25, [16090000]], ["Bone Arrow", 81, [50110000]], ["Bone Arrow (Fletched)", 81, [50030000]], ["Bone Ballista Bolt", 86, [53030000]], ["Bone Bolt", 85, [52090000]], ["Bone Bow", 50, [40500000]], ["Bone Great Arrow", 83, [51060000]], ["Bone Great Arrow (Fletched)", 83, [51050000]], ["Bonny Butchering Knife", 19, [15510000]], ["Brass Shield", 67, [31130000]], ["Briar Greatshield", 69, [32050000]], ["Brick Hammer", 23, [12190000]], ["Broadsword", 3, [2020000]], ["Buckler", 65, [30000000]], ["Burred Bolt", 85, [52040000]], ["Butchering Knife", 19, [15120000]], ["Caestus", 35, [21000000]], ["Candletree Wooden Shield", 67, [31250000]], ["Cane Sword", 3, [2210000]], ["Carian Glintblade Staff", 57, [33170000]], ["Carian Glintstone Staff", 57, [33210000]], ["Carian Knight's Shield", 67, [31190000]], ["Carian Knight's Sword", 3, [2180000]], ["Carian Regal Scepter", 57, [33090000]], ["Carian Sorcery Sword", 15, [2530000]], ["Carian Thrusting Shield", 90, [62510000]], ["Celebrant's Cleaver", 17, [14060000]], ["Celebrant's Rib-Rake", 25, [16060000]], ["Celebrant's Sickle", 1, [1060000]], ["Celebrant's Skull", 23, [12130000]], ["Chainlink Flail", 24, [13040000]], ["Chilling Perfume Bottle", 89, [61510000]], ["Cinquedea", 1, [1110000]], ["Cipher Pata", 35, [21130000]], ["Clawmark Seal", 61, [34040000]], ["Claws of Night", 37, [22500000]], ["Clayman's Harpoon", 25, [16030000]], ["Claymore", 5, [3180000]], ["Cleanrot Knight's Sword", 15, [5010000]], ["Cleanrot Spear", 25, [16040000]], ["Clinging Bone", 35, [21110000]], ["Club", 21, [11010000]], ["Coded Sword", 3, [2110000]], ["Coil Shield", 65, [30200000]], ["Coldbone Arrow", 81, [50330000]], ["Coldbone Arrow (Fletched)", 81, [50320000]], ["Coldbone Bolt", 85, [52160000]], ["Commander's Standard", 29, [18040000]], ["Composite Bow", 50, [40050000]], ["Cranial Vessel Candlestand", 23, [12170000]], ["Crepus's Black-Key Crossbow", 55, [43110000]], ["Crescent Moon Axe", 19, [15030000]], ["Cross-Naginata", 25, [16110000]], ["Crossed-Tree Towershield", 69, [32200000]], ["Crucible Hornshield", 69, [32030000]], ["Crystal Knife", 1, [1050000]], ["Crystal Spear", 25, [16020000]], ["Crystal Staff", 57, [33040000]], ["Crystal Sword", 3, [2150000]], ["Cuckoo Greatshield", 69, [32250000]], ["Curseblade's Cirque", 92, [64520000]], ["Curved Club", 21, [11030000]], ["Curved Great Club", 23, [12080000]], ["Dagger", 1, [1000000]], ["Dancing Blade of Ranah", 9, [7520000]], ["Dane's Footwork", 88, [60510000]], ["Dark Moon Greatsword", 5, [3090000]], ["Deadly Poison Perfume Bottle", 89, [61540000]], ["Death Knight's Longhaft Axe", 19, [15500000]], ["Death Knight's Twin Axes", 17, [14510000]], ["Death Ritual Spear", 25, [16120000]], ["Death's Poker", 5, [3200000]], ["Demi-Human Queen's Staff", 57, [33060000]], ["Devonia's Hammer", 41, [23500000]], ["Devourer's Scepter", 23, [12200000]], ["Digger's Staff", 57, [33120000]], ["Dismounter", 11, [8020000]], ["Distinguished Greatshield", 69, [32020000]], ["Dragon Communion Seal", 61, [34080000]], ["Dragon Greatclaw", 41, [23060000]], ["Dragon Halberd", 29, [18140000]], ["Dragon King's Cragblade", 16, [6040000]], ["Dragon Towershield", 69, [32000000]], ["Dragon-Hunter's Great Katana", 94, [66510000]], ["Dragonclaw Shield", 69, [32040000]], ["Dragonscale Blade", 13, [9070000]], ["Dryleaf Arts", 88, [60500000]], ["Dryleaf Seal", 61, [34500000]], ["Dueling Shield", 90, [62500000]], ["Duelist Greataxe", 41, [23040000]], ["Dwelling Arrow", 81, [50100000]], ["Eclipse Crest Greatshield", 69, [32240000]], ["Eclipse Crest Heater Shield", 67, [31310000]], ["Eclipse Shotel", 9, [7100000]], ["Eleonora's Poleblade", 14, [10050000]], ["Envoy's Greathorn", 41, [23030000]], ["Envoy's Horn", 21, [11100000]], ["Envoy's Long Horn", 23, [12160000]], ["Erdsteel Dagger", 1, [1150000]], ["Erdtree Bow", 51, [41030000]], ["Erdtree Greatbow", 53, [42030000]], ["Erdtree Greatshield", 69, [32080000]], ["Erdtree Seal", 61, [34070000]], ["Estoc", 15, [5000000]], ["Euporia", 14, [10500000]], ["Executioner's Greataxe", 19, [15080000]], ["Explosive Bolt", 85, [52060000]], ["Explosive Greatbolt", 86, [53020000]], ["Falchion", 9, [7000000]], ["Fallingstar Beast Jaw", 41, [23080000]], ["Falx", 9, [7510000]], ["Family Heads", 24, [13020000]], ["Finger Seal", 61, [34000000]], ["Fingerprint Stone Shield", 69, [32130000]], ["Fire Arrow", 81, [50010000]], ["Fire Knight's Greatsword", 7, [4520000]], ["Fire Knight's Seal", 61, [34510000]], ["Fire Knight's Shortsword", 1, [1510000]], ["Firebone Arrow", 81, [50140000]], ["Firebone Arrow (Fletched)", 81, [50130000]], ["Firebone Bolt", 85, [52100000]], ["Firespark Perfume Bottle", 89, [61500000]], ["Flail", 24, [13010000]], ["Flamberge", 5, [3050000]], ["Flame Crest Wooden Shield", 67, [31260000]], ["Flaming Bolt", 85, [52190000]], ["Flowerstone Gavel", 21, [11500000]], ["Flowing Curved Sword", 9, [7060000]], ["Forked Greatsword", 5, [3010000]], ["Forked Hatchet", 17, [14010000]], ["Forked-Tongue Hatchet", 17, [14540000]], ["Frenzied Flame Seal", 61, [34090000]], ["Frenzyflame Perfume Bottle", 89, [61520000]], ["Freyja's Greatsword", 11, [8510000]], ["Frozen Needle", 15, [5050000]], ["Full Moon Crossbow", 55, [43060000]], ["Gargoyle's Black Axe", 19, [15140000]], ["Gargoyle's Black Blades", 14, [10090000]], ["Gargoyle's Black Halberd", 29, [18160000]], ["Gargoyle's Blackblade", 5, [3210000]], ["Gargoyle's Great Axe", 19, [15130000]], ["Gargoyle's Greatsword", 5, [3190000]], ["Gargoyle's Halberd", 29, [18150000]], ["Gargoyle's Twinblade", 14, [10080000]], ["Gazing Finger", 41, [23520000]], ["Gelmir Glintstone Staff", 57, [33050000]], ["Ghiza's Wheel", 41, [23100000]], ["Ghostflame Torch", 87, [24050000]], ["Giant's Red Braid", 39, [20060000]], ["Giant's Seal", 61, [34020000]], ["Giant-Crusher", 41, [23110000]], ["Gilded Greatshield", 69, [32270000]], ["Gilded Iron Shield", 65, [30130000]], ["Glaive", 29, [18090000]], ["Glintstone Kris", 1, [1070000]], ["Glintstone Staff", 57, [33000000]], ["Godskin Peeler", 14, [10010000]], ["Godskin Stitcher", 16, [6010000]], ["Godslayer's Greatsword", 7, [4070000]], ["Godslayer's Seal", 61, [34010000]], ["Golden Arrow", 81, [50090000]], ["Golden Beast Crest Shield", 69, [32090000]], ["Golden Bolt", 85, [52070000]], ["Golden Epitaph", 3, [2070000]], ["Golden Great Arrow", 83, [51020000]], ["Golden Greatshield", 69, [32260000]], ["Golden Halberd", 29, [18080000]], ["Golden Lion Shield", 67, [31530000]], ["Golden Order Flail", 24, [13510000]], ["Golden Order Greatsword", 5, [3170000]], ["Golden Order Seal", 61, [34060000]], ["Golem Fist", 35, [21540000]], ["Golem Greatbow", 53, [42010000]], ["Golem's Great Arrow", 83, [51010000]], ["Golem's Halberd", 41, [23120000]], ["Golem's Magic Arrow", 83, [51030000]], ["Grafted Blade Greatsword", 7, [4100000]], ["Grafted Dragon", 35, [21060000]], ["Grave Scythe", 31, [19010000]], ["Gravel Stone Seal", 61, [34030000]], ["Great Arrow", 83, [51000000]], ["Great Club", 41, [23020000]], ["Great Katana", 94, [66500000]], ["Great Knife", 1, [1090000]], ["Great Mace", 23, [12060000]], ["Great Omenkiller Cleaver", 19, [15020000]], ["Great Stars", 23, [12180000]], ["Great Turtle Shell", 67, [31140000]], ["Great Épée", 16, [6020000]], ["Greataxe", 19, [15000000]], ["Greatbow", 53, [42040000]], ["Greathorn Hammer", 23, [12010000]], ["Greatsword", 7, [4000000]], ["Greatsword of Damnation", 5, [3510000]], ["Greatsword of Radahn (Light)", 7, [4550000]], ["Greatsword of Radahn (Lord)", 7, [4530000]], ["Greatsword of Solitude", 5, [3550000]], ["Grossmesser", 9, [7150000]], ["Guardian's Swordspear", 29, [18110000]], ["Halberd", 29, [18000000]], ["Haligbone Arrow", 81, [50290000]], ["Haligbone Arrow (Fletched)", 81, [50280000]], ["Haligbone Bolt", 85, [52130000]], ["Haligtree Crest Greatshield", 69, [32280000]], ["Halo Scythe", 31, [19020000]], ["Hammer", 21, [11080000]], ["Hand Axe", 17, [14020000]], ["Hand Ballista", 56, [44000000]], ["Hand of Malenia", 13, [9020000]], ["Harp Bow", 50, [40030000]], ["Hawk Crest Wooden Shield", 67, [31270000]], ["Heater Shield", 67, [31330000]], ["Heavy Crossbow", 55, [43030000]], ["Hefty Scimitar", 11, [8530000]], ["Helphen's Steeple", 5, [3130000]], ["Highland Axe", 17, [14100000]], ["Hookclaws", 37, [22000000]], ["Horn Bow", 51, [41020000]], ["Horned Warrior's Greatsword", 11, [8520000]], ["Horned Warrior's Sword", 9, [7530000]], ["Horse Crest Wooden Shield", 67, [31240000]], ["Hoslow's Petal Whip", 39, [20050000]], ["Ice Crest Shield", 65, [30140000]], ["Icerind Hatchet", 17, [14080000]], ["Icon Shield", 69, [32140000]], ["Idus Sword", 93, [67530000]], ["Igon's Greatbow", 53, [42500000]], ["Igon's Harpoon", 83, [51500000]], ["Inquisitor's Girandole", 25, [16130000]], ["Inseparable Sword", 5, [2090000]], ["Inverted Hawk Heater Shield", 67, [31320000]], ["Inverted Hawk Towershield", 69, [32210000]], ["Iron Ball", 35, [21070000]], ["Iron Cleaver", 17, [14040000]], ["Iron Greatsword", 5, [3020000]], ["Iron Roundshield", 65, [30120000]], ["Iron Spear", 25, [16150000]], ["Ivory Sickle", 1, [1130000]], ["Jar Cannon", 56, [44010000]], ["Jawbone Axe", 17, [14030000]], ["Jellyfish Shield", 69, [32120000]], ["Katar", 35, [21100000]], ["Kite Shield", 67, [31000000]], ["Knight's Greatsword", 5, [3040000]], ["Lamenting Visage", 87, [24510000]], ["Lance", 28, [17060000]], ["Large Club", 23, [12000000]], ["Large Leather Shield", 67, [31230000]], ["Lazuli Glintstone Sword", 3, [2250000]], ["Leda's Sword", 93, [67510000]], ["Leontiel's Greatsword", 5, [3560000]], ["Light Crossbow", 55, [43020000]], ["Lightning Bolt", 85, [52010000]], ["Lightning Greatbolt", 86, [53010000]], ["Lightning Perfume Bottle", 89, [61530000]], ["Lightningbone Arrow", 81, [50210000]], ["Lightningbone Arrow (Fletched)", 81, [50200000]], ["Lightningbone Bolt", 85, [52110000]], ["Lion Greatbow", 53, [42000000]], ["Lizard Greatsword", 5, [3520000]], ["Longbow", 51, [41000000]], ["Longhaft Axe", 19, [15050000]], ["Longsword", 3, [2000000]], ["Lordsworn's Bolt", 85, [52080000]], ["Lordsworn's Greatsword", 5, [3030000]], ["Lordsworn's Shield", 69, [32300000]], ["Lordsworn's Straight Sword", 3, [2040000]], ["Loretta's War Sickle", 29, [18100000]], ["Lucerne", 29, [18020000]], ["Lusat's Glintstone Staff", 57, [33240000]], ["Mace", 21, [11000000]], ["Madding Hand", 35, [21530000]], ["Magicbone Arrow", 81, [50270000]], ["Magicbone Arrow (Fletched)", 81, [50260000]], ["Magicbone Bolt", 85, [52120000]], ["Magma Blade", 9, [7050000]], ["Magma Whip Candlestick", 39, [20030000]], ["Magma Wyrm's Scalesword", 11, [8040000]], ["Main-gauche", 1, [1500000]], ["Maliketh's Black Blade", 7, [4020000]], ["Man-Serpent's Shield", 65, [30020000]], ["Manor Towershield", 69, [32190000]], ["Mantis Blade", 9, [7120000]], ["Marais Executioner's Sword", 5, [3150000]], ["Marika's Hammer", 21, [11150000]], ["Marred Leather Shield", 67, [31010000]], ["Marred Wooden Shield", 67, [31020000]], ["Maternal Staff", 57, [33520000]], ["Messmer Soldier Shield", 67, [31500000]], ["Messmer Soldier's Axe", 17, [14520000]], ["Messmer Soldier's Spear", 28, [17510000]], ["Meteor Bolt", 85, [52050000]], ["Meteoric Ore Blade", 13, [9030000]], ["Meteorite Staff", 57, [33250000]], ["Milady", 93, [67500000]], ["Miquellan Knight's Sword", 3, [2200000]], ["Misbegotten Shortbow", 50, [40010000]], ["Miséricorde", 1, [1030000]], ["Mohgwyn's Sacred Spear", 28, [17010000]], ["Monk's Flameblade", 11, [8070000]], ["Monk's Flamemace", 21, [11090000]], ["Moonrithyll's Knight Sword", 7, [4540000]], ["Moonveil", 13, [9060000]], ["Morgott's Cursed Sword", 11, [8100000]], ["Morning Star", 21, [11050000]], ["Nagakiba", 13, [9010000]], ["Nanaya's Torch", 87, [24500000]], ["Nightrider Flail", 24, [13000000]], ["Nightrider Glaive", 29, [18050000]], ["Noble's Estoc", 15, [5060000]], ["Noble's Slender Sword", 3, [2230000]], ["Nox Flowing Hammer", 21, [11120000]], ["Nox Flowing Sword", 9, [2080000]], ["Obsidian Lamina", 31, [19500000]], ["Omen Cleaver", 11, [8060000]], ["One-Eyed Shield", 69, [32150000]], ["Onyx Lord's Greatsword", 11, [8010000]], ["Ordovis's Greatsword", 5, [3060000]], ["Ornamental Straight Sword", 3, [2060000]], ["Parrying Dagger", 1, [1020000]], ["Partisan", 25, [16050000]], ["Pata", 35, [21510000]], ["Perfumer's Bolt", 85, [52020000]], ["Perfumer's Shield", 65, [30010000]], ["Pest's Glaive", 29, [18010000]], ["Pickaxe", 23, [12140000]], ["Pike", 25, [16070000]], ["Pillory Shield", 65, [30040000]], ["Piquebone Arrow", 81, [50550000]], ["Piquebone Arrow (Fletched)", 81, [50540000]], ["Piquebone Bolt", 85, [52520000]], ["Poisonbone Arrow", 81, [50160000]], ["Poisonbone Arrow (Fletched)", 81, [50150000]], ["Poisonbone Bolt", 85, [52140000]], ["Poisoned Hand", 35, [21520000]], ["Poleblade of the Bud", 29, [18510000]], ["Prelate's Inferno Crozier", 41, [23000000]], ["Prince of Death's Staff", 57, [33180000]], ["Pulley Bow", 51, [41060000]], ["Pulley Crossbow", 55, [43050000]], ["Putrescence Cleaver", 19, [8500000]], ["Queelign's Greatsword", 16, [6500000]], ["Rabbath's Cannon", 56, [44500000]], ["Rabbath's Greatbolt", 86, [53500000]], ["Radahn's Spear", 83, [51040000]], ["Rainbow Stone Arrow", 81, [50220000]], ["Rainbow Stone Arrow (Fletched)", 81, [50080000]], ["Rakshasa's Great Katana", 94, [66520000]], ["Rapier", 15, [5020000]], ["Raptor Talons", 37, [22030000]], ["Red Bear's Claw", 95, [68510000]], ["Red Branch Shortbow", 50, [40020000]], ["Red Crest Heater Shield", 67, [31290000]], ["Red Thorn Roundshield", 65, [30070000]], ["Redmane Greatshield", 69, [32230000]], ["Reduvia", 1, [1040000]], ["Reed Great Katana", 94, [66530000]], ["Regalia of Eochaid", 3, [2220000]], ["Rellana's Twin Blades", 93, [67520000]], ["Repeating Crossbow", 55, [43500000]], ["Reverse-Bladed Sword", 92, [64530000]], ["Rickety Shield", 65, [30030000]], ["Rift Shield", 65, [30110000]], ["Ringed Finger", 21, [11130000]], ["Ripple Blade", 17, [14050000]], ["Ripple Crescent Halberd", 29, [18060000]], ["Ritual Thrusting Shield", 90, [62520000]], ["Rivers of Blood", 13, [9040000]], ["Riveted Wooden Shield", 65, [30090000]], ["Rogier's Rapier", 15, [5030000]], ["Rosus' Axe", 17, [14120000]], ["Rotbone Arrow", 81, [50350000]], ["Rotbone Arrow (Fletched)", 81, [50340000]], ["Rotbone Bolt", 85, [52170000]], ["Rotten Battle Hammer", 23, [12210000]], ["Rotten Crystal Spear", 25, [16160000]], ["Rotten Crystal Staff", 57, [33270000]], ["Rotten Crystal Sword", 3, [2260000]], ["Rotten Greataxe", 41, [23150000]], ["Rotten Staff", 41, [23140000]], ["Round Shield", 67, [31070000]], ["Royal Greatsword", 7, [4060000]], ["Ruins Greatsword", 7, [4080000]], ["Rusted Anchor", 19, [15060000]], ["Sacred Relic Sword", 5, [3100000]], ["Sacrificial Axe", 17, [14110000]], ["Scavenger's Curved Sword", 9, [7080000]], ["Scepter of the All-Knowing", 21, [11110000]], ["Scimitar", 9, [7140000]], ["Scorpion Kite Shield", 67, [31080000]], ["Scorpion's Stinger", 1, [1080000]], ["Scripture Wooden Shield", 65, [30080000]], ["Scythe", 31, [19000000]], ["Sentry's Torch", 87, [24070000]], ["Serpent Arrow", 81, [50020000]], ["Serpent Bow", 51, [41040000]], ["Serpent Crest Shield", 67, [31520000]], ["Serpent Flail", 24, [13500000]], ["Serpent-God's Curved Sword", 9, [7110000]], ["Serpent-Hunter", 28, [17030000]], ["Serpentbone Blade", 13, [9080000]], ["Shadow Sunflower Blossom", 41, [23510000]], ["Shamshir", 9, [7030000]], ["Shattershard Arrow", 81, [50230000]], ["Shattershard Arrow (Fletched)", 81, [50060000]], ["Shield of Night", 65, [21550000]], ["Shield of the Guilty", 65, [31170000]], ["Short Spear", 25, [16000000]], ["Short Sword", 3, [2010000]], ["Shortbow", 50, [40000000]], ["Shotel", 9, [7020000]], ["Siluria's Tree", 28, [17020000]], ["Silver Grooved Shield", 67, [31540000]], ["Silver Mirrorshield", 67, [31060000]], ["Sleepbone Arrow", 81, [50180000]], ["Sleepbone Arrow (Fletched)", 81, [50170000]], ["Sleepbone Bolt", 85, [52180000]], ["Smithscript Axe", 17, [14500000]], ["Smithscript Cirque", 92, [64510000]], ["Smithscript Dagger", 91, [63500000]], ["Smithscript Greathammer", 23, [12500000]], ["Smithscript Shield", 65, [30510000]], ["Smithscript Spear", 25, [16500000]], ["Smoldering Shield", 65, [30150000]], ["Soldier's Crossbow", 55, [43000000]], ["Spear", 25, [16010000]], ["Spear of the Impaler", 28, [17500000]], ["Spiked Caestus", 35, [21010000]], ["Spiked Club", 21, [11070000]], ["Spiked Palisade Shield", 69, [32170000]], ["Spiked Spear", 25, [16140000]], ["Spiralhorn Shield", 65, [30190000]], ["Spiraltree Seal", 61, [34520000]], ["Spirit Glaive", 29, [18500000]], ["Spirit Sword", 9, [7500000]], ["Spiritflame Arrow", 81, [50240000]], ["Spread Crossbow", 55, [43510000]], ["St. Trina's Arrow", 81, [50040000]], ["St. Trina's Torch", 87, [24040000]], ["Staff of Loss", 57, [33280000]], ["Staff of the Avatar", 41, [23070000]], ["Staff of the Great Beyond", 57, [33510000]], ["Staff of the Guilty", 57, [33260000]], ["Star Fist", 35, [21080000]], ["Star-Lined Sword", 13, [2520000]], ["Starscourge Greatsword", 7, [4050000]], ["Steel-Wire Torch", 87, [24020000]], ["Stone Club", 21, [11140000]], ["Stone-Sheathed Sword", 3, [2540000]], ["Stormhawk Axe", 17, [14140000]], ["Stormwing Bone Arrow", 81, [50190000]], ["Sun Realm Shield", 67, [31050000]], ["Swift Spear", 25, [16520000]], ["Sword Lance", 16, [3500000]], ["Sword of Darkness", 3, [2560000]], ["Sword of Light", 3, [2550000]], ["Sword of Milos", 5, [3160000]], ["Sword of Night", 13, [9500000]], ["Sword of Night and Flame", 3, [2140000]], ["Sword of St. Trina", 3, [2190000]], ["Thiollier's Hidden Needle", 35, [21500000]], ["Thorned Whip", 39, [20020000]], ["Tooth Whip", 39, [20500000]], ["Torch", 87, [24000000]], ["Torchpole", 25, [16080000]], ["Treespear", 28, [17070000]], ["Troll Knight's Sword", 7, [4110000]], ["Troll's Golden Sword", 7, [4030000]], ["Troll's Hammer", 41, [23130000]], ["Twinbird Kite Shield", 67, [31090000]], ["Twinblade", 14, [10000000]], ["Twinned Knight Swords", 14, [10030000]], ["Uchigatana", 13, [9000000]], ["Unarmed", 33, [110000]], ["Urumi", 39, [20070000]], ["Varré's Bouquet", 21, [11060000]], ["Velvet Sword of St. Trina", 3, [2510000]], ["Venomous Fang", 37, [22010000]], ["Verdigris Greatshield", 69, [32520000]], ["Veteran's Prosthesis", 35, [21120000]], ["Visage Shield", 69, [32160000]], ["Vulgar Militia Saw", 29, [18070000]], ["Vulgar Militia Shotel", 29, [18130000]], ["Vyke's War Spear", 28, [17050000]], ["Wakizashi", 1, [1100000]], ["Warhawk's Talon", 3, [2240000]], ["Warped Axe", 17, [15010000]], ["Warpick", 21, [11040000]], ["Watchdog's Greatsword", 7, [4010000]], ["Watchdog's Staff", 41, [23010000]], ["Weathered Straight Sword", 3, [2050000]], ["Whip", 39, [20000000]], ["Wing of Astel", 9, [7070000]], ["Winged Greathorn", 19, [15110000]], ["Winged Scythe", 31, [19060000]], ["Wolf Crest Shield", 67, [31510000]], ["Wooden Greatshield", 69, [32290000]], ["Zamor Curved Sword", 11, [8050000]], ["Zweihander", 7, [4040000]]]};
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

/// Every rule `p` breaks this frame except the per-round flask count. `cfg` supplies the numbers
/// three rules are written with; which rules apply at all is the referee's business.
function check(p, facts, cfg = DEFAULT_CONFIG) {
    const out = [];
    // rules.rs violation / equipped / event: something done, what is equipped (judged only once an
    // opponent shares the world), and one discrete act that is never merged with another.
    const v = (rule, detail) => out.push({ rule, detail, onEquipment: false, event: false });
    const eq = (rule, detail) => out.push({ rule, detail, onEquipment: true, event: false });
    const ev = (rule, detail) => out.push({ rule, detail, onEquipment: false, event: true });
    const W = T.weapon, C = T.wepType, S = T.skill, TAL = T.talisman;
    if (p.level !== 0 && p.level !== cfg.runeLevel) eq('RuneLevel', `Rune Level ${p.level}`);

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
        if (shieldClasses.includes(w.wepType) && w.wepType !== C.THRUSTING_SHIELD && w.physGuardCut > cfg.shieldGuardCap) {
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

// ============================================================================================
// Your own rules. er-duel-rules.json beside eldenring.exe switches any rule above off, changes the
// numbers three of them are written with, and adds bans of your own. The Cheat Engine table's
// Rules window, scripts/er-duel-rules-bans.py and the helpers at the end of this file all write
// it, and the referee reads it again whenever it changes. With no file, the ruleset applies as
// written.
//
// A ban of your own breaks when every one of its conditions holds for one player at once, and a
// condition holds when any one of the things it lists does: "Rivers of Blood two-handed" and
// "Lord of Blood's Exultation" in one ban means both at once; two weapons in one condition means
// either. Bans are judged the way the ruleset judges its own: a ban on what someone wears waits
// for an opponent to share the world, a ban on something done (a spell, an item, an effect)
// fires whenever it is seen, and a ban on a skill or an animation fires on every use.
// ============================================================================================
const RULES_FILE_NAME = 'er-duel-rules.json';
/// Frames between looks at the rules file: about two seconds at 60 fps.
const RULES_POLL_TICKS = 120;
const DEFAULT_CONFIG = Object.freeze({
    off: [], runeLevel: RUNE_LEVEL, blueFlasks: CERULEAN_FLASK_LIMIT, shieldGuardCap: SHIELD_PHYS_GUARD_CAP, bans: [],
});
/// The three numbers a rules file may change, with the rule each one is written into.
const SETTINGS = { runeLevel: 'RuneLevel', blueFlasks: 'CeruleanFlasks', shieldGuardCap: 'ShieldGuard' };
const CONDITION_KINDS = {
    weapon: 'Weapon', armor: 'Armor', talisman: 'Talisman', tear: 'Crystal tear', skill: 'Skill',
    spell: 'Spell', item: 'Item used', effect: 'Effect', bullet: 'Projectile', anim: 'Animation',
    level: 'Rune level', stat: 'Attribute',
};
/// Where a weapon condition looks: any of the six slots, a hand, a grip, or the weapon a skill
/// plays from (the two-handed one, else the right).
const WEAPON_WHERE = {
    equipped: '', held: ' in hand', right: ' in the right hand', left: ' in the left hand',
    twoHanded: ' two-handed', oneHanded: ' one-handed', powerstanced: ' powerstanced', skill: ' (skill weapon)',
};
const COMPARE = {
    '=': (a, b) => a === b, '!=': (a, b) => a !== b, '<': (a, b) => a < b,
    '<=': (a, b) => a <= b, '>': (a, b) => a > b, '>=': (a, b) => a >= b,
};
/// Kinds that are one act each (every use its own break) and kinds that are something done.
const EVENT_KINDS = ['skill', 'anim'];
const ACT_KINDS = ['spell', 'item', 'effect', 'bullet'];

/// How a ban is judged: 'event' on every use, 'act' whenever seen, 'equipment' once in a match.
/// A weapon carrying a buff was buffed, which is something done, as the ruleset's own Buff
/// Restrictions are.
function banJudged(ban) {
    if (ban.all.some((c) => EVENT_KINDS.includes(c.kind))) return 'event';
    if (ban.all.some((c) => ACT_KINDS.includes(c.kind) || (c.kind === 'weapon' && c.buff))) return 'act';
    return 'equipment';
}

/// The rule as written with the numbers `cfg` gives it.
function ruleText(rule, cfg = DEFAULT_CONFIG) {
    if (rule === 'RuneLevel') return `Rune Level: ${cfg.runeLevel}`;
    if (rule === 'CeruleanFlasks') return `Blue flasks limited to ${cfg.blueFlasks}`;
    if (rule === 'ShieldGuard') return `Blocking With Shields above ${cfg.shieldGuardCap}% physical absorption`;
    return RULE_TEXT[rule];
}

const entryWith = (entries, id) => entries.find((e) => e.ids.includes(id)) || null;
const heldHands = (p) => (twoHanded(p) ? [twoHanded(p)] : ['right', 'left']);
const buffCategory = (hand) => (hand === 'left' ? CATEGORY_LEFT_WEAPON_BUFF : CATEGORY_RIGHT_WEAPON_BUFF);
function weaponBuffed(p, facts, hand) {
    return p.speffects.some((id) => { const e = facts.effect(id); return e !== null && e.category === buffCategory(hand); });
}

/// The weapons a weapon condition looks at, each with the hand it is held in (null when it is not).
function weaponCandidates(p, facts, where) {
    const held = heldHands(p);
    const inHand = (h) => [active(p, h), h];
    switch (where) {
        case 'held': return held.map(inHand);
        case 'right': case 'left': return held.includes(where) ? [inHand(where)] : [];
        case 'twoHanded': return twoHanded(p) ? [inHand(twoHanded(p))] : [];
        case 'oneHanded': return twoHanded(p) ? [] : held.map(inHand);
        case 'powerstanced': {
            if (twoHanded(p)) return [];
            const right = active(p, 'right'), left = active(p, 'left');
            const rc = classOf(facts, right);
            return rc !== null && rc === classOf(facts, left) ? [[right, 'right'], [left, 'left']] : [];
        }
        case 'skill': return [inHand(twoHanded(p) || 'right')];
        default: {
            const out = [];
            for (const hand of ['right', 'left']) {
                const list = hand === 'left' ? p.weaponsLeft : p.weaponsRight;
                const slot = hand === 'left' ? p.leftSlot : p.rightSlot;
                list.forEach((id, i) => out.push([id, i === slot && held.includes(hand) ? hand : null]));
            }
            return out;
        }
    }
}

function matchWeapon(p, facts, c) {
    for (const [id, hand] of weaponCandidates(p, facts, c.where)) {
        if (id < 0) continue;
        const item = entryWith(c.items, weaponBase(id));
        if (c.items.length && !item) continue;
        const klass = entryWith(c.classes, classOf(facts, id));
        if (c.classes.length && !klass) continue;
        const affinity = entryWith(c.affinities, weaponAffinity(id));
        if (c.affinities.length && !affinity) continue;
        if (c.buff && !(hand && weaponBuffed(p, facts, hand))) continue;
        const what = item ? item.name : klass ? klass.name : 'a weapon';
        return `${affinity ? affinity.name + ' ' : ''}${what}${WEAPON_WHERE[c.where]}${c.buff ? ' with a weapon buff' : ''}`;
    }
    return null;
}

/// What makes condition `c` hold for `p` this frame, in words, or null when it does not.
function matchCondition(p, facts, c) {
    const first = (ids) => { for (const id of ids) { const e = id >= 0 ? entryWith(c.items, id) : null; if (e) return e.name; } return null; };
    switch (c.kind) {
        case 'weapon': return matchWeapon(p, facts, c);
        case 'armor': return first(p.armor);
        case 'talisman': return first(p.talismans);
        case 'tear': return first(p.physick);
        case 'effect': return first(p.speffects);
        case 'bullet': return first(p.bullets);
        case 'anim': return first(p.newAnims);
        case 'skill': return first(p.newAnims.map((a) => { const sc = skillClip(a); return sc === null ? -1 : sc[0]; }));
        case 'spell': case 'item': {
            const e = c.items.find((x) => x.bullets.some((b) => p.bullets.includes(b)) || x.effects.some((s) => p.speffects.includes(s)));
            return e ? e.name : null;
        }
        case 'level': return p.level !== 0 && COMPARE[c.op](p.level, c.value) ? `Rune Level ${p.level}` : null;
        case 'stat': {
            const value = p.attributes[c.stat];
            return p.level !== 0 && COMPARE[c.op](value, c.value) ? `${ATTRIBUTE_NAMES[c.stat]} ${value}` : null;
        }
        default: return null;
    }
}

/// Every ban of your own `p` breaks this frame.
function customViolations(p, facts, bans) {
    const out = [];
    for (const ban of bans) {
        if (!ban.on || ban.all.length === 0) continue;
        const parts = [];
        for (const c of ban.all) {
            const why = matchCondition(p, facts, c);
            if (why === null) break;
            parts.push(why);
        }
        if (parts.length !== ban.all.length) continue;
        const judged = banJudged(ban);
        out.push({ rule: 'Custom', text: ban.name, detail: parts.join(', '), onEquipment: judged === 'equipment', event: judged === 'event' });
    }
    return out;
}

const integers = (v) => (Array.isArray(v) ? v.filter((x) => Number.isInteger(x)) : []);
function normalEntries(list) {
    if (!Array.isArray(list)) return [];
    return list.filter((e) => e && typeof e.name === 'string')
        .map((e) => ({ name: e.name, ids: integers(e.ids), bullets: integers(e.bullets), effects: integers(e.effects) }));
}

function normalCondition(c, problems, where) {
    if (!c || typeof c !== 'object' || !Object.prototype.hasOwnProperty.call(CONDITION_KINDS, c.kind)) {
        problems.push(`${where}: no condition kind ${JSON.stringify(c && c.kind)}`);
        return null;
    }
    if (c.kind === 'level' || c.kind === 'stat') {
        if (!COMPARE[c.op] || typeof c.value !== 'number') {
            problems.push(`${where}: a ${c.kind} condition needs op (one of ${Object.keys(COMPARE).join(' ')}) and a number value`);
            return null;
        }
        if (c.kind === 'level') return { kind: 'level', op: c.op, value: c.value };
        if (!(Number.isInteger(c.stat) && c.stat >= 0 && c.stat < ATTRIBUTE_NAMES.length)) {
            problems.push(`${where}: stat must be 0 (Vigor) to 7 (Arcane)`);
            return null;
        }
        return { kind: 'stat', stat: c.stat, op: c.op, value: c.value };
    }
    const out = { kind: c.kind, items: normalEntries(c.items) };
    if (c.kind === 'weapon') {
        out.classes = normalEntries(c.classes);
        out.affinities = normalEntries(c.affinities);
        out.where = Object.prototype.hasOwnProperty.call(WEAPON_WHERE, c.where) ? c.where : 'equipped';
        out.buff = c.buff === true;
    } else if (out.items.length === 0) {
        problems.push(`${where}: a ${c.kind} condition names nothing`);
        return null;
    }
    return out;
}

/// The rules a file's text asks for, and what in it could not be used. A ban with any condition
/// that cannot be read is left out whole, because the rest of it alone would ban more than asked.
function readConfig(text) {
    let raw;
    try { raw = JSON.parse(text); } catch (e) {
        return { config: DEFAULT_CONFIG, problems: [`${RULES_FILE_NAME} is not JSON (${e.message}); the ruleset applies as written`] };
    }
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
        return { config: DEFAULT_CONFIG, problems: [`${RULES_FILE_NAME} holds no object; the ruleset applies as written`] };
    }
    const problems = [];
    const setting = (key) => {
        const dflt = DEFAULT_CONFIG[key];
        if (raw[key] === undefined) return dflt;
        if (typeof raw[key] === 'number' && raw[key] >= 0) return raw[key];
        problems.push(`${key} must be a number of 0 or more; ${dflt} is used`);
        return dflt;
    };
    const off = [];
    for (const r of Array.isArray(raw.off) ? raw.off : []) {
        if (Object.prototype.hasOwnProperty.call(RULE_TEXT, r)) off.push(r); else problems.push(`off: no rule ${JSON.stringify(r)}`);
    }
    const bans = [];
    (Array.isArray(raw.bans) ? raw.bans : []).forEach((b, i) => {
        if (!b || typeof b.name !== 'string' || b.name.trim() === '') { problems.push(`ban ${i + 1} has no name and is left out`); return; }
        const name = b.name.trim();
        const all = (Array.isArray(b.all) ? b.all : []).map((c, k) => normalCondition(c, problems, `${name}, condition ${k + 1}`));
        if (all.length === 0 || all.includes(null)) { problems.push(`${name}: left out`); return; }
        bans.push({ name, on: b.on !== false, all });
    });
    const config = { off, bans };
    for (const key of Object.keys(SETTINGS)) config[key] = setting(key);
    return { config, problems };
}

const writeConfig = (cfg) => JSON.stringify({
    off: cfg.off, runeLevel: cfg.runeLevel, blueFlasks: cfg.blueFlasks, shieldGuardCap: cfg.shieldGuardCap, bans: cfg.bans,
}, null, 2) + '\n';

/// One line on what the rules are: the switched-off rules, changed numbers and bans in force.
function describeConfig(cfg) {
    const changed = Object.keys(SETTINGS).filter((k) => cfg[k] !== DEFAULT_CONFIG[k]).map((k) => `${k}=${cfg[k]}`);
    const on = cfg.bans.filter((b) => b.on).length;
    return `${Object.keys(RULE_TEXT).length - cfg.off.length} of ${Object.keys(RULE_TEXT).length} built-in rules on` +
        (cfg.off.length ? ` (off: ${cfg.off.join(', ')})` : '') + (changed.length ? `, ${changed.join(', ')}` : '') +
        `, ${on} of ${cfg.bans.length} bans of your own on`;
}

// -- the catalog, and the edits the helpers make -----------------------------------------------
/// Which catalog list each kind of pick comes from.
const CATALOG_LISTS = {
    weapon: 'weapons', class: 'classes', affinity: 'affinities', armor: 'armor', talisman: 'talismans',
    tear: 'tears', skill: 'skills', spell: 'spells', item: 'items',
};
/// A catalog row as a condition entry.
function catalogEntry(list, row) {
    if (list === 'weapons') return { name: row[0], ids: row[2], bullets: [], effects: [] };
    if (list === 'classes' || list === 'affinities') return { name: row[0], ids: [row[1]], bullets: [], effects: [] };
    if (list === 'spells' || list === 'items') return { name: row[0], ids: [], bullets: row[1], effects: row[2] };
    return { name: row[0], ids: row[1], bullets: [], effects: [] };
}
/// Every catalog name containing `text`, case aside, as `{ pick, name }`, at most `limit` of them.
function searchCatalog(catalog, text, limit = 60) {
    const needle = String(text).toLowerCase();
    const out = [];
    for (const [pick, list] of Object.entries(CATALOG_LISTS)) {
        for (const row of catalog[list] || []) {
            if (row[0].toLowerCase().includes(needle)) out.push({ pick, name: row[0] });
            if (out.length >= limit) return out;
        }
    }
    return out;
}
/// The entries for `names` (one or a list) in one catalog list; an unknown name is an error that
/// says how to find the right one.
function pickEntries(catalog, pick, names) {
    const list = CATALOG_LISTS[pick];
    return [].concat(names).map((n) => {
        const row = (catalog[list] || []).find((r) => r[0].toLowerCase() === String(n).toLowerCase());
        if (!row) throw new Error(`no ${pick} is called ${JSON.stringify(n)}; findItem(${JSON.stringify(String(n))}) lists the names`);
        return catalogEntry(list, row);
    });
}
/// Conditions by name: weapon(names, { where, class, affinity, buff }), armor(names), ...,
/// effect(ids), level(op, value), stat(attribute name, op, value).
function conditionBuilders(catalog) {
    const named = (kind) => (names) => ({ kind, items: pickEntries(catalog, kind, names) });
    const raw = (kind) => (ids) => ({ kind, items: [].concat(ids).map((id) => ({ name: `${kind} ${id}`, ids: [id], bullets: [], effects: [] })) });
    return {
        weapon(names, opts = {}) {
            if (opts.where !== undefined && !Object.prototype.hasOwnProperty.call(WEAPON_WHERE, opts.where)) {
                throw new Error(`where is one of ${Object.keys(WEAPON_WHERE).join(', ')}`);
            }
            return {
                kind: 'weapon',
                items: names === undefined || names === null ? [] : pickEntries(catalog, 'weapon', names),
                classes: opts.class ? pickEntries(catalog, 'class', opts.class) : [],
                affinities: opts.affinity ? pickEntries(catalog, 'affinity', opts.affinity) : [],
                where: opts.where || 'equipped',
                buff: opts.buff === true,
            };
        },
        armor: named('armor'), talisman: named('talisman'), tear: named('tear'), skill: named('skill'),
        spell: named('spell'), item: named('item'), effect: raw('effect'), bullet: raw('bullet'), anim: raw('anim'),
        level(op, value) { return { kind: 'level', op, value }; },
        stat(attribute, op, value) {
            const stat = ATTRIBUTE_NAMES.findIndex((n) => n.toLowerCase() === String(attribute).toLowerCase());
            if (stat < 0) throw new Error(`the attributes are ${ATTRIBUTE_NAMES.join(', ')}`);
            return { kind: 'stat', stat, op, value };
        },
    };
}
/// The rule key a key or the wording of a rule names, case aside.
function ruleKey(name) {
    const want = String(name).toLowerCase();
    return Object.keys(RULE_TEXT).find((k) => k.toLowerCase() === want || RULE_TEXT[k].toLowerCase() === want) || null;
}
/// `cfg` with one change, each checked by `readConfig` the way a file would be.
const configWith = {
    ban(cfg, name, all) {
        const bans = cfg.bans.filter((b) => b.name !== name).concat([{ name, on: true, all }]);
        return { ...cfg, bans };
    },
    unban(cfg, name) { return { ...cfg, bans: cfg.bans.filter((b) => b.name !== name) }; },
    banOn(cfg, name, on) { return { ...cfg, bans: cfg.bans.map((b) => (b.name === name ? { ...b, on } : b)) }; },
    rule(cfg, key, on) {
        const off = cfg.off.filter((k) => k !== key);
        return { ...cfg, off: on ? off : off.concat([key]) };
    },
    setting(cfg, key, value) { return { ...cfg, [key]: value }; },
};

const slotChanges = (name, old, now) => old.flatMap((o, i) => (o === now[i] ? [] : [`${name} ${i + 1}: ${o} -> ${now[i]}`]));
const buildOf = (p) => ({ level: p.level, attributes: p.attributes.slice(), weaponsLeft: p.weaponsLeft.slice(), weaponsRight: p.weaponsRight.slice(),
    armor: p.armor.slice(), talismans: p.talismans.slice(), physick: p.physick.slice() });

/// Which breaks become a notice, the blue flask count per round, and a build that changes once two
/// players share a world (referee.rs).
class Referee {
    constructor(config = DEFAULT_CONFIG) {
        this.lastSeen = new Map(); this.flasks = new Map(); this.alive = new Map(); this.entered = new Map(); this.hands = {}; this.aliveSince = new Map();
        this.config = config;
    }

    /// The rules from now on; what was already counted (flasks, a build entered with) stays.
    setConfig(config) { this.config = config; }

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
        const cfg = this.config;
        const alerts = [];
        for (const p of players) {
            const violations = check(p, facts, cfg);
            const drinking = p.speffects.some((id) => T.ceruleanFlask.includes(id));
            const f = this.flasks.get(p.key) || { drinking: false, used: 0 };
            if (drinking && !f.drinking) f.used += 1;
            f.drinking = drinking;
            this.flasks.set(p.key, f);
            if (f.used > cfg.blueFlasks) violations.push({ rule: 'CeruleanFlasks', detail: `Flask of Cerulean Tears number ${f.used} this round` });
            if (inMatch) violations.push(...this.buildChanges(p));
            const since = this.aliveSince.get(p.key);
            if (inMatch && betterMultiplayer && p.hp > 0 && !runeArcActive(p) && since !== undefined && nowMs - since >= RUNE_ARC_GRACE_MS) {
                violations.push({ rule: 'RuneArc', detail: `no active Rune Arc ${RUNE_ARC_GRACE_MS / 1000} s into the round` });
            }
            if (p.isLocal) violations.push(...this.carriedBuffs(nowMs, p, facts));
            violations.push(...customViolations(p, facts, cfg.bans));
            for (const vio of violations) {
                if (vio.rule !== 'Custom' && cfg.off.includes(vio.rule)) continue;
                if (vio.onEquipment && !inMatch) continue;
                const text = vio.text || ruleText(vio.rule, cfg);
                let fresh = true;
                if (!vio.event) {
                    const key = `${p.key}|${vio.rule}|${text}|${vio.detail}`;
                    const last = this.lastSeen.get(key);
                    this.lastSeen.set(key, nowMs);
                    fresh = last === undefined || nowMs - last >= REARM_MS;
                }
                if (fresh) alerts.push({ key: p.key, player: p.name, isLocal: p.isLocal, rule: vio.rule, text, detail: vio.detail });
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

/// The rule as the alert names it: a ban's own name, a rule written with the file's numbers, or
/// the ruleset's wording.
const alertText = (a) => a.text || RULE_TEXT[a.rule];

function headline(a, steamName) {
    return `Attention: Rule break: ${playerLabel(a.player, a.isLocal, steamName)}. ${alertText(a)}`;
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
    const line = `${stamp}  ${playerLabel(a.player, a.isLocal, steamName)}  ${alertText(a)}`;
    return a.detail ? `${line}  --  ${a.detail}` : line;
}

// ============================================================================================
// Reading the game. Everything below runs inside Frida only.
// ============================================================================================
if (typeof Process === 'undefined') {
    module.exports = {
        T, CATALOG, check, equipLoad, correctGraphAt, Referee, headline, breakLine, playerLabel, skillClip, animStarts,
        weaponBase, weaponAffinity, RULE_TEXT, DEFAULT_CONFIG, readConfig, writeConfig, describeConfig, ruleText,
        customViolations, banJudged, searchCatalog, pickEntries, conditionBuilders, configWith, ruleKey,
    };
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

    // -- your own rules ----------------------------------------------------------------------
    // Read at start and again every RULES_POLL_TICKS frames; a file that has not changed is not
    // read into the referee twice.
    const rulesPath = game.path.replace(/[^\\/]*$/, '') + RULES_FILE_NAME;
    let rulesText = null;
    function loadRules() {
        let text = '';
        try { text = File.readAllText(rulesPath); } catch (e) { /* no file: the ruleset as written */ }
        if (text === rulesText) return;
        rulesText = text;
        const { config, problems } = text === '' ? { config: DEFAULT_CONFIG, problems: [] } : readConfig(text);
        referee.setConfig(config);
        say(`rules: ${describeConfig(config)} (${text === '' ? `no ${rulesPath}; the ruleset as written` : rulesPath})`);
        for (const problem of problems) say(`rules: ${problem}`);
    }
    loadRules();
    // The helpers below are for the Frida REPL this agent runs in (`frida -n eldenring.exe -l
    // er-duel-rules.js`): each change is checked the way a file is, written to the rules file and
    // read back, so what the REPL sets and what the next run reads are the same thing.
    const saveRules = (cfg) => {
        const { config, problems } = readConfig(writeConfig(cfg));
        if (problems.length) throw new Error(problems.join('; '));
        File.writeAllText(rulesPath, writeConfig(config));
        loadRules();
        return describeConfig(config);
    };
    const knownRule = (name) => {
        const key = ruleKey(name);
        if (key === null) throw new Error(`no rule is called ${JSON.stringify(name)}; rules() lists them`);
        return key;
    };
    Object.assign(globalThis, conditionBuilders(CATALOG), {
        rules() {
            const cfg = referee.config;
            const built = Object.keys(RULE_TEXT).map((k) => `  [${cfg.off.includes(k) ? ' ' : 'x'}] ${k}: ${ruleText(k, cfg)}`);
            const own = cfg.bans.map((b) => `  [${b.on ? 'x' : ' '}] ${b.name} (${banJudged(b)}): ` +
                b.all.map((c) => `${CONDITION_KINDS[c.kind]} ${c.items ? c.items.map((e) => e.name).join(' or ') : `${c.op} ${c.value}`}`).join(' and '));
            return [describeConfig(cfg), 'Built-in rules:'].concat(built, ['Bans of your own:'], own.length ? own : ['  (none)']).join('\n');
        },
        findItem(text) { return searchCatalog(CATALOG, text).map((h) => `${h.pick}: ${h.name}`).join('\n') || 'nothing by that name'; },
        ban(name, ...all) { return saveRules(configWith.ban(referee.config, String(name), all)); },
        unban(name) { return saveRules(configWith.unban(referee.config, String(name))); },
        banOn(name, on = true) { return saveRules(configWith.banOn(referee.config, String(name), on !== false)); },
        ruleOn(name) { return saveRules(configWith.rule(referee.config, knownRule(name), true)); },
        ruleOff(name) { return saveRules(configWith.rule(referee.config, knownRule(name), false)); },
        setRule(key, value) {
            if (!Object.prototype.hasOwnProperty.call(SETTINGS, key)) throw new Error(`the numbers are ${Object.keys(SETTINGS).join(', ')}`);
            return saveRules(configWith.setting(referee.config, key, value));
        },
    });

    const loggedBuilds = new Map();
    const started = Date.now();
    let ticks = 0, alerts = 0, errors = 0;
    function tick() {
        ticks += 1;
        if (ticks % RULES_POLL_TICKS === 0) loadRules();
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
